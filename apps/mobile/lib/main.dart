import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:window_manager/window_manager.dart';

import 'node/platform_hooks.dart';
import 'screens/home.dart';
import 'screens/sign_in.dart';
import 'state/providers.dart';
import 'push/push.dart';
import 'theme.dart';

bool get isDesktop =>
    !kIsWeb &&
    (defaultTargetPlatform == TargetPlatform.macOS ||
        defaultTargetPlatform == TargetPlatform.linux ||
        defaultTargetPlatform == TargetPlatform.windows);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (isDesktop) await windowManager.ensureInitialized();
  runApp(const ProviderScope(child: JarvisApp()));
}

class JarvisApp extends ConsumerStatefulWidget {
  const JarvisApp({super.key});

  @override
  ConsumerState<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends ConsumerState<JarvisApp> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      // The server URL must be restored before auth: changing it rebuilds the
      // client, which would drop a session restored against the wrong host.
      final stored =
          await ref.read(secureStorageProvider).read(key: 'base_url');
      if (stored != null && stored.isNotEmpty) {
        ref.read(baseUrlProvider.notifier).state = stored;
      }
      await ref.read(themeModeProvider.notifier).restore();
      await ref.read(responseCacheProvider).warm();
      await ref.read(authProvider.notifier).restore();
    });
    // Whenever a session exists, make this phone the first rung of the ladder.
    ref.listenManual(authProvider, (prev, next) {
      if (next.signedIn && prev?.signedIn != true) {
        PushRegistrar.register(ref.read(clientProvider)).catchError((_) => null);
        // The notification mirror forwards allowlisted titles while signed in.
        final mirror = ref.read(notificationMirrorProvider);
        mirror.restore().then((_) => mirror.start(
              ref.read(clientProvider),
              () => ref.read(secureStorageProvider).read(key: 'phone_node_device_id'),
            ));
        // This device answers signed jobs as soon as it is signed in and paired.
        ref.read(phoneNodeProvider).boot(
              ref.read(secureStorageProvider),
              ref.read(clientProvider),
            );
        // The activity sampler posts foreground app names once a minute when allowed.
        final sampler = ref.read(activitySamplerProvider);
        sampler.restore().then((_) => sampler.start(
              ref.read(clientProvider),
              () => ref.read(secureStorageProvider).read(key: 'phone_node_device_id'),
            ));
      }
    }, fireImmediately: true);
  }

  @override
  Widget build(BuildContext context) {
    final signedIn = ref.watch(authProvider).signedIn;

    return MaterialApp(
      title: 'JARVIS X',
      scaffoldMessengerKey: messengerKey,
      debugShowCheckedModeBanner: false,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      themeMode: ref.watch(themeModeProvider),
      home: signedIn ? const HomeScreen() : const SignInScreen(),
    );
  }
}
