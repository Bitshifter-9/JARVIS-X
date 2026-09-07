import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter/foundation.dart';
import 'package:intl/intl.dart';

import '../api/models.dart';
import '../node/phone_node.dart';
import '../node/notification_mirror.dart';
import '../state/providers.dart';
import '../theme.dart';

/// Devices: what can act on your behalf, and how to stop it.
class DevicesScreen extends ConsumerWidget {
  const DevicesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final devices = ref.watch(devicesProvider);
    final formatter = DateFormat('d MMM, HH:mm');

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(devicesProvider),
      child: devices.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => ListView(children: [
          const SizedBox(height: 96),
          Center(child: Text('Could not load devices: $e')),
        ]),
        data: (all) {
          // Revoked rows are history, and this very device already has the top card.
          final own = ref.watch(phoneNodeProvider).node.deviceId;
          final list = all.where((d) => !d.revoked && d.id != own).toList();
          return list.isEmpty
            ? ListView(children: [
                const _ThisPhoneCard(),
                const _TrustCard(),
                const SizedBox(height: 48),
                Icon(Icons.laptop_mac,
                    size: 48, color: Theme.of(context).disabledColor),
                const SizedBox(height: 12),
                Text('No paired Mac',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 4),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 48),
                  child: Text(
                    'Everything except Mac-local actions works without one.',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              ])
            : ListView.builder(
                itemCount: list.length + 3,
                itemBuilder: (context, i) {
                  if (i == 0) return const _ThisPhoneCard();
                  if (i == 1) return const _TrustCard();
                  if (i == 2) return const _ClipboardCard();
                  final device = list[i - 3];
                  if (device.platform == 'macos' && !device.revoked) {
                    return _MacCard(device: device);
                  }
                  if (device.platform == 'android' && !device.revoked) {
                    return _PhoneCard(device: device);
                  }
                  return ListTile(
                    leading: Icon(
                      Icons.devices_other,
                      color: device.online ? Colors.green.shade600 : Colors.orange.shade700,
                    ),
                    title: Text(device.name),
                    subtitle: Text([
                      if (device.online) 'Online' else 'Offline',
                      if (device.lastSeenAt != null)
                        'seen ${formatter.format(device.lastSeenAt!)}',
                      '${device.allowedBundleIds.length} allowed app(s)',
                    ].join(' · ')),
                    trailing: Text(device.fingerprint,
                        style: Theme.of(context).textTheme.bodySmall),
                  );
                },
              );
        },
      ),
    );
  }
}


/// One switch instead of a card per screenshot: your own Mac and phone may act for you.
class _TrustCard extends ConsumerWidget {
  const _TrustCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(trustStatusProvider).valueOrNull;
    final trusted = status?['trusted'] == true;
    final until = status?['expires_at'] as String?;
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 12),
      child: SwitchListTile.adaptive(
        secondary: Icon(Icons.verified_user_outlined,
            color: trusted ? JarvisColors.success : null),
        title: const Text('Trust my devices'),
        subtitle: Text(trusted
            ? 'Screenshots, describe screen, typing, files, WhatsApp and the camera run '
                'without an approval card until '
                '${until == null ? 'revoked' : DateTime.parse(until).toLocal().toString().substring(0, 10)}. '
                'Mail, posts, money and the blind click still ask.'
            : 'Let your own Mac and phone act for you for 30 days: no approval card for '
                'screenshots, typing, files, WhatsApp or the camera. Revoke any time.'),
        value: trusted,
        onChanged: (v) async {
          try {
            if (v) {
              await ref.read(clientProvider).trustDevices(days: 30);
            } else {
              await ref.read(clientProvider).untrustDevices();
            }
            ref.invalidate(trustStatusProvider);
            ref.invalidate(permissionsProvider);
          } on ProblemException catch (e) {
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
            }
          }
        },
      ),
    );
  }
}


/// Control your Mac from here. Every button is one typed action through the same
/// gate as the agent: green ones run now (R1); the rest come back as an approval.
class _MacCard extends ConsumerStatefulWidget {
  const _MacCard({required this.device});

  final DeviceInfo device;

  @override
  ConsumerState<_MacCard> createState() => _MacCardState();
}

class _MacCardState extends ConsumerState<_MacCard> {
  double _volume = 50;
  bool _busy = false;

  Future<void> _run(String tool, [Map<String, dynamic> args = const {}]) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final result = await ref.read(clientProvider).runAction(
            tool,
            args: args,
            deviceId: widget.device.id,
          );
      if (!mounted) return;
      final status = result['status'] as String? ?? 'sent';
      final text = switch (status) {
        'queued' => 'Sent to ${widget.device.name} — it will run in a moment',
        'awaiting_approval' => 'Needs your approval — see Approvals',
        'denied' => 'Refused: ${result['reason']}',
        _ => status,
      };
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
      }
    } on ProblemException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _prompt(
      String title, String hint, void Function(String) onValue) async {
    final controller = TextEditingController();
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: InputDecoration(hintText: hint),
          onSubmitted: (v) => Navigator.pop(context, v),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, controller.text),
              child: const Text('Go')),
        ],
      ),
    );
    if (value != null && value.trim().isNotEmpty) onValue(value.trim());
  }

  @override
  Widget build(BuildContext context) {
    final d = widget.device;
    final online = d.online;
    return Card(
      margin: const EdgeInsets.all(12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.laptop_mac,
                color: online ? Colors.green.shade600 : Colors.orange.shade700),
            const SizedBox(width: 8),
            Expanded(
              child: Text(d.name, style: Theme.of(context).textTheme.titleMedium),
            ),
            Text(online ? 'Online' : 'Offline — queued until it connects',
                style: Theme.of(context).textTheme.bodySmall),
            _RemoveButton(device: d),
          ]),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            ActionChip(
              avatar: const Icon(Icons.content_paste_go, size: 18),
              label: const Text('Paste my clipboard there'),
              onPressed: () async {
                final data = await Clipboard.getData(Clipboard.kTextPlain);
                final text = data?.text ?? '';
                if (text.isEmpty) return;
                _run('mac.clipboard_write', {'text': text});
              },
            ),
            ActionChip(
              avatar: const Icon(Icons.notifications_active, size: 18),
              label: const Text('Ring Mac'),
              onPressed: () => _run('mac.ring'),
            ),
            ActionChip(
              key: const Key('mac-screenshot'),
              avatar: const Icon(Icons.screenshot_monitor, size: 18),
              label: const Text('Screenshot'),
              onPressed: () => _run('mac.capture_screen'),
            ),
            ActionChip(
              avatar: const Icon(Icons.public, size: 18),
              label: const Text('Open Chrome'),
              onPressed: () =>
                  _run('mac.open_app', {'bundle_id': 'com.google.Chrome'}),
            ),
            ActionChip(
              avatar: const Icon(Icons.link, size: 18),
              label: const Text('Open URL…'),
              onPressed: () => _prompt('Open on the Mac', 'https://…',
                  (url) => _run('mac.open_url', {'url': url})),
            ),
            ActionChip(
              avatar: const Icon(Icons.chat, size: 18),
              label: const Text('WhatsApp…'),
              onPressed: () => _prompt('WhatsApp: phone, then message',
                  '+91 98765 43210: running late', (v) {
                final parts = v.split(':');
                if (parts.length < 2) return;
                _run('mac.whatsapp_send', {
                  'phone': parts.first.trim(),
                  'text': parts.sublist(1).join(':').trim(),
                });
              }),
            ),
            ActionChip(
              avatar: const Icon(Icons.content_paste, size: 18),
              label: const Text('Clipboard → here'),
              onPressed: () => _run('mac.clipboard_read'),
            ),
            ActionChip(
              avatar: const Icon(Icons.lock_outline, size: 18),
              label: const Text('Lock'),
              onPressed: () => _run('mac.lock_screen'),
            ),
            ActionChip(
              avatar: const Icon(Icons.pause_circle_outline, size: 18),
              label: const Text('Pause music'),
              onPressed: () =>
                  _run('mac.media', {'app': 'Music', 'command': 'pause'}),
            ),
            ActionChip(
              avatar: const Icon(Icons.notifications_active_outlined, size: 18),
              label: const Text('Ping'),
              onPressed: () => _run(
                  'mac.notify', {'title': 'JARVIS', 'body': 'Hello from your phone'}),
            ),
          ]),
          const SizedBox(height: 8),
          Row(children: [
            const Icon(Icons.volume_up, size: 18),
            Expanded(
              child: Slider(
                value: _volume,
                max: 100,
                divisions: 20,
                label: '${_volume.round()}',
                onChanged: (v) => setState(() => _volume = v),
                onChangeEnd: (v) => _run('mac.set_volume', {'level': v.round()}),
              ),
            ),
          ]),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final (key, label, icon) in const [
              ('wifi', 'Wi-Fi', Icons.wifi),
              ('bluetooth', 'Bluetooth', Icons.bluetooth),
              ('dark_mode', 'Dark mode', Icons.dark_mode_outlined),
              ('do_not_disturb', 'Do not disturb', Icons.do_not_disturb_on_outlined),
            ])
              PopupMenuButton<String>(
                tooltip: '$label on or off',
                onSelected: (v) => _run('mac.set_setting', {'key': key, 'value': v}),
                itemBuilder: (context) => const [
                  PopupMenuItem(value: 'on', child: Text('On')),
                  PopupMenuItem(value: 'off', child: Text('Off')),
                ],
                child: Chip(avatar: Icon(icon, size: 18), label: Text(label)),
              ),
          ]),
          if (_busy) const LinearProgressIndicator(minHeight: 2),
          const SizedBox(height: 4),
          Text(
            'Green actions run now. Typing, screenshots, files and WhatsApp wait for '
            'your approval — the Mac itself refuses anything not on its allowlist.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ]),
      ),
    );
  }
}


/// Control a paired phone from here (the Mac, or another phone). Settings panels,
/// the dialer, an SMS draft, an app link — each a typed action the phone verifies.
class _PhoneCard extends ConsumerStatefulWidget {
  const _PhoneCard({required this.device});

  final DeviceInfo device;

  @override
  ConsumerState<_PhoneCard> createState() => _PhoneCardState();
}

class _PhoneCardState extends ConsumerState<_PhoneCard> {
  bool _busy = false;

  Future<void> _run(String tool, [Map<String, dynamic> args = const {}]) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final result = await ref.read(clientProvider).runAction(
            tool,
            args: args,
            deviceId: widget.device.id,
          );
      if (!mounted) return;
      final status = result['status'] as String? ?? 'sent';
      final text = switch (status) {
        'queued' => 'Sent to ${widget.device.name} — it will run in a moment',
        'awaiting_approval' => 'Needs your approval — see Approvals',
        'denied' => 'Refused: ${result['reason']}',
        _ => status,
      };
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<Map<String, String>?> _ask(String title, List<(String, String)> fields) async {
    final controllers = {for (final f in fields) f.$1: TextEditingController()};
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          for (final f in fields)
            TextField(
              controller: controllers[f.$1],
              decoration: InputDecoration(labelText: f.$2),
              autofocus: f == fields.first,
            ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Send')),
        ],
      ),
    );
    if (ok != true) return null;
    return {for (final e in controllers.entries) e.key: e.value.text.trim()};
  }

  // A library of common deep links (FEATURES-50 #27); tapping one hands it to the OS.
  static const _presets = <(String, IconData, String)>[
    ('WhatsApp', Icons.chat, 'whatsapp://send'),
    ('Google Maps', Icons.map, 'https://maps.google.com'),
    ('Spotify', Icons.music_note, 'spotify:'),
    ('YouTube', Icons.play_circle, 'https://www.youtube.com'),
    ('Gmail', Icons.mail, 'https://mail.google.com'),
    ('Calendar', Icons.event, 'https://calendar.google.com'),
  ];

  void _openPresets(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const ListTile(title: Text('Open on the phone')),
          for (final (label, icon, url) in _presets)
            ListTile(
              leading: Icon(icon),
              title: Text(label),
              onTap: () {
                Navigator.pop(context);
                _run('phone.open_deeplink', {'url': url});
              },
            ),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final d = widget.device;
    return Card(
      margin: const EdgeInsets.all(12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.phone_android,
                color: d.online ? Colors.green.shade600 : Colors.orange.shade700),
            const SizedBox(width: 8),
            Expanded(child: Text(d.name, style: Theme.of(context).textTheme.titleMedium)),
            Text(d.online ? 'Online' : 'Offline — queued until it connects',
                style: Theme.of(context).textTheme.bodySmall),
            _RemoveButton(device: d),
          ]),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            PopupMenuButton<String>(
              tooltip: 'Open a settings panel',
              onSelected: (panel) => _run('phone.open_settings', {'panel': panel}),
              itemBuilder: (context) => const [
                PopupMenuItem(value: 'wifi', child: Text('Wi-Fi')),
                PopupMenuItem(value: 'bluetooth', child: Text('Bluetooth')),
                PopupMenuItem(value: 'display', child: Text('Display')),
                PopupMenuItem(value: 'sound', child: Text('Sound')),
                PopupMenuItem(value: 'battery', child: Text('Battery saver')),
                PopupMenuItem(value: 'location', child: Text('Location')),
                PopupMenuItem(value: 'dnd', child: Text('Do not disturb')),
                PopupMenuItem(value: 'airplane', child: Text('Airplane mode')),
              ],
              child: const Chip(avatar: Icon(Icons.settings, size: 18), label: Text('Settings')),
            ),
            ActionChip(
              avatar: const Icon(Icons.apps, size: 18),
              label: const Text('Presets'),
              onPressed: () => _openPresets(context),
            ),
            ActionChip(
              avatar: const Icon(Icons.tune, size: 18),
              label: const Text('Allowlist'),
              onPressed: () => editDeviceAllowlist(context, ref, widget.device),
            ),
            ActionChip(
              avatar: const Icon(Icons.notifications_active, size: 18),
              label: const Text('Ring'),
              onPressed: () => _run('phone.ring'),
            ),
            ActionChip(
              avatar: const Icon(Icons.my_location, size: 18),
              label: const Text('Locate'),
              onPressed: () => _run('phone.locate'),
            ),
            ActionChip(
              avatar: const Icon(Icons.call, size: 18),
              label: const Text('Call…'),
              onPressed: () async {
                final a = await _ask('Call someone', [('number', 'Number')]);
                if (a != null && a['number']!.isNotEmpty) _run('phone.call', a);
              },
            ),
            ActionChip(
              avatar: const Icon(Icons.sms_outlined, size: 18),
              label: const Text('SMS…'),
              onPressed: () async {
                final a = await _ask('Draft an SMS', [('number', 'Number'), ('text', 'Text')]);
                if (a != null && a['number']!.isNotEmpty) _run('phone.sms_draft', a);
              },
            ),
            ActionChip(
              avatar: const Icon(Icons.link, size: 18),
              label: const Text('Open link…'),
              onPressed: () async {
                final a = await _ask('Open a link', [('url', 'maps:, spotify:, upi:, https:…')]);
                if (a != null && a['url']!.isNotEmpty) _run('phone.open_deeplink', a);
              },
            ),
            ActionChip(
              avatar: const Icon(Icons.chat, size: 18),
              label: const Text('WhatsApp…'),
              onPressed: () async {
                final a = await _ask('Send a WhatsApp',
                    [('phone', 'Number'), ('text', 'Message')]);
                if (a != null && a['phone']!.isNotEmpty) _run('phone.whatsapp_send', a);
              },
            ),
            ActionChip(
              avatar: const Icon(Icons.content_paste_go, size: 18),
              label: const Text('Send my clipboard'),
              onPressed: () async {
                final data = await Clipboard.getData(Clipboard.kTextPlain);
                final text = data?.text ?? '';
                if (text.isEmpty) return;
                _run('phone.clipboard_write', {'text': text});
              },
            ),
            ActionChip(
              avatar: const Icon(Icons.notifications_none, size: 18),
              label: const Text('Ping'),
              onPressed: () =>
                  _run('phone.notify', {'title': 'JARVIS', 'body': 'Hello from your Mac'}),
            ),
          ]),
          if (_busy) const LinearProgressIndicator(minHeight: 2),
          const SizedBox(height: 4),
          Text(
            'Nothing here sends by itself: the dialer, SMS and WhatsApp open filled in and '
            'wait for a thumb. A call needs your approval first.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ]),
      ),
    );
  }
}


/// This device as an execution node: pair once, then it answers signed jobs —
/// "open WhatsApp and draft this", "open this link" — while the app is open.
class _ThisPhoneCard extends ConsumerStatefulWidget {
  const _ThisPhoneCard();

  @override
  ConsumerState<_ThisPhoneCard> createState() => _ThisPhoneCardState();
}

class _ThisPhoneCardState extends ConsumerState<_ThisPhoneCard> {
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _restore());
  }

  Future<void> _restore() async {
    final storage = ref.read(secureStorageProvider);
    final node = ref.read(phoneNodeProvider).node;
    if (node.paired) return;
    node.restore(
      scalarHex: await storage.read(key: 'phone_node_scalar'),
      deviceId: await storage.read(key: 'phone_node_device_id'),
      serverPublicPem: await storage.read(key: 'phone_node_server_pem'),
    );
  }

  Future<void> _pair() async {
    setState(() => _busy = true);
    final client = ref.read(clientProvider);
    final node = ref.read(phoneNodeProvider).node;
    final storage = ref.read(secureStorageProvider);
    try {
      await node.pair(
        begin: client.beginPairing,
        complete: client.completePairing,
        serverKey: client.serverPublicKey,
        name: !kIsWeb && defaultTargetPlatform == TargetPlatform.macOS ? 'This Mac' : 'This phone',
        platform: kIsWeb
            ? 'web'
            : switch (defaultTargetPlatform) {
                TargetPlatform.android => 'android',
                TargetPlatform.iOS => 'ios',
                TargetPlatform.macOS => 'macos',
                _ => 'other',
              },
      );
      await storage.write(key: 'phone_node_scalar', value: node.scalarHex);
      await storage.write(key: 'phone_node_device_id', value: node.deviceId);
      await storage.write(key: 'phone_node_server_pem', value: node.serverPublicPem);
      ref.invalidate(devicesProvider);
    } on ProblemException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Pairing failed: $e')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _connect(PhoneNode node) async {
    final client = ref.read(clientProvider);
    final token = client.accessToken;
    if (token == null) return;
    try {
      await node.connect(wsBase: client.wsBase, accessToken: token);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Connect failed: $e')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final node = ref.watch(phoneNodeProvider).node;
    final status = !node.paired
        ? 'Not paired'
        : node.stopped
            ? 'Stopped'
            : node.connected
                ? 'Connected — answering signed jobs'
                : 'Paired — reconnecting…';
    return Card(
      margin: const EdgeInsets.all(12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.smartphone,
                color: node.connected && !node.stopped
                    ? Colors.green.shade600
                    : Theme.of(context).disabledColor),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                !kIsWeb && defaultTargetPlatform == TargetPlatform.macOS ? 'This Mac' : 'This phone',
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            Text(status, style: Theme.of(context).textTheme.bodySmall),
          ]),
          const SizedBox(height: 10),
          Wrap(spacing: 8, children: [
            if (!node.paired)
              FilledButton.icon(
                key: const Key('pair-phone'),
                onPressed: _busy ? null : _pair,
                icon: const Icon(Icons.link),
                label: const Text('Pair this phone'),
              ),
            if (node.paired && !node.connected)
              FilledButton.tonalIcon(
                onPressed: () => _connect(node),
                icon: const Icon(Icons.power),
                label: const Text('Connect'),
              ),
            if (node.connected)
              OutlinedButton.icon(
                onPressed: node.disconnect,
                icon: const Icon(Icons.power_off),
                label: const Text('Disconnect'),
              ),
            if (node.paired)
              OutlinedButton.icon(
                onPressed: node.stopped ? node.resume : node.stop,
                icon: Icon(node.stopped ? Icons.play_arrow : Icons.stop_circle_outlined),
                label: Text(node.stopped ? 'Resume' : 'STOP'),
              ),
          ]),
          const SizedBox(height: 6),
          Text(
            !kIsWeb && defaultTargetPlatform == TargetPlatform.macOS
                ? 'This app is the Mac\'s hand: screenshots, links, apps, clipboard, volume, '
                    'settings, speech. Every job is signed by the server and checked here; '
                    'STOP outranks any signature.'
                : 'Jarvis can open links and apps here and draft WhatsApp messages for you to '
                    'send. Every job is signed by the server and checked on this phone; STOP '
                    'outranks any signature.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (node.log.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('Last: ${node.log.first['status']} ${node.log.first['job_id']}',
                style: Theme.of(context).textTheme.labelSmall),
          ],
          if (NotificationMirror.supported && node.paired) ...[
            const Divider(height: 24),
            Consumer(builder: (context, ref, _) {
              final mirror = ref.watch(notificationMirrorProvider);
              return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                SwitchListTile.adaptive(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Mirror notifications'),
                  subtitle: Text(mirror.accessGranted
                      ? 'Titles from WhatsApp, Gmail, Slack, Messages, Phone and Calendar '
                          'become events routines can react to. Forwarded: ${mirror.forwarded}'
                      : 'Grant notification access, then switch this on'),
                  value: mirror.on,
                  onChanged: (v) async {
                    if (v && !mirror.accessGranted) {
                      await mirror.openSettings();
                      await mirror.refreshAccess();
                    }
                    await mirror.setOn(v);
                  },
                ),
                if (mirror.on)
                  SwitchListTile.adaptive(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    title: const Text('Include message text'),
                    subtitle: const Text('Off: titles only. On: the body too, for those apps.'),
                    value: mirror.withText,
                    onChanged: mirror.setWithText,
                  ),
                if (mirror.lastError != null)
                  Text(mirror.lastError!, style: const TextStyle(color: JarvisColors.danger)),
              ]);
            }),
            Consumer(builder: (context, ref, _) {
              final sampler = ref.watch(activitySamplerProvider);
              return SwitchListTile.adaptive(
                contentPadding: EdgeInsets.zero,
                title: const Text('Share which app is in front'),
                subtitle: Text(sampler.accessGranted
                    ? 'App names only, once a minute, kept 30 days. Powers the focus guard '
                        'and "what was I doing". Posted: ${sampler.posted}'
                    : 'Grant usage access, then switch this on'),
                value: sampler.on,
                onChanged: (v) async {
                  if (v && !sampler.accessGranted) {
                    await sampler.openSettings();
                    await sampler.refreshAccess();
                  }
                  await sampler.setOn(v);
                },
              );
            }),
          ],
        ]),
      ),
    );
  }
}


class _RemoveButton extends ConsumerWidget {
  const _RemoveButton({required this.device});
  final DeviceInfo device;

  @override
  Widget build(BuildContext context, WidgetRef ref) => IconButton(
        tooltip: 'Remove this device',
        icon: const Icon(Icons.delete_outline, size: 20),
        onPressed: () async {
          final sure = await showDialog<bool>(
            context: context,
            builder: (context) => AlertDialog(
              title: Text('Remove ${device.name}?'),
              content: const Text('It stops answering jobs until it pairs again.'),
              actions: [
                TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep')),
                FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Remove')),
              ],
            ),
          );
          if (sure != true) return;
          try {
            await ref.read(clientProvider).revokeDevice(device.id);
            ref.invalidate(devicesProvider);
          } on ProblemException catch (e) {
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
            }
          }
        },
      );
}

/// A clipboard shared across your devices (FEATURES-50 #26). Push this device's
/// clipboard to the account; pull it on another. Text only, through the one account.
class _ClipboardCard extends ConsumerStatefulWidget {
  const _ClipboardCard();

  @override
  ConsumerState<_ClipboardCard> createState() => _ClipboardCardState();
}

class _ClipboardCardState extends ConsumerState<_ClipboardCard> {
  String? _synced;
  String? _when;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final r = await ref.read(clientProvider).getClipboard();
      if (mounted) {
        setState(() {
          _synced = r['text'] as String?;
          _when = r['device'] as String?;
        });
      }
    } on ProblemException {
      // nothing synced yet
    }
  }

  Future<void> _push() async {
    setState(() => _busy = true);
    try {
      final data = await Clipboard.getData(Clipboard.kTextPlain);
      final text = data?.text ?? '';
      if (text.isEmpty) {
        if (mounted) {
          ScaffoldMessenger.of(context)
              .showSnackBar(const SnackBar(content: Text('Clipboard is empty')));
        }
        return;
      }
      await ref.read(clientProvider).setClipboard(text, device: _platform());
      await _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('Clipboard pushed')));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pull() async {
    if (_synced == null) return;
    await Clipboard.setData(ClipboardData(text: _synced!));
    if (mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Copied to this device')));
    }
  }

  String _platform() {
    if (kIsWeb) return 'Web';
    return defaultTargetPlatform == TargetPlatform.macOS ? 'Mac' : 'Phone';
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.content_paste, size: 20),
            const SizedBox(width: 8),
            Text('Shared clipboard', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 6),
          Text(
            _synced == null
                ? 'Nothing shared yet.'
                : '“${_synced!.length > 80 ? '${_synced!.substring(0, 80)}…' : _synced!}”'
                    '${_when != null ? '  · from $_when' : ''}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 8),
          Row(children: [
            FilledButton.tonalIcon(
              onPressed: _busy ? null : _push,
              icon: const Icon(Icons.upload, size: 18),
              label: const Text('Push'),
            ),
            const SizedBox(width: 8),
            OutlinedButton.icon(
              onPressed: _synced == null ? null : _pull,
              icon: const Icon(Icons.download, size: 18),
              label: const Text('Copy here'),
            ),
          ]),
        ]),
      ),
    );
  }
}

/// Edit what a device is allowed to do (FEATURES-50 #30). The server gate honours these,
/// so unchecking a capability stops those jobs from being dispatched to the device.
Future<void> editDeviceAllowlist(
    BuildContext context, WidgetRef ref, DeviceInfo device) async {
  // The universe of capabilities per platform, unioned with whatever the device reports.
  const phoneCaps = [
    'phone.open_url', 'phone.open_deeplink', 'phone.open_app', 'phone.open_settings',
    'phone.clipboard_read', 'phone.clipboard_write', 'phone.notify', 'phone.call',
    'phone.ring', 'phone.locate', 'phone.whatsapp_send', 'phone.torch', 'phone.system_info',
  ];
  const macCaps = [
    'mac.capture_screen', 'mac.describe_screen', 'mac.open_app', 'mac.open_url',
    'mac.clipboard_read', 'mac.clipboard_write', 'mac.ring', 'mac.lock_screen',
    'mac.volume', 'mac.whatsapp_send', 'mac.type',
  ];
  final universe = <String>{
    ...(device.platform == 'macos' ? macCaps : phoneCaps),
    ...device.capabilities,
  }.toList()
    ..sort();
  final selected = device.capabilities.toSet();

  final saved = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    builder: (context) => StatefulBuilder(
      builder: (context, setSheet) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.7,
        builder: (context, controller) => ListView(
          controller: controller,
          children: [
            ListTile(
              title: Text('${device.name} — allowlist',
                  style: Theme.of(context).textTheme.titleMedium),
              subtitle: const Text('Only checked capabilities can run on this device'),
            ),
            for (final cap in universe)
              CheckboxListTile(
                dense: true,
                value: selected.contains(cap),
                title: Text(cap),
                onChanged: (v) => setSheet(() {
                  if (v == true) {
                    selected.add(cap);
                  } else {
                    selected.remove(cap);
                  }
                }),
              ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Save'),
              ),
            ),
          ],
        ),
      ),
    ),
  );
  if (saved != true) return;
  try {
    await ref.read(clientProvider).editAllowlist(device.id, capabilities: selected.toList());
    ref.invalidate(devicesProvider);
    if (context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Allowlist updated')));
    }
  } on ProblemException catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }
}
