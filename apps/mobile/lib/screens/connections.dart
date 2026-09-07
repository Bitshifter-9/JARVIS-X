import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/states.dart';

/// Every connection, checked for real: is the token valid, when did it last scan, what
/// did it find, is the worker alive. The one screen for "is it actually working".
class ConnectionsScreen extends ConsumerStatefulWidget {
  const ConnectionsScreen({super.key});

  @override
  ConsumerState<ConnectionsScreen> createState() => _ConnectionsScreenState();
}

class _ConnectionsScreenState extends ConsumerState<ConnectionsScreen> {
  Future<Map<String, dynamic>>? _status;
  bool _syncing = false;

  @override
  void initState() {
    super.initState();
    _status = ref.read(clientProvider).systemStatus();
  }

  Future<void> _refresh({bool fresh = true}) async {
    setState(() => _status = ref.read(clientProvider).systemStatus(fresh: fresh));
    await _status;
  }

  Future<void> _syncAll() async {
    setState(() => _syncing = true);
    try {
      final r = await ref.read(clientProvider).syncAllConnectors();
      if (!mounted) return;
      final found = (r['new'] as Map<String, dynamic>? ?? {}).entries.map((e) {
        final v = e.value as Map;
        // Slack reports {channels, new}; an account reports {provider: count}.
        final n = v.containsKey('new')
            ? v['new'] as int
            : v.values.fold<int>(0, (a, b) => a + (b as int));
        return '${e.key}: $n new';
      }).join(', ');
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(r['synced'] == 0
              ? 'Nothing connected yet — connect Google below'
              : 'Scanned ${r['synced']} account(s) · ${found.isEmpty ? 'nothing new' : found}')));
      await _refresh();
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  Future<void> _connectGoogle() async {
    try {
      final url = await ref.read(clientProvider).googleAuthUrl();
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _refresh,
      child: FutureBuilder<Map<String, dynamic>>(
        future: _status,
        builder: (context, snap) {
          if (snap.hasError) {
            return ErrorState(message: '${snap.error}', onRetry: _refresh);
          }
          if (!snap.hasData) return const SkeletonList(rows: 6, height: 68);
          final body = snap.data!;
          final checks = (body['checks'] as List<dynamic>).cast<Map<String, dynamic>>();
          final groups = <String, List<Map<String, dynamic>>>{};
          for (final c in checks) {
            groups.putIfAbsent(c['group'] as String, () => []).add(c);
          }
          const order = ['connectors', 'devices', 'models', 'core'];
          const titles = {
            'connectors': 'Connections',
            'devices': 'Devices, push and voice',
            'models': 'Models',
            'core': 'Server and workers',
          };
          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
            children: [
              _Summary(body: body, onSync: _syncing ? null : _syncAll, onConnect: _connectGoogle)
                  .enter(0),
              const _LinkTelegram(),
              const _LinkSlack(),
              for (final (gi, g) in order.indexed)
                if (groups[g] != null) ...[
                  Padding(
                    padding: const EdgeInsets.fromLTRB(4, 18, 4, 6),
                    child: Text(titles[g]!, style: Theme.of(context).textTheme.titleMedium),
                  ),
                  for (final (i, c) in groups[g]!.indexed) _CheckTile(check: c, onAction: _act).enter(gi * 4 + i + 1),
                ],
              const SizedBox(height: 8),
              Text('Checked ${DateTime.parse(body['at'] as String).toLocal().toString().substring(11, 16)} · '
                  'pull down to re-check',
                  textAlign: TextAlign.center, style: Theme.of(context).textTheme.labelSmall),
            ],
          );
        },
      ),
    );
  }

  Future<void> _act(String action) async {
    switch (action) {
      case 'connect_google':
        await _connectGoogle();
      case 'sync':
        await _syncAll();
      default:
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Fix this in Settings → Configuration')));
        }
    }
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.body, required this.onSync, required this.onConnect});

  final Map<String, dynamic> body;
  final VoidCallback? onSync;
  final VoidCallback onConnect;

  @override
  Widget build(BuildContext context) {
    final problems = body['problems'] as int? ?? 0;
    final unconfigured = body['unconfigured'] as int? ?? 0;
    final ok = body['ok'] == true;
    final color = ok ? JarvisColors.success : (problems > 0 ? JarvisColors.danger : JarvisColors.warning);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Container(
              width: 12, height: 12,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle, boxShadow: [
                BoxShadow(color: color.withValues(alpha: 0.6), blurRadius: 10),
              ]),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                ok
                    ? 'Everything configured is working'
                    : problems > 0
                        ? '$problems problem${problems == 1 ? '' : 's'}'
                        : 'Working · $unconfigured not set up yet',
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
          ]),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            FilledButton.icon(
              onPressed: onSync,
              icon: const Icon(Icons.sync, size: 18),
              label: Text(onSync == null ? 'Scanning…' : 'Scan now'),
            ),
            OutlinedButton.icon(
              onPressed: onConnect,
              icon: const Icon(Icons.add_link, size: 18),
              label: const Text('Connect Google'),
            ),
          ]),
        ]),
      ),
    );
  }
}

class _CheckTile extends StatelessWidget {
  const _CheckTile({required this.check, required this.onAction});

  final Map<String, dynamic> check;
  final Future<void> Function(String action) onAction;

  @override
  Widget build(BuildContext context) {
    final ok = check['ok'];
    final (icon, color) = switch (ok) {
      true => (Icons.check_circle, JarvisColors.success),
      false => (Icons.error, JarvisColors.danger),
      _ => (Icons.radio_button_unchecked, Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.4)),
    };
    final action = check['action'] as String?;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: Icon(icon, color: color),
        title: Text(check['name'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(check['detail'] as String, maxLines: 3),
        trailing: action == null
            ? null
            : TextButton(
                onPressed: () => onAction(action),
                child: Text(switch (action) {
                  'connect_google' => 'Connect',
                  'sync' => 'Scan',
                  'pair' => 'Pair',
                  _ => 'Fix',
                }),
              ),
      ),
    );
  }
}


/// Link your Telegram chat so you can talk to Jarvis there. Message the bot /start,
/// paste the chat id it replies with, and Link.
class _LinkTelegram extends ConsumerStatefulWidget {
  const _LinkTelegram();

  @override
  ConsumerState<_LinkTelegram> createState() => _LinkTelegramState();
}

class _LinkTelegramState extends ConsumerState<_LinkTelegram> {
  final _id = TextEditingController();
  bool _busy = false;
  String? _linked;

  @override
  void initState() {
    super.initState();
    ref.read(clientProvider).identities().then((rows) {
      final tg = rows.where((r) => r['provider'] == 'telegram');
      if (tg.isNotEmpty && mounted) setState(() => _linked = tg.first['subject'] as String?);
    }).catchError((_) {});
  }

  @override
  void dispose() {
    _id.dispose();
    super.dispose();
  }

  Future<void> _link() async {
    final id = _id.text.trim();
    if (id.isEmpty) return;
    setState(() => _busy = true);
    try {
      await ref.read(clientProvider).linkIdentity('telegram', id);
      if (mounted) {
        setState(() {
          _linked = id;
          _id.clear();
        });
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Telegram linked — message the bot and it will answer')));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.send, color: JarvisColors.sky),
            const SizedBox(width: 10),
            Expanded(
                child: Text('Talk to Jarvis on Telegram',
                    style: Theme.of(context).textTheme.titleMedium)),
            if (_linked != null)
              const Chip(
                avatar: Icon(Icons.check, size: 16, color: JarvisColors.success),
                label: Text('Linked'),
              ),
          ]),
          const SizedBox(height: 8),
          if (_linked != null)
            Text('Chat $_linked is linked. Message the bot: "what is due today?", '
                '"ring my phone", "screenshot my Mac".',
                style: Theme.of(context).textTheme.bodySmall)
          else ...[
            Text('Message the bot /start, then paste the chat id it replies with.',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 10),
            Row(children: [
              Expanded(
                child: TextField(
                  controller: _id,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                      labelText: 'Telegram chat id', isDense: true),
                ),
              ),
              const SizedBox(width: 10),
              FilledButton(onPressed: _busy ? null : _link, child: const Text('Link')),
            ]),
          ],
        ]),
      ),
    );
  }
}

/// Link your Slack member id so Slack messages act on your account. Slack profile →
/// ⋯ → Copy member ID (U0…), paste it here.
class _LinkSlack extends ConsumerStatefulWidget {
  const _LinkSlack();

  @override
  ConsumerState<_LinkSlack> createState() => _LinkSlackState();
}

class _LinkSlackState extends ConsumerState<_LinkSlack> {
  final _id = TextEditingController();
  bool _busy = false;
  String? _linked;

  @override
  void initState() {
    super.initState();
    ref.read(clientProvider).identities().then((rows) {
      final sl = rows.where((r) => r['provider'] == 'slack');
      if (sl.isNotEmpty && mounted) setState(() => _linked = sl.first['subject'] as String?);
    }).catchError((_) {});
  }

  @override
  void dispose() {
    _id.dispose();
    super.dispose();
  }

  Future<void> _scan() async {
    setState(() => _busy = true);
    try {
      final r = await ref.read(clientProvider).scanSlack();
      if (!mounted) return;
      final missing = (r['missing_scopes'] as List?)?.cast<String>() ?? const [];
      final String msg;
      if (r['ok'] == true) {
        msg = 'Scanned ${r['channels']} conversation(s) · ${r['new']} new deadline(s)';
      } else if (missing.isNotEmpty) {
        msg = 'Add ${missing.join(', ')} to the Slack app scopes, '
            'then reinstall it. Scanned ${r['channels']} so far.';
      } else if (r['reason'] == 'not_linked') {
        msg = 'Link your Slack member id first';
      } else {
        msg = 'Set your Slack bot token in Settings';
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(msg),
        duration: Duration(seconds: missing.isNotEmpty ? 8 : 4),
      ));
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _link() async {
    final id = _id.text.trim();
    if (id.isEmpty) return;
    setState(() => _busy = true);
    try {
      await ref.read(clientProvider).linkIdentity('slack', id);
      if (mounted) {
        setState(() {
          _linked = id;
          _id.clear();
        });
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Slack linked — a deadline you post now becomes a task')));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.tag, color: JarvisColors.sky),
            const SizedBox(width: 10),
            Expanded(
                child: Text('Slack messages → tasks',
                    style: Theme.of(context).textTheme.titleMedium)),
            if (_linked != null)
              const Chip(
                avatar: Icon(Icons.check, size: 16, color: JarvisColors.success),
                label: Text('Linked'),
              ),
          ]),
          const SizedBox(height: 8),
          if (_linked != null) ...[
            Text('Member $_linked is linked. Post "the report is due Friday 5pm" in a '
                'channel or group the bot is in — it becomes a deadline in Goals.',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: FilledButton.tonalIcon(
                onPressed: _busy ? null : _scan,
                icon: _busy
                    ? const SizedBox(
                        width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.sync, size: 18),
                label: const Text('Scan Slack now'),
              ),
            ),
          ]
          else ...[
            Text('Slack profile → ⋯ → Copy member ID (starts with U0…), paste it here. '
                'The bot must be in the channel (channel → Integrations → Add apps).',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 10),
            Row(children: [
              Expanded(
                child: TextField(
                  controller: _id,
                  decoration: const InputDecoration(
                      labelText: 'Slack member id (U0…)', isDense: true),
                ),
              ),
              const SizedBox(width: 10),
              FilledButton(onPressed: _busy ? null : _link, child: const Text('Link')),
            ]),
          ],
        ]),
      ),
    );
  }
}