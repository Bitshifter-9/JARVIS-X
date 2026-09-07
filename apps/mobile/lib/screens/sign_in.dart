import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../widgets/ambient.dart';
import '../widgets/orb.dart';

class SignInScreen extends ConsumerStatefulWidget {
  const SignInScreen({super.key});

  @override
  ConsumerState<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends ConsumerState<SignInScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  late final TextEditingController _baseUrl =
      TextEditingController(text: ref.read(baseUrlProvider));

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _baseUrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authProvider);

    return Scaffold(
      body: AmbientBackground(
        child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Center(child: JarvisOrb(state: OrbState.idle, size: 110))
                    .animate()
                    .scale(duration: 800.ms, curve: Curves.easeOutBack),
                const SizedBox(height: 18),
                Text('JARVIS X',
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.displaySmall)
                    .animate()
                    .fadeIn(delay: 150.ms),
                const SizedBox(height: 4),
                Text('Your operations, predicted and proven',
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.65)))
                    .animate()
                    .fadeIn(delay: 250.ms),
                const SizedBox(height: 32),
                TextField(
                  key: const Key('email'),
                  controller: _email,
                  decoration: const InputDecoration(labelText: 'Email'),
                  keyboardType: TextInputType.emailAddress,
                  autofillHints: const [AutofillHints.username],
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('password'),
                  controller: _password,
                  decoration: const InputDecoration(labelText: 'Password'),
                  obscureText: true,
                  autofillHints: const [AutofillHints.password],
                  onSubmitted: (_) => _submit(),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('baseUrl'),
                  controller: _baseUrl,
                  decoration: const InputDecoration(
                    labelText: 'Server',
                    helperText: 'Your deployed https address, e.g. https://name.duckdns.org',
                  ),
                ),
                if (auth.error != null) ...[
                  const SizedBox(height: 16),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Theme.of(context)
                          .colorScheme
                          .errorContainer
                          .withValues(alpha: 0.5),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(auth.error!, key: const Key('signInError')),
                  ),
                ],
                const SizedBox(height: 24),
                FilledButton(
                  key: const Key('signIn'),
                  onPressed: auth.loading ? null : _submit,
                  child: auth.loading
                      ? const SizedBox(
                          height: 18,
                          width: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('Sign in'),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  key: const Key('googleSignIn'),
                  icon: const Icon(Icons.account_circle_outlined, size: 18),
                  label: const Text('Sign in with Google'),
                  onPressed: auth.loading ? null : _googleSignIn,
                ),
              ],
            ),
          ),
        ),
      ),
      ),
    );
  }

  void _submit() {
    _applyBaseUrl();
    ref.read(authProvider.notifier).signIn(_email.text.trim(), _password.text);
  }

  void _applyBaseUrl() {
    final url = _baseUrl.text.trim();
    ref.read(baseUrlProvider.notifier).state = url;
    // Persist so a phone pointed at the Mac's LAN address stays pointed there.
    ref.read(secureStorageProvider).write(key: 'base_url', value: url);
  }

  /// Opens Google in the browser and polls until the server parks a session.
  Future<void> _googleSignIn() async {
    _applyBaseUrl();
    final auth = ref.read(authProvider.notifier);
    auth.setLoading(true);
    try {
      final start = await ref.read(clientProvider).googleLoginStart();
      await launchUrl(Uri.parse(start['authorization_url'] as String),
          mode: LaunchMode.externalApplication);
      final pollToken = start['poll_token'] as String;
      // Android freezes the network while the browser is in front; a single failed
      // poll used to escape the loop and leave the spinner on forever. Every error is
      // a retry now, and the loop only ends on tokens, on a definite server "no", or
      // after the server's own five-minute window.
      final deadline = DateTime.now().add(const Duration(minutes: 5));
      while (DateTime.now().isBefore(deadline)) {
        await Future.delayed(const Duration(seconds: 2));
        if (!mounted) return;
        Map<String, dynamic> poll;
        try {
          poll = await ref.read(clientProvider).googleLoginPoll(pollToken);
        } on ProblemException catch (e) {
          if (e.status == 401) rethrow; // expired or unknown: start again
          continue;
        } catch (_) {
          continue; // offline for a moment (backgrounded); try again
        }
        if (poll['pending'] != true) {
          await auth.adoptTokens(
              poll['access_token'] as String, poll['refresh_token'] as String);
          return;
        }
      }
      auth.setLoading(false);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          duration: Duration(seconds: 8),
          content: Text('Sign-in timed out. Google can only send you back to a public '
              'https server — set the server URL to your deployed address '
              '(not a LAN IP or localhost) and try again.')));
    } on ProblemException catch (e) {
      auth.setLoading(false);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }
}
