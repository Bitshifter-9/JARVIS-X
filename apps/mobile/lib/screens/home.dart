import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/orb.dart';
import 'approvals.dart';
import 'chat.dart';
import 'command_palette.dart';
import 'connections.dart';
import 'devices.dart';
import 'goals.dart';
import 'hud.dart';
import 'routines.dart';
import 'settings.dart';
import 'timeline.dart';
import 'train.dart';
import 'videos.dart';

/// The shell. Wide windows (the Mac) get a rail with every destination; phones get a
/// five-item bar and a "More" sheet. Screens live in an IndexedStack so switching tabs
/// never loses a conversation or a paired-phone connection.
class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _Destination {
  const _Destination(this.label, this.icon, this.selectedIcon, this.screen);
  final String label;
  final IconData icon;
  final IconData selectedIcon;
  final Widget screen;
}

class _HomeScreenState extends ConsumerState<HomeScreen> with WidgetsBindingObserver {
  int get _index => ref.watch(homeTabProvider);
  set _index(int value) => ref.read(homeTabProvider.notifier).state = value;
  Timer? _refresh;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Screens live in an IndexedStack, so their providers never dispose on their own:
    // the visible tab is refreshed on every switch, every 45 s, and when the app
    // comes back to the front.
    _refresh = Timer.periodic(const Duration(seconds: 45), (_) => _refreshTab(_current));
  }

  @override
  void dispose() {
    _refresh?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refreshTab(_current);
  }

  int get _current => ref.read(homeTabProvider);

  void _refreshTab(int i) {
    final List<ProviderOrFamily> providers = switch (_all[i].label) {
      'Home' => <ProviderOrFamily>[hudProvider, tasksProvider, focusProvider],
      'Routines' => <ProviderOrFamily>[routinesProvider],
      'Goals' => <ProviderOrFamily>[goalsProvider, tasksProvider, allTasksProvider],
      'Approvals' => <ProviderOrFamily>[approvalsProvider, permissionsProvider],
      'Timeline' => <ProviderOrFamily>[timelineProvider],
      'Train' => <ProviderOrFamily>[suggestionsProvider],
      'Videos' => <ProviderOrFamily>[videoRunsProvider],
      'Devices' => <ProviderOrFamily>[devicesProvider],
      'Settings' => <ProviderOrFamily>[settingsProvider],
      _ => const <ProviderOrFamily>[],
    };
    for (final p in providers) {
      ref.invalidate(p);
    }
  }

  static const _all = [
    _Destination('Jarvis', Icons.graphic_eq, Icons.graphic_eq, ChatScreen()),
    _Destination('Home', Icons.dashboard_outlined, Icons.dashboard, HudScreen()),
    _Destination('Routines', Icons.schedule_outlined, Icons.schedule, RoutinesScreen()),
    _Destination('Goals', Icons.flag_outlined, Icons.flag, GoalsScreen()),
    _Destination('Approvals', Icons.verified_user_outlined, Icons.verified_user, ApprovalsScreen()),
    _Destination('Timeline', Icons.timeline_outlined, Icons.timeline, TimelineScreen()),
    _Destination('Connections', Icons.hub_outlined, Icons.hub, ConnectionsScreen()),
    _Destination('Train', Icons.school_outlined, Icons.school, TrainScreen()),
    _Destination('Videos', Icons.movie_outlined, Icons.movie, VideosScreen()),
    _Destination('Devices', Icons.devices_outlined, Icons.devices, DevicesScreen()),
    _Destination('Settings', Icons.settings_outlined, Icons.settings, SettingsScreen()),
  ];

  // The phone bar shows four destinations and "More"; the rest live in the sheet.
  static const _barIndices = [0, 1, 3, 4];

  static const _approvalsIndex = 4;

  @override
  Widget build(BuildContext context) {
    ref.listen(homeTabProvider, (previous, next) {
      if (previous != next) _refreshTab(next);
    });
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final pending = (ref.watch(approvalsProvider).valueOrNull ?? const [])
        .where((a) => a.isPending)
        .length;
    // A short fade and lift on every switch, so a tab change reads as a scene change
    // while the IndexedStack underneath keeps every screen's state.
    final body = TweenAnimationBuilder<double>(
      key: ValueKey(_index),
      tween: Tween(begin: 0, end: 1),
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      child: IndexedStack(index: _index, children: [for (final d in _all) d.screen]),
      builder: (context, v, child) => Opacity(
        opacity: v,
        child: Transform.translate(offset: Offset(0, (1 - v) * 10), child: child),
      ),
    );

    if (wide) {
      // ⌘1…⌘0 jump between tabs; ⌘K goes to Jarvis. Developers live on the keyboard.
      final bindings = <ShortcutActivator, VoidCallback>{
        const SingleActivator(LogicalKeyboardKey.keyK, meta: true): () =>
            CommandPalette.open(context),
        for (final (i, key) in const [
          LogicalKeyboardKey.digit1, LogicalKeyboardKey.digit2, LogicalKeyboardKey.digit3,
          LogicalKeyboardKey.digit4, LogicalKeyboardKey.digit5, LogicalKeyboardKey.digit6,
          LogicalKeyboardKey.digit7, LogicalKeyboardKey.digit8, LogicalKeyboardKey.digit9,
          LogicalKeyboardKey.digit0,
        ].indexed)
          SingleActivator(key, meta: true): () {
            if (i < _all.length) _index = i;
          },
      };
      return CallbackShortcuts(
        bindings: bindings,
        child: Focus(
          autofocus: true,
          child: Scaffold(
        body: Row(children: [
          _Rail(
            index: _index,
            extended: MediaQuery.sizeOf(context).width >= 1180,
            badges: {_approvalsIndex: pending},
            onSelect: (i) => _index = i,
            onPause: _confirmPause,
            onSignOut: () => ref.read(authProvider.notifier).signOut(),
          ),
          const VerticalDivider(width: 1),
          Expanded(
            child: Scaffold(
              floatingActionButton: _QuickCaptureButton(),
              body: Column(children: [
                _Header(title: _all[_index].label),
                Expanded(child: body),
              ]),
            ),
          ),
        ]),
          ),
        ),
      );
    }

    final barIndex = _barIndices.indexOf(_index);
    return Scaffold(
      appBar: AppBar(
        title: Text(_all[_index].label),
        actions: [
          IconButton(
            key: const Key('killSwitch'),
            tooltip: 'Pause JARVIS',
            icon: const Icon(Icons.stop_circle_outlined),
            onPressed: _confirmPause,
          ),
          IconButton(
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(authProvider.notifier).signOut(),
          ),
        ],
      ),
      floatingActionButton: _index == 0 ? null : _QuickCaptureButton(),
      body: body,
      bottomNavigationBar: NavigationBar(
        selectedIndex: barIndex == -1 ? 4 : barIndex,
        onDestinationSelected: (i) {
          if (i == 4) {
            _more();
          } else {
            _index = _barIndices[i];
          }
        },
        destinations: [
          for (final i in _barIndices)
            NavigationDestination(
              icon: _badged(Icon(_all[i].icon), i == _approvalsIndex ? pending : 0),
              selectedIcon:
                  _badged(Icon(_all[i].selectedIcon), i == _approvalsIndex ? pending : 0),
              label: _all[i].label,
            ),
          NavigationDestination(
            icon: _badged(const Icon(Icons.grid_view_rounded),
                _barIndices.contains(_approvalsIndex) ? 0 : pending),
            label: 'More',
          ),
        ],
      ),
    );
  }

  static Widget _badged(Widget icon, int count) => count == 0
      ? icon
      : Badge.count(count: count, backgroundColor: JarvisColors.warning, child: icon);

  Future<void> _more() async {
    final picked = await showModalBottomSheet<int>(
      context: context,
      builder: (context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
          child: GridView.count(
            crossAxisCount: 4,
            shrinkWrap: true,
            mainAxisSpacing: 8,
            crossAxisSpacing: 8,
            children: [
              for (var i = 0; i < _all.length; i++)
                if (!_barIndices.contains(i))
                  InkWell(
                    borderRadius: BorderRadius.circular(16),
                    onTap: () => Navigator.pop(context, i),
                    child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                      Container(
                        width: 48,
                        height: 48,
                        decoration: BoxDecoration(
                          color: _index == i
                              ? Theme.of(context).colorScheme.primary.withValues(alpha: 0.16)
                              : context.surfaces.surface3,
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Icon(_all[i].icon, color: Theme.of(context).colorScheme.primary),
                      ),
                      const SizedBox(height: 6),
                      Text(_all[i].label, style: Theme.of(context).textTheme.labelMedium),
                    ]),
                  ),
            ],
          ),
        ),
      ),
    );
    if (picked != null && mounted) _index = picked;
  }

  Future<void> _confirmPause() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Pause JARVIS?'),
        content: const Text(
          'Queued work is cancelled and every device session is revoked. '
          'Evidence is never deleted.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Pause')),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final result = await ref.read(clientProvider).pause();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          'Paused. ${result['jobs_cancelled']} job(s) cancelled, '
          '${result['sessions_revoked']} session(s) revoked.',
        ),
      ),
    );
  }
}

class _Rail extends StatelessWidget {
  const _Rail({
    required this.index,
    required this.extended,
    required this.onSelect,
    required this.onPause,
    required this.onSignOut,
    this.badges = const {},
  });

  final int index;
  final bool extended;
  final Map<int, int> badges;
  final ValueChanged<int> onSelect;
  final VoidCallback onPause;
  final VoidCallback onSignOut;

  @override
  Widget build(BuildContext context) {
    return NavigationRail(
      extended: extended,
      minExtendedWidth: 220,
      selectedIndex: index,
      onDestinationSelected: onSelect,
      labelType: extended ? NavigationRailLabelType.none : NavigationRailLabelType.all,
      leading: Padding(
        padding: const EdgeInsets.fromLTRB(8, 16, 8, 8),
        child: extended
            ? Row(mainAxisSize: MainAxisSize.min, children: [
                const JarvisOrb(state: OrbState.idle, size: 40),
                const SizedBox(width: 10),
                Text('JARVIS X', style: Theme.of(context).textTheme.titleLarge),
              ])
            : const JarvisOrb(state: OrbState.idle, size: 36),
      ),
      trailing: Expanded(
        child: Align(
          alignment: Alignment.bottomCenter,
          child: Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              IconButton(
                key: const Key('killSwitch'),
                tooltip: 'Pause JARVIS',
                icon: const Icon(Icons.stop_circle_outlined),
                onPressed: onPause,
              ),
              IconButton(
                tooltip: 'Sign out',
                icon: const Icon(Icons.logout),
                onPressed: onSignOut,
              ),
            ]),
          ),
        ),
      ),
      destinations: [
        for (final (i, d) in _HomeScreenState._all.indexed)
          NavigationRailDestination(
            icon: _HomeScreenState._badged(Icon(d.icon), badges[i] ?? 0),
            selectedIcon: _HomeScreenState._badged(Icon(d.selectedIcon), badges[i] ?? 0),
            label: Text(d.label),
          ),
      ],
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.title});

  final String title;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(24, 20, 24, 4),
        child: Align(
          alignment: Alignment.centerLeft,
          child: AnimatedSwitcher(
            duration: const Duration(milliseconds: 220),
            child: Text(title,
                key: ValueKey(title), style: Theme.of(context).textTheme.headlineSmall),
          ),
        ),
      );
}


/// One button, anywhere: capture a thought to Jarvis or add a deadline, without
/// hunting for the right screen.
class _QuickCaptureButton extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return FloatingActionButton(
      heroTag: 'quick-capture',
      tooltip: 'Quick capture',
      onPressed: () => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (context) => Padding(
          padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
          child: const _QuickCaptureSheet(),
        ),
      ),
      child: const Icon(Icons.add),
    );
  }
}

class _QuickCaptureSheet extends ConsumerStatefulWidget {
  const _QuickCaptureSheet();

  @override
  ConsumerState<_QuickCaptureSheet> createState() => _QuickCaptureSheetState();
}

class _QuickCaptureSheetState extends ConsumerState<_QuickCaptureSheet> {
  final _text = TextEditingController();

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  void _ask() {
    final t = _text.text.trim();
    if (t.isEmpty) return;
    Navigator.pop(context);
    ref.read(chatPrefillProvider.notifier).state = t;
    ref.read(homeTabProvider.notifier).state = 0;
  }

  Future<void> _addDeadline() async {
    final title = _text.text.trim();
    // A line with a day or time word ("rent friday 6pm") is parsed straight away.
    final looksDated = RegExp(
      r'\b(today|tonight|tomorrow|mon|tue|wed|thu|fri|sat|sun|\d{1,2}\s*(am|pm)|:\d2|'
      r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b',
      caseSensitive: false,
    ).hasMatch(title);
    if (looksDated && title.isNotEmpty) {
      Navigator.pop(context);
      try {
        final task = await ref.read(clientProvider).quickAdd(title);
        ref.invalidate(tasksProvider);
        ref.invalidate(hudProvider);
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(task.dueAt == null
                  ? 'Added "${task.title}"'
                  : 'Added — due ${task.dueAt}')));
        }
      } on ProblemException catch (e) {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
      return;
    }
    Navigator.pop(context);
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now.add(const Duration(days: 1)),
      firstDate: now,
      lastDate: now.add(const Duration(days: 365)),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
        context: context, initialTime: const TimeOfDay(hour: 9, minute: 0));
    if (time == null || !mounted) return;
    final due = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    await ref.read(clientProvider).createTask(
        title.isEmpty ? 'Deadline' : title, dueAt: due);
    ref.invalidate(tasksProvider);
    ref.invalidate(hudProvider);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Deadline added')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('Quick capture', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 12),
        TextField(
          controller: _text,
          autofocus: true,
          minLines: 1,
          maxLines: 4,
          textInputAction: TextInputAction.send,
          onSubmitted: (_) => _ask(),
          decoration: const InputDecoration(
              hintText: 'A thought, a question, or a deadline title'),
        ),
        const SizedBox(height: 14),
        Row(children: [
          Expanded(
            child: OutlinedButton.icon(
              onPressed: _addDeadline,
              icon: const Icon(Icons.event_available_outlined),
              label: const Text('Add deadline'),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: FilledButton.icon(
              onPressed: _ask,
              icon: const Icon(Icons.auto_awesome),
              label: const Text('Ask Jarvis'),
            ),
          ),
        ]),
      ]),
    );
  }
}