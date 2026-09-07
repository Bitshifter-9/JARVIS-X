import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';

/// ⌘K on the Mac: a searchable list of everything you can do, one keystroke away.
class CommandPalette extends ConsumerStatefulWidget {
  const CommandPalette({super.key});

  static Future<void> open(BuildContext context) => showDialog<void>(
        context: context,
        barrierColor: Colors.black54,
        builder: (_) => const CommandPalette(),
      );

  @override
  ConsumerState<CommandPalette> createState() => _CommandPaletteState();
}

class _Command {
  const _Command(this.label, this.icon, this.run, {this.keywords = ''});
  final String label;
  final IconData icon;
  final void Function(WidgetRef ref, BuildContext context) run;
  final String keywords;
}

class _CommandPaletteState extends ConsumerState<CommandPalette> {
  final _search = TextEditingController();

  List<_Command> get _all => [
        _Command('Go to Home', Icons.dashboard, (ref, _) => _tab(ref, 1)),
        _Command('New chat', Icons.add_comment, (ref, _) => _tab(ref, 0), keywords: 'jarvis ask'),
        _Command('Routines', Icons.schedule, (ref, _) => _tab(ref, 2)),
        _Command('Goals & deadlines', Icons.flag, (ref, _) => _tab(ref, 3), keywords: 'agenda'),
        _Command('Approvals', Icons.verified_user, (ref, _) => _tab(ref, 4)),
        _Command('Timeline', Icons.timeline, (ref, _) => _tab(ref, 5)),
        _Command('Connections', Icons.hub, (ref, _) => _tab(ref, 6)),
        _Command('Train Jarvis', Icons.school, (ref, _) => _tab(ref, 7)),
        _Command('Devices', Icons.devices, (ref, _) => _tab(ref, 9)),
        _Command('Settings', Icons.settings, (ref, _) => _tab(ref, 10)),
        _Command('Ask: what is due today?', Icons.auto_awesome,
            (ref, _) => _ask(ref, 'What is due today?'), keywords: 'deadline'),
        _Command('Brief me now', Icons.wb_sunny_outlined,
            (ref, _) => _ask(ref, 'Give me my briefing now')),
        _Command('Screenshot my Mac', Icons.screenshot_monitor,
            (ref, c) => _run(ref, c, 'mac.capture_screen')),
        _Command('Ring my phone', Icons.notifications_active,
            (ref, c) => _run(ref, c, 'phone.ring')),
        _Command('Where is my phone?', Icons.my_location,
            (ref, c) => _run(ref, c, 'phone.locate')),
        _Command('Scan my mail now', Icons.mark_email_read_outlined,
            (ref, c) => _sync(ref, c)),
      ];

  void _tab(WidgetRef ref, int i) {
    ref.read(homeTabProvider.notifier).state = i;
    Navigator.pop(context);
  }

  void _ask(WidgetRef ref, String text) {
    ref.read(chatPrefillProvider.notifier).state = text;
    ref.read(homeTabProvider.notifier).state = 0;
    Navigator.pop(context);
  }

  Future<void> _run(WidgetRef ref, BuildContext c, String tool) async {
    Navigator.pop(context);
    try {
      await ref.read(clientProvider).runAction(tool);
      if (c.mounted) {
        ScaffoldMessenger.of(c).showSnackBar(SnackBar(content: Text('Running $tool…')));
      }
    } on ProblemException catch (e) {
      if (c.mounted) ScaffoldMessenger.of(c).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _sync(WidgetRef ref, BuildContext c) async {
    Navigator.pop(context);
    try {
      await ref.read(clientProvider).syncAllConnectors();
      if (c.mounted) {
        ScaffoldMessenger.of(c).showSnackBar(const SnackBar(content: Text('Scanning your mail…')));
      }
    } on ProblemException catch (e) {
      if (c.mounted) ScaffoldMessenger.of(c).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final q = _search.text.trim().toLowerCase();
    final shown = q.isEmpty
        ? _all
        : _all
            .where((c) =>
                c.label.toLowerCase().contains(q) || c.keywords.contains(q))
            .toList();
    return Dialog(
      alignment: Alignment.topCenter,
      insetPadding: const EdgeInsets.only(top: 80, left: 40, right: 40),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560, maxHeight: 480),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextField(
              controller: _search,
              autofocus: true,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                hintText: 'Type a command…',
                prefixIcon: Icon(Icons.search),
                border: InputBorder.none,
              ),
            ),
          ),
          const Divider(height: 1),
          Flexible(
            child: ListView.builder(
              shrinkWrap: true,
              itemCount: shown.length,
              itemBuilder: (context, i) => ListTile(
                leading: Icon(shown[i].icon),
                title: Text(shown[i].label),
                onTap: () => shown[i].run(ref, context),
              ),
            ),
          ),
        ]),
      ),
    );
  }
}
