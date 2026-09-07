import 'dart:async';

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import 'audit.dart';
import 'changelog.dart';
import '../voice/wake_service.dart';
import '../voice/voice_prefs.dart';
import 'package:flutter_tts/flutter_tts.dart';

/// Every `JARVIS_*` variable, editable in place, prefilled with what the server is
/// running with right now. Grouped the way `.env.example` is grouped; typed inputs
/// (switch, dropdown, number, text); secrets obscured until revealed.
class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(settingsProvider);
    return settings.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(child: Text('Could not load settings: $e')),
      data: (data) => _SettingsForm(
        fields: ((data['fields'] as List<dynamic>?) ?? const [])
            .cast<Map<String, dynamic>>(),
        envFile: data['env_file'] as String? ?? '.env',
      ),
    );
  }
}

/// What is connected, plus the connect flow — with feedback when Google lands.
class _ConnectedAccounts extends ConsumerStatefulWidget {
  const _ConnectedAccounts();

  @override
  ConsumerState<_ConnectedAccounts> createState() => _ConnectedAccountsState();
}

class _ConnectedAccountsState extends ConsumerState<_ConnectedAccounts> {
  Timer? _poll;

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final connectors = ref.watch(connectorsProvider);
    final accounts = connectors.valueOrNull ?? const <Map<String, dynamic>>[];
    final active =
        accounts.where((a) => a['status'] == 'active' && a['revoked_at'] == null);

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text('Accounts', style: Theme.of(context).textTheme.titleMedium),
        const Spacer(),
        IconButton(
          tooltip: 'Refresh',
          icon: const Icon(Icons.refresh, size: 20),
          onPressed: () => ref.invalidate(connectorsProvider),
        ),
      ]),
      if (active.isEmpty)
        Text('Nothing connected yet.',
            style: Theme.of(context).textTheme.bodySmall)
      else
        for (final a in active)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.check_circle, color: Colors.green, size: 20),
            title: Text('${a['provider']} — ${a['display_name'] ?? ''}'),
            subtitle: Text('${(a['scopes'] as List?)?.length ?? 0} scopes granted'),
          ),
      const SizedBox(height: 8),
      OutlinedButton.icon(
        icon: const Icon(Icons.link),
        label: const Text('Connect Google (Gmail · Calendar · YouTube)'),
        onPressed: _connect,
      ),
      const SizedBox(height: 4),
      Text(
        'Opens Google in your browser; finish there, then come back — this list '
        'updates itself. Do it on the Mac: the callback returns to the API server.',
        style: Theme.of(context).textTheme.bodySmall,
      ),
    ]);
  }

  Future<void> _connect() async {
    try {
      final url = await ref.read(clientProvider).googleAuthUrl();
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } on ProblemException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      return;
    }
    // Watch for the account to land while the user finishes in the browser.
    var ticks = 0;
    _poll?.cancel();
    _poll = Timer.periodic(const Duration(seconds: 5), (t) async {
      ticks += 1;
      if (!mounted || ticks > 36) {
        t.cancel();
        return;
      }
      final accounts = await ref.read(clientProvider).connectors();
      final connected = accounts.any(
          (a) => a['status'] == 'active' && a['revoked_at'] == null);
      if (connected) {
        t.cancel();
        ref.invalidate(connectorsProvider);
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Google connected ✓')));
        }
      }
    });
  }
}

class _SetupGuide extends StatelessWidget {
  const _SetupGuide();

  static const _steps = [
    ('1. Start the server', 'On the Mac: make api, make video-worker.'),
    ('2. Add provider keys', 'Below — Groq and Gemini unblock the brain; the rest '
        'are optional. Ollama on the Mac is the automatic offline fallback.'),
    ('3. Connect Google', 'Button above, from the Mac. Grants Gmail, Calendar and '
        'YouTube upload/analytics.'),
    ('4. Add your voice and face', 'On the Mac: uv run python -m '
        'scripts.upload_avatar_assets --voice me.wav --avatar hero.mp4 --music bgm.mp3'),
    ('5. Phone setup', 'Install the APK, then set the API field on sign-in to '
        'http://<mac-lan-ip>:8000 — both devices share one brain, nothing syncs '
        'device-to-device.'),
    ('6. Automate', 'Set a daily topic below; renders wait in Approvals before '
        'anything is published.'),
  ];

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ExpansionTile(
        leading: const Icon(Icons.help_outline),
        title: const Text('Setup guide'),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        children: [
          for (final (title, body) in _steps)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              title: Text(title),
              subtitle: Text(body),
            ),
        ],
      ),
    );
  }
}

class _SettingsForm extends ConsumerStatefulWidget {
  const _SettingsForm({required this.fields, required this.envFile});

  final List<Map<String, dynamic>> fields;
  final String envFile;

  @override
  ConsumerState<_SettingsForm> createState() => _SettingsFormState();
}

class _SettingsFormState extends ConsumerState<_SettingsForm> {
  final _controllers = <String, TextEditingController>{};
  final _values = <String, dynamic>{}; // switches and dropdowns
  final _expanded = <String>{};
  bool _busy = false;
  bool _reveal = false;
  String _filter = '';

  @override
  void initState() {
    super.initState();
    for (final f in widget.fields) {
      final name = f['name'] as String;
      final kind = f['kind'] as String;
      if (kind == 'bool' || kind == 'choice') {
        _values[name] = f['value'];
      } else {
        _controllers[name] = TextEditingController(text: '${f['value'] ?? ''}');
      }
    }
    // Open the sections people come here for; the rest fold away.
    _expanded.addAll(['LLM providers', 'Telegram', 'Slack']);
  }

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  Map<String, List<Map<String, dynamic>>> get _sections {
    final out = <String, List<Map<String, dynamic>>>{};
    for (final f in widget.fields) {
      final name = f['name'] as String;
      if (_filter.isNotEmpty &&
          !name.contains(_filter) &&
          !(f['env'] as String).toLowerCase().contains(_filter)) {
        continue;
      }
      out.putIfAbsent(f['section'] as String, () => []).add(f);
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final sections = _sections;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const _SetupGuide(),
        const SizedBox(height: 8),
        const _ConnectedAccounts(),
        const SizedBox(height: 16),
        const _Appearance(),
        const SizedBox(height: 8),
        const _AlwaysListening(),
        const SizedBox(height: 8),
        const _VoiceSettings(),
        const SizedBox(height: 8),
        _QuietHours(
          current: widget.fields.firstWhere(
                (f) => f['name'] == 'quiet_hours',
                orElse: () => const {'value': ''},
              )['value'] as String? ??
              '',
        ),
        const SizedBox(height: 8),
        const _Memories(),
        const SizedBox(height: 8),
        const _Diagnostics(),
        const SizedBox(height: 8),
        Card(
          margin: EdgeInsets.zero,
          child: ListTile(
            leading: const Icon(Icons.auto_awesome_outlined),
            title: const Text("What's new"),
            subtitle: Text('Version ${Changelog.latest}'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Changelog.open(context),
          ),
        ),
        const SizedBox(height: 8),
        const _YourData(),
        const SizedBox(height: 16),
        Row(children: [
          Text('Configuration', style: Theme.of(context).textTheme.titleMedium),
          const Spacer(),
          IconButton(
            tooltip: _reveal ? 'Hide secrets' : 'Show secrets',
            icon: Icon(_reveal ? Icons.visibility_off : Icons.visibility, size: 20),
            onPressed: () => setState(() => _reveal = !_reveal),
          ),
        ]),
        Text(
          'Every JARVIS_* variable, as the server is running it now. Changes apply '
          'immediately and are written to ${widget.envFile}.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 8),
        TextField(
          decoration: const InputDecoration(
            prefixIcon: Icon(Icons.search, size: 18),
            hintText: 'Filter (e.g. slack, groq, tts)',
            isDense: true,
            border: OutlineInputBorder(),
          ),
          onChanged: (v) => setState(() => _filter = v.trim().toLowerCase()),
        ),
        const SizedBox(height: 8),
        for (final entry in sections.entries)
          Card(
            key: Key('section-${entry.key}'),
            child: ExpansionTile(
              title: Text(entry.key),
              subtitle: Text('${entry.value.length} setting(s)'),
              initiallyExpanded: _filter.isNotEmpty || _expanded.contains(entry.key),
              onExpansionChanged: (open) => setState(
                  () => open ? _expanded.add(entry.key) : _expanded.remove(entry.key)),
              childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              children: [for (final f in entry.value) _field(context, f)],
            ),
          ),
        const SizedBox(height: 16),
        FilledButton(
          onPressed: _busy ? null : _save,
          child: Text(_busy ? 'Saving…' : 'Save changes'),
        ),
        const SizedBox(height: 24),
      ],
    );
  }

  Widget _field(BuildContext context, Map<String, dynamic> f) {
    final name = f['name'] as String;
    final kind = f['kind'] as String;
    final secret = f['secret'] == true;
    final description = (f['description'] as String?) ?? '';
    final label = name.replaceAll('_', ' ');

    switch (kind) {
      case 'bool':
        return SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(label),
          subtitle: Text(description.isEmpty ? f['env'] as String : description),
          value: _values[name] == true,
          onChanged: (v) => setState(() => _values[name] = v),
        );
      case 'choice':
        final options = ((f['options'] as List<dynamic>?) ?? const []).cast<String>();
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: DropdownButtonFormField<String>(
            initialValue: options.contains('${_values[name]}') ? '${_values[name]}' : null,
            decoration: InputDecoration(
              labelText: label,
              helperText: f['env'] as String,
              border: const OutlineInputBorder(),
            ),
            items: [for (final o in options) DropdownMenuItem(value: o, child: Text(o))],
            onChanged: (v) => setState(() => _values[name] = v),
          ),
        );
      default:
        final isSet = '${f['value'] ?? ''}'.isNotEmpty;
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: TextField(
            controller: _controllers[name],
            obscureText: secret && !_reveal,
            keyboardType: kind == 'int' || kind == 'float'
                ? const TextInputType.numberWithOptions(decimal: true)
                : TextInputType.text,
            decoration: InputDecoration(
              labelText: label,
              helperText: description.isNotEmpty ? description : f['env'] as String,
              helperMaxLines: 3,
              border: const OutlineInputBorder(),
              suffixIcon: secret
                  ? Icon(isSet ? Icons.check_circle : Icons.radio_button_unchecked,
                      color: isSet ? Colors.green : null, size: 18)
                  : null,
            ),
          ),
        );
    }
  }

  Future<void> _save() async {
    final changes = <String, dynamic>{};
    for (final f in widget.fields) {
      final name = f['name'] as String;
      final kind = f['kind'] as String;
      if (kind == 'bool' || kind == 'choice') {
        if (_values[name] != f['value']) changes[name] = _values[name];
      } else {
        final text = _controllers[name]!.text.trim();
        if (text != '${f['value'] ?? ''}') changes[name] = text;
      }
    }
    if (changes.isEmpty) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Nothing changed')));
      return;
    }

    setState(() => _busy = true);
    try {
      await ref.read(clientProvider).updateSettings(changes);
      ref.invalidate(settingsProvider);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Saved ${changes.length} setting(s) — live now')));
    } on ProblemException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}

/// System / dark / light.
class _Appearance extends ConsumerWidget {
  const _Appearance();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(themeModeProvider);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        child: Row(children: [
          Expanded(child: Text('Appearance', style: Theme.of(context).textTheme.titleMedium)),
          SegmentedButton<ThemeMode>(
            showSelectedIcon: false,
            segments: const [
              ButtonSegment(value: ThemeMode.system, icon: Icon(Icons.brightness_auto), label: Text('Auto')),
              ButtonSegment(value: ThemeMode.dark, icon: Icon(Icons.dark_mode_outlined), label: Text('Dark')),
              ButtonSegment(value: ThemeMode.light, icon: Icon(Icons.light_mode_outlined), label: Text('Light')),
            ],
            selected: {mode},
            onSelectionChanged: (v) => ref.read(themeModeProvider.notifier).set(v.first),
          ),
        ]),
      ),
    );
  }
}

/// "Hey Jarvis" with the screen off (Android). Free and on-device — no key to set up;
/// the audio is transcribed on the phone and never leaves it until the wake word fires.
class _AlwaysListening extends ConsumerWidget {
  const _AlwaysListening();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wake = ref.watch(wakeProvider);
    final supported = WakeController.supported;
    final phase = switch (wake.phase) {
      WakePhase.off => 'Off',
      WakePhase.armed => 'Armed — say "Jarvis"',
      WakePhase.listening => 'Listening…',
      WakePhase.thinking => 'Thinking…',
      WakePhase.speaking => 'Speaking',
      WakePhase.error => 'Error',
    };
    return Card(
      margin: EdgeInsets.zero,
      child: Column(children: [
        SwitchListTile(
          key: const Key('alwaysListening'),
          title: const Text('Always listening — "Hey Jarvis"'),
          subtitle: Text(
            !supported
                ? 'Android only. On the Mac use: python -m macnode voice'
                : wake.error ?? '$phase. Runs on-device; a notification shows while it is on.',
          ),
          value: wake.on,
          onChanged: !supported
              ? null
              : (v) => v
                  ? ref.read(wakeProvider.notifier).start()
                  : ref.read(wakeProvider.notifier).stop(),
        ),
        if (wake.lastHeard != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text('You: ${wake.lastHeard}\nJarvis: ${wake.lastReply ?? ''}',
                  style: Theme.of(context).textTheme.bodySmall),
            ),
          ),
      ]),
    );
  }
}

/// What Jarvis remembers about you — and a way to make it forget.
class _Memories extends ConsumerStatefulWidget {
  const _Memories();

  @override
  ConsumerState<_Memories> createState() => _MemoriesState();
}

class _MemoriesState extends ConsumerState<_Memories> {
  Future<List<Map<String, dynamic>>>? _rows;
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: ExpansionTile(
        leading: const Icon(Icons.psychology_outlined),
        title: const Text('What Jarvis remembers'),
        subtitle: const Text('Facts learned from your chats and mail. Tap one to forget it.'),
        onExpansionChanged: (open) => setState(() {
          _open = open;
          if (open) _rows = ref.read(clientProvider).memories();
        }),
        children: [
          if (_open)
            FutureBuilder<List<Map<String, dynamic>>>(
              future: _rows,
              builder: (context, snap) {
                if (!snap.hasData) {
                  return const Padding(
                      padding: EdgeInsets.all(16), child: LinearProgressIndicator(minHeight: 2));
                }
                final rows = snap.data!;
                if (rows.isEmpty) {
                  return const Padding(
                      padding: EdgeInsets.fromLTRB(16, 0, 16, 16),
                      child: Text('Nothing yet. Tell Jarvis something about yourself.'));
                }
                return Column(children: [
                  for (final m in rows)
                    ListTile(
                      dense: true,
                      leading: Icon(
                        switch (m['kind']) {
                          'source' => Icons.mail_outline,
                          'semantic' => Icons.lightbulb_outline,
                          _ => Icons.chat_bubble_outline,
                        },
                        size: 18,
                      ),
                      title: Text(m['content'] as String),
                      subtitle: Text('${m['kind']} · ${m['source'] ?? 'chat'}'),
                      trailing: IconButton(
                        tooltip: 'Forget',
                        icon: const Icon(Icons.delete_outline, size: 18),
                        onPressed: () async {
                          await ref.read(clientProvider).forgetMemory(m['id'] as String);
                          setState(() => _rows = ref.read(clientProvider).memories());
                        },
                      ),
                    ),
                ]);
              },
            ),
        ],
      ),
    );
  }
}

/// The five things that decide whether the app works, on one card.
class _Diagnostics extends ConsumerStatefulWidget {
  const _Diagnostics();

  @override
  ConsumerState<_Diagnostics> createState() => _DiagnosticsState();
}

class _DiagnosticsState extends ConsumerState<_Diagnostics> {
  Future<List<(String, String, bool)>>? _checks;

  Future<List<(String, String, bool)>> _run() async {
    final client = ref.read(clientProvider);
    final out = <(String, String, bool)>[];
    try {
      final h = await client.health();
      out.add(('Server', '${ref.read(baseUrlProvider)} · ${h['env']} · build ${h['build']}', true));
    } catch (e) {
      out.add(('Server', 'unreachable: $e', false));
    }
    out.add(('Signed in', ref.read(authProvider).email ?? '—', ref.read(authProvider).signedIn));
    try {
      // The server's own checks: token validity, worker pulses, last scans.
      final status = await client.systemStatus(fresh: true);
      for (final c in (status['checks'] as List<dynamic>).cast<Map<String, dynamic>>()) {
        if (c['ok'] == null) continue; // not configured — Connections lists those
        out.add((c['name'] as String, c['detail'] as String, c['ok'] == true));
      }
    } catch (e) {
      out.add(('Server checks', '$e', false));
    }
    final node = ref.read(phoneNodeProvider).node;
    out.add(('This device as a hand',
        !node.paired ? 'not paired' : node.connected ? 'connected' : 'paired, not connected',
        node.paired && node.connected));
    final wake = ref.read(wakeProvider);
    out.add(('Always listening', wake.error ?? wake.phase.name, wake.on));
    return out;
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: ExpansionTile(
        leading: const Icon(Icons.monitor_heart_outlined),
        title: const Text('Diagnostics'),
        subtitle: const Text('Server, session, push, device link, wake word'),
        onExpansionChanged: (open) => setState(() {
          if (open) _checks = _run();
        }),
        children: [
          FutureBuilder<List<(String, String, bool)>>(
            future: _checks,
            builder: (context, snap) {
              if (!snap.hasData) {
                return const Padding(
                    padding: EdgeInsets.all(16), child: LinearProgressIndicator(minHeight: 2));
              }
              return Column(children: [
                for (final (name, detail, ok) in snap.data!)
                  ListTile(
                    dense: true,
                    leading: Icon(ok ? Icons.check_circle : Icons.error_outline,
                        color: ok ? JarvisColors.success : JarvisColors.warning, size: 18),
                    title: Text(name),
                    subtitle: Text(detail, maxLines: 2, overflow: TextOverflow.ellipsis),
                  ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                  child: Align(
                    alignment: Alignment.centerRight,
                    child: TextButton.icon(
                      onPressed: () => setState(() => _checks = _run()),
                      icon: const Icon(Icons.refresh, size: 16),
                      label: const Text('Re-check'),
                    ),
                  ),
                ),
              ]);
            },
          ),
        ],
      ),
    );
  }
}


/// Export everything, read the audit trail, or wipe the account's content.
class _YourData extends ConsumerWidget {
  const _YourData();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Card(
      child: ExpansionTile(
        leading: const Icon(Icons.shield_outlined),
        title: const Text('Your data'),
        subtitle: const Text('Export, audit trail, or delete'),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        children: [
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.download_outlined),
            title: const Text('Export my data'),
            subtitle: const Text('Everything as one JSON, yours to keep'),
            onTap: () async {
              try {
                final data = await ref.read(clientProvider).exportData();
                if (!context.mounted) return;
                final counts = {
                  'tasks': (data['tasks'] as List).length,
                  'chats': (data['conversations'] as List).length,
                  'memories': (data['memories'] as List).length,
                };
                showDialog<void>(
                  context: context,
                  builder: (context) => AlertDialog(
                    title: const Text('Export ready'),
                    content: Text('Exported $counts. Saved to the clipboard.'),
                    actions: [
                      TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('OK'),
                      ),
                    ],
                  ),
                );
                await Clipboard.setData(ClipboardData(text: jsonEncode(data)));
              } on ProblemException catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                }
              }
            },
          ),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.receipt_long_outlined),
            title: const Text('Audit trail'),
            subtitle: const Text('Everything the system did on your account'),
            onTap: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const AuditScreen())),
          ),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.delete_forever_outlined, color: JarvisColors.danger),
            title: const Text('Delete my data',
                style: TextStyle(color: JarvisColors.danger)),
            subtitle: const Text('Tasks, chats, routines, memories. Keeps the account'),
            onTap: () async {
              final sure = await showDialog<bool>(
                context: context,
                builder: (context) => AlertDialog(
                  title: const Text('Delete all your data?'),
                  content: const Text(
                      'Tasks, deadlines, chats, routines and memories are removed. '
                      'This cannot be undone. Your login stays.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('Cancel')),
                    FilledButton(
                      style: FilledButton.styleFrom(backgroundColor: JarvisColors.danger),
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('Delete everything'),
                    ),
                  ],
                ),
              );
              if (sure != true) return;
              try {
                await ref.read(clientProvider).wipeAccount();
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Your data was deleted')));
                }
              } on ProblemException catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                }
              }
            },
          ),
        ],
      ),
    );
  }
}
/// Voice reply speed and voice picker (FEATURES-50 #19). The speed applies to both the
/// server voice and the on-device fallback; the picker chooses the on-device voice.
class _VoiceSettings extends StatefulWidget {
  const _VoiceSettings();

  @override
  State<_VoiceSettings> createState() => _VoiceSettingsState();
}

class _VoiceSettingsState extends State<_VoiceSettings> {
  final _tts = FlutterTts();
  double _rate = 1.0;
  String? _voice; // "name|locale"
  List<Map<String, String>> _voices = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final rate = await VoicePrefs.rate();
    final voice = await VoicePrefs.voice();
    List<Map<String, String>> voices = const [];
    try {
      final raw = await _tts.getVoices as List<dynamic>? ?? const [];
      voices = raw
          .map((v) => (v as Map).map((k, val) => MapEntry('$k', '$val')))
          .where((v) => (v['locale'] ?? '').toLowerCase().startsWith('en'))
          .toList();
    } catch (_) {}
    if (mounted) {
      setState(() {
        _rate = rate;
        _voice = voice;
        _voices = voices;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.record_voice_over_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Voice', style: Theme.of(context).textTheme.titleMedium),
          ]),
          Row(children: [
            const Text('Speed'),
            Expanded(
              child: Slider(
                value: _rate,
                min: 0.5,
                max: 1.75,
                divisions: 5,
                label: '${_rate.toStringAsFixed(2)}×',
                onChanged: (v) => setState(() => _rate = v),
                onChangeEnd: (v) => VoicePrefs.setRate(v),
              ),
            ),
            Text('${_rate.toStringAsFixed(2)}×'),
          ]),
          if (_voices.isNotEmpty)
            DropdownButton<String>(
              isExpanded: true,
              value: _voices.any((v) => '${v['name']}|${v['locale']}' == _voice)
                  ? _voice
                  : null,
              hint: const Text('On-device voice (default)'),
              items: [
                const DropdownMenuItem(value: null, child: Text('Default')),
                for (final v in _voices)
                  DropdownMenuItem(
                    value: '${v['name']}|${v['locale']}',
                    child: Text('${v['name']} (${v['locale']})',
                        maxLines: 1, overflow: TextOverflow.ellipsis),
                  ),
              ],
              onChanged: (v) {
                setState(() => _voice = v);
                VoicePrefs.setVoice(v);
              },
            ),
        ]),
      ),
    );
  }
}

/// Do-not-disturb / quiet hours (FEATURES-50 #29). Jarvis holds non-urgent notifications
/// during this window — enforced on the server, so it applies to every device at once.
class _QuietHours extends ConsumerStatefulWidget {
  const _QuietHours({required this.current});

  final String current; // "HH:MM-HH:MM" or ""

  @override
  ConsumerState<_QuietHours> createState() => _QuietHoursState();
}

class _QuietHoursState extends ConsumerState<_QuietHours> {
  late bool _enabled;
  late TimeOfDay _start;
  late TimeOfDay _end;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    final parsed = _parse(widget.current);
    _enabled = parsed != null;
    _start = parsed?.$1 ?? const TimeOfDay(hour: 22, minute: 30);
    _end = parsed?.$2 ?? const TimeOfDay(hour: 7, minute: 0);
  }

  static (TimeOfDay, TimeOfDay)? _parse(String v) {
    final m = RegExp(r'^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$').firstMatch(v.trim());
    if (m == null) return null;
    return (
      TimeOfDay(hour: int.parse(m.group(1)!), minute: int.parse(m.group(2)!)),
      TimeOfDay(hour: int.parse(m.group(3)!), minute: int.parse(m.group(4)!)),
    );
  }

  String _fmt(TimeOfDay t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  Future<void> _save() async {
    setState(() => _busy = true);
    final value = _enabled ? '${_fmt(_start)}-${_fmt(_end)}' : '';
    try {
      await ref.read(clientProvider).updateSettings({'quiet_hours': value});
      ref.invalidate(settingsProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(_enabled ? 'Quiet hours saved' : 'Quiet hours off')));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pick(bool start) async {
    final picked = await showTimePicker(context: context, initialTime: start ? _start : _end);
    if (picked != null) setState(() => start ? _start = picked : _end = picked);
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: Column(children: [
        SwitchListTile(
          secondary: const Icon(Icons.do_not_disturb_on_outlined),
          title: const Text('Do not disturb'),
          subtitle: Text(_enabled
              ? 'Quiet ${_fmt(_start)}–${_fmt(_end)} on every device (urgent still breaks through)'
              : 'Hold non-urgent notifications on a schedule'),
          value: _enabled,
          onChanged: _busy ? null : (v) => setState(() => _enabled = v),
        ),
        if (_enabled)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Row(children: [
              OutlinedButton(onPressed: () => _pick(true), child: Text('From ${_fmt(_start)}')),
              const SizedBox(width: 8),
              OutlinedButton(onPressed: () => _pick(false), child: Text('to ${_fmt(_end)}')),
              const Spacer(),
              FilledButton(onPressed: _busy ? null : _save, child: const Text('Save')),
            ]),
          )
        else
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Align(
              alignment: Alignment.centerRight,
              child: FilledButton(onPressed: _busy ? null : _save, child: const Text('Save')),
            ),
          ),
      ]),
    );
  }
}
