import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/ambient.dart';
import '../widgets/orb.dart';
import '../widgets/states.dart';

/// The home HUD: the orb reacting to what the workers are doing right now, every
/// number that matters in one glance, and a ticker of the last things Jarvis did.
/// Fed by `/v1/hud` (one call) and `/v1/live` (a stream), so nothing here is stale.
class HudScreen extends ConsumerStatefulWidget {
  const HudScreen({super.key});

  /// The index of the Routines destination in the shell (kept in one place).
  static const routinesTab = 2;

  @override
  ConsumerState<HudScreen> createState() => _HudScreenState();
}

class _HudScreenState extends ConsumerState<HudScreen> {
  final List<Map<String, dynamic>> _ticker = [];
  OrbState _orb = OrbState.idle;
  Timer? _settle;
  Timer? _refresh;

  @override
  void initState() {
    super.initState();
    // The tiles re-count every minute; the ticker is live.
    _refresh = Timer.periodic(const Duration(seconds: 60), (_) => ref.invalidate(hudProvider));
  }

  @override
  void dispose() {
    _settle?.cancel();
    _refresh?.cancel();
    super.dispose();
  }

  void _onLive(Map<String, dynamic> event) {
    final kind = event['kind'] as String?;
    if (kind == null || kind == 'hello' || kind == 'ping') return;
    setState(() {
      _ticker.insert(0, event);
      if (_ticker.length > 30) _ticker.removeLast();
      final status = event['status'] as String?;
      _orb = switch ((kind, status)) {
        ('run', 'running') || ('action', 'dispatched') || ('action', 'proposed') =>
          OrbState.thinking,
        ('run', _) || ('action', 'verified') || ('event', _) => OrbState.speaking,
        _ => OrbState.thinking,
      };
    });
    _settle?.cancel();
    _settle = Timer(const Duration(seconds: 4), () {
      if (mounted) setState(() => _orb = OrbState.idle);
    });
    // A new action or run changes the counts too.
    ref.invalidate(hudProvider);
    ref.invalidate(tasksProvider);
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(liveProvider, (_, next) {
      next.whenData(_onLive);
    });
    final hud = ref.watch(hudProvider);
    final wide = MediaQuery.sizeOf(context).width >= 900;

    return AmbientBackground(
      intensity: _orb == OrbState.idle ? 0.7 : 1.2,
      child: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(hudProvider);
          ref.invalidate(digestProvider);
        },
        child: hud.when(
          loading: () => const SkeletonList(rows: 5, height: 96),
          error: (e, _) => ErrorState(message: '$e', onRetry: () => ref.invalidate(hudProvider)),
          data: (body) => _body(context, body, wide),
        ),
      ),
    );
  }

  Widget _body(BuildContext context, Map<String, dynamic> body, bool wide) {
    final tiles = (body['tiles'] as Map<String, dynamic>? ?? const {});
    final recent = ((body['recent'] as List<dynamic>?) ?? const [])
        .cast<Map<String, dynamic>>()
        .map(TimelineEntry.fromJson)
        .toList();
    final live = _ticker.map(_LiveLine.fromEvent).toList();
    final lines = [...live, ...recent.map(_LiveLine.fromTimeline)].take(14).toList();
    final digest = ref.watch(digestProvider).valueOrNull ?? const [];

    final left = <Widget>[
      _Hero(greeting: body['greeting'] as String? ?? 'Hello.', orb: _orb).enter(0),
      const _OnboardingNudge(),
      const SizedBox(height: 12),
      if (digest.isNotEmpty) ...[
        _Panel(title: 'What matters now', child: _Digest(items: digest)).enter(0),
        const SizedBox(height: 14),
      ],
      const _AskBar().enter(0),
      const _FocusCard(),
      const SizedBox(height: 14),
      _Tiles(tiles: tiles, wide: wide).enter(1),
      const SizedBox(height: 14),
      _QuickActions(routines: (body['routines'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>())
          .enter(2),
    ];
    final tasks = (ref.watch(tasksProvider).valueOrNull ?? const [])
        .where((t) => t.dueAt != null)
        .take(5)
        .toList();
    final right = <Widget>[
      _Panel(
        title: 'Deadlines',
        trailing: TextButton(
          onPressed: () => ref.read(homeTabProvider.notifier).state = 3,
          child: const Text('All'),
        ),
        child: tasks.isEmpty
            ? const _Muted('Nothing dated yet. Scanned mail with a date lands here.')
            : Column(children: [
                for (final t in tasks)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Row(children: [
                      Icon(
                        t.sourceProvider == 'gmail' ? Icons.mail_outline : Icons.flag_outlined,
                        size: 16,
                        color: t.dueAt!.isBefore(DateTime.now())
                            ? JarvisColors.danger
                            : JarvisColors.sky,
                      ),
                      const SizedBox(width: 10),
                      Expanded(child: Text(t.title, maxLines: 1, overflow: TextOverflow.ellipsis)),
                      Text(DateFormat('EEE HH:mm').format(t.dueAt!),
                          style: Theme.of(context).textTheme.labelSmall),
                    ]),
                  ),
              ]),
      ).enter(2),
      const SizedBox(height: 14),
      _Panel(
        title: 'Live',
        trailing: _Pulse(active: _orb != OrbState.idle),
        child: lines.isEmpty
            ? const _Muted('Nothing yet. Ask Jarvis for something and watch it happen here.')
            : Column(children: [for (final (i, l) in lines.indexed) _TickerRow(line: l, index: i)]),
      ).enter(3),
      const SizedBox(height: 14),
      _Panel(
        title: 'Scans',
        child: _Scans(scans: (body['scans'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>()),
      ).enter(4),
      const SizedBox(height: 14),
      _Panel(
        title: 'Routines',
        trailing: TextButton(
          onPressed: () => ref.read(homeTabProvider.notifier).state = HudScreen.routinesTab,
          child: const Text('All'),
        ),
        child: _Routines(
            routines: (body['routines'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>()),
      ).enter(5),
      const SizedBox(height: 14),
      _Panel(
        title: 'System',
        child: _System(
          workers: (body['workers'] as Map<String, dynamic>? ?? const {}),
          devices: (body['devices'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>(),
        ),
      ).enter(6),
    ];

    if (wide) {
      return ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(flex: 5, child: Column(children: left)),
            const SizedBox(width: 16),
            Expanded(flex: 4, child: Column(children: right)),
          ]),
        ],
      );
    }
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
      children: [...left, const SizedBox(height: 14), ...right],
    );
  }

}

// ── pieces ────────────────────────────────────────────────────────────
class _Hero extends StatelessWidget {
  const _Hero({required this.greeting, required this.orb});

  final String greeting;
  final OrbState orb;

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final label = switch (orb) {
      OrbState.idle => 'Standing by',
      OrbState.listening => 'Listening',
      OrbState.thinking => 'Working',
      OrbState.speaking => 'Done',
    };
    return Glass(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
      child: Row(children: [
        JarvisOrb(state: orb, size: 84),
        const SizedBox(width: 18),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(greeting, style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 4),
            Text(DateFormat('EEEE, d MMMM').format(now),
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.6))),
            const SizedBox(height: 8),
            AnimatedSwitcher(
              duration: 240.ms,
              child: Row(key: ValueKey(label), mainAxisSize: MainAxisSize.min, children: [
                _Pulse(active: orb != OrbState.idle),
                const SizedBox(width: 8),
                Text(label, style: Theme.of(context).textTheme.labelLarge),
              ]),
            ),
          ]),
        ),
      ]),
    );
  }
}

class _Tiles extends ConsumerWidget {
  const _Tiles({required this.tiles, required this.wide});

  final Map<String, dynamic> tiles;
  final bool wide;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    int n(String k) => (tiles[k] as num?)?.toInt() ?? 0;
    final scheme = Theme.of(context).colorScheme;
    final items = [
      _Tile('Due today', n('due_today'), Icons.today, scheme.primary, tab: 3),
      _Tile('Overdue', n('overdue'), Icons.warning_amber_rounded,
          n('overdue') > 0 ? JarvisColors.danger : scheme.primary, tab: 3),
      _Tile('At risk', n('at_risk'), Icons.trending_down,
          n('at_risk') > 0 ? JarvisColors.warning : JarvisColors.success, tab: 3),
      _Tile('Awaiting you', n('pending_approvals'), Icons.verified_user_outlined,
          n('pending_approvals') > 0 ? JarvisColors.warning : scheme.primary, tab: 4),
      _Tile('Verified today', n('verified_today'), Icons.verified, JarvisColors.success, tab: 5),
      _Tile('Actions today', n('actions_today'), Icons.bolt, scheme.secondary, tab: 5),
      _Tile('Memories', n('memories'), Icons.psychology_outlined, scheme.secondary, tab: 7),
    ];
    return GridView.count(
      crossAxisCount: wide ? 4 : 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 10,
      crossAxisSpacing: 10,
      childAspectRatio: wide ? 1.55 : 1.7,
      children: [
        for (final (i, t) in items.indexed)
          InkWell(
            borderRadius: BorderRadius.circular(18),
            onTap: () => ref.read(homeTabProvider.notifier).state = t.tab,
            child: Glass(
              padding: const EdgeInsets.all(14),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Icon(t.icon, size: 18, color: t.color),
                  const Spacer(),
                  _Count(value: t.value, color: t.color),
                ]),
                const Spacer(),
                Text(t.label, style: Theme.of(context).textTheme.labelLarge),
              ]),
            ),
          ).animate(delay: (60 * i).ms).fadeIn().scale(begin: const Offset(0.96, 0.96)),
      ],
    );
  }
}

class _Tile {
  const _Tile(this.label, this.value, this.icon, this.color, {required this.tab});
  final String label;
  final int value;
  final IconData icon;
  final Color color;
  final int tab;
}

/// A number that rolls to its new value rather than snapping.
class _Count extends StatelessWidget {
  const _Count({required this.value, required this.color});

  final int value;
  final Color color;

  @override
  Widget build(BuildContext context) => TweenAnimationBuilder<double>(
        tween: Tween(begin: 0, end: value.toDouble()),
        duration: 600.ms,
        curve: Curves.easeOutCubic,
        builder: (context, v, _) => Text(
          v.round().toString(),
          style: Theme.of(context)
              .textTheme
              .headlineMedium
              ?.copyWith(color: color, fontWeight: FontWeight.w800),
        ),
      );
}

class _QuickActions extends ConsumerWidget {
  const _QuickActions({required this.routines});

  final List<Map<String, dynamic>> routines;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final client = ref.read(clientProvider);
    Future<void> guard(Future<dynamic> Function() f, String done) async {
      try {
        await f();
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(done)));
        }
      } on ProblemException catch (e) {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
        }
      }
    }

    final briefing = routines.cast<Map<String, dynamic>?>().firstWhere(
          (r) => (r!['name'] as String).toLowerCase().contains('brief'),
          orElse: () => null,
        );
    return Wrap(spacing: 8, runSpacing: 8, children: [
      ActionChip(
        avatar: const Icon(Icons.graphic_eq, size: 18),
        label: const Text('Talk'),
        onPressed: () => ref.read(homeTabProvider.notifier).state = 0,
      ),
      ActionChip(
        avatar: const Icon(Icons.wb_sunny_outlined, size: 18),
        label: const Text('Brief me'),
        onPressed: briefing == null
            ? () => ref.read(homeTabProvider.notifier).state = HudScreen.routinesTab
            : () => guard(() => client.runRoutine(briefing['id'] as String),
                'On it — the briefing lands in its thread and on your channel'),
      ),
      ActionChip(
        avatar: const Icon(Icons.sync, size: 18),
        label: const Text('Scan now'),
        onPressed: () => guard(client.syncAllConnectors, 'Scanning every connected account'),
      ),
      ActionChip(
        avatar: const Icon(Icons.laptop_mac, size: 18),
        label: const Text('Screenshot Mac'),
        onPressed: () => guard(() => client.runAction('mac.capture_screen'),
            'Taking it — the screenshot lands in Timeline in a moment'),
      ),
    ]);
  }
}

class _Panel extends StatelessWidget {
  const _Panel({required this.title, required this.child, this.trailing});

  final String title;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Glass(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            if (trailing != null) trailing!,
          ]),
          const SizedBox(height: 8),
          child,
        ]),
      );
}

class _Muted extends StatelessWidget {
  const _Muted(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(text,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.55))),
      );
}

class _Pulse extends StatelessWidget {
  const _Pulse({required this.active});
  final bool active;

  @override
  Widget build(BuildContext context) {
    final color = active ? Theme.of(context).colorScheme.primary : JarvisColors.success;
    final dot = Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
    if (!active) return dot;
    return dot
        .animate(onPlay: (c) => c.repeat())
        .scale(begin: const Offset(1, 1), end: const Offset(1.6, 1.6), duration: 700.ms)
        .fadeOut(duration: 700.ms);
  }
}

class _LiveLine {
  const _LiveLine({required this.title, required this.subtitle, required this.at, required this.icon, required this.color});

  final String title;
  final String subtitle;
  final DateTime at;
  final IconData icon;
  final Color color;

  static _LiveLine fromEvent(Map<String, dynamic> e) {
    final kind = e['kind'] as String;
    final status = (e['status'] as String?) ?? '';
    final (icon, color) = _style(kind, status);
    final detail = e['detail'] as Map<String, dynamic>?;
    return _LiveLine(
      title: (e['title'] as String? ?? kind).replaceAll('_', ' '),
      subtitle: [
        if (status.isNotEmpty) status.replaceAll('_', ' '),
        if (e['risk'] != null) e['risk'] as String,
        if (detail?['excerpt'] != null) detail!['excerpt'] as String,
      ].join(' · '),
      at: DateTime.parse(e['at'] as String).toLocal(),
      icon: icon,
      color: color,
    );
  }

  static _LiveLine fromTimeline(TimelineEntry t) {
    final (icon, color) = _style(t.kind, t.verdict ?? t.status ?? '');
    return _LiveLine(
      title: t.title.replaceAll('_', ' '),
      subtitle: [
        if (t.status != null) t.status!.replaceAll('_', ' '),
        if (t.verdict != null) 'evidence: ${t.verdict}',
        if (t.detail['excerpt'] != null) t.detail['excerpt'] as String,
      ].join(' · '),
      at: t.at,
      icon: icon,
      color: color,
    );
  }

  static (IconData, Color) _style(String kind, String status) => switch ((kind, status)) {
        ('action', 'verified') => (Icons.verified, JarvisColors.success),
        ('action', 'failed') || ('action', 'denied') => (Icons.error_outline, JarvisColors.danger),
        ('action', 'awaiting_approval') => (Icons.hourglass_top, JarvisColors.warning),
        ('action', _) => (Icons.bolt, JarvisColors.cyan),
        ('run', 'succeeded') => (Icons.check_circle_outline, JarvisColors.success),
        ('run', _) => (Icons.auto_awesome, JarvisColors.sky),
        _ => (Icons.notifications_none, JarvisColors.sky),
      };
}

class _TickerRow extends StatelessWidget {
  const _TickerRow({required this.line, required this.index});

  final _LiveLine line;
  final int index;

  @override
  Widget build(BuildContext context) {
    final row = Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Icon(line.icon, size: 16, color: line.color),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(line.title, style: const TextStyle(fontWeight: FontWeight.w600)),
            if (line.subtitle.isNotEmpty)
              Text(line.subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                      color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.6))),
          ]),
        ),
        const SizedBox(width: 8),
        Text(_ago(line.at), style: Theme.of(context).textTheme.labelSmall),
      ]),
    );
    return index == 0 ? row.animate().fadeIn(duration: 300.ms).slideX(begin: -0.04) : row;
  }
}

String _ago(DateTime at) {
  final d = DateTime.now().difference(at);
  if (d.inSeconds < 60) return 'now';
  if (d.inMinutes < 60) return '${d.inMinutes}m';
  if (d.inHours < 24) return '${d.inHours}h';
  return DateFormat('d MMM').format(at);
}

class _Scans extends StatelessWidget {
  const _Scans({required this.scans});
  final List<Map<String, dynamic>> scans;

  @override
  Widget build(BuildContext context) {
    if (scans.isEmpty) return const _Muted('Nothing connected. Connections → Connect Google.');
    return Column(children: [
      for (final s in scans)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(children: [
            Icon(
              s['error'] != null ? Icons.error_outline : Icons.mark_email_read_outlined,
              size: 16,
              color: s['error'] != null ? JarvisColors.danger : JarvisColors.success,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text('${s['provider']} · ${s['account']}',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
            ),
            Text(
              s['error'] != null
                  ? 'error'
                  : s['at'] == null
                      ? 'never'
                      : '${_ago(DateTime.parse(s['at'] as String).toLocal())}'
                          '${s['new'] != null ? ' · ${s['new']} new' : ''}',
              style: Theme.of(context).textTheme.labelSmall,
            ),
          ]),
        ),
    ]);
  }
}

class _Routines extends StatelessWidget {
  const _Routines({required this.routines});
  final List<Map<String, dynamic>> routines;

  @override
  Widget build(BuildContext context) {
    if (routines.isEmpty) return const _Muted('None enabled. Turn on the morning briefing.');
    return Column(children: [
      for (final r in routines)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(children: [
            Icon(
              switch (r['channel']) { 'call' => Icons.call, 'telegram' => Icons.send, _ => Icons.notifications_active_outlined },
              size: 16,
              color: JarvisColors.sky,
            ),
            const SizedBox(width: 10),
            Expanded(child: Text(r['name'] as String)),
            Text(
              r['next_run_at'] == null
                  ? 'on trigger'
                  : DateFormat('EEE HH:mm').format(DateTime.parse(r['next_run_at'] as String).toLocal()),
              style: Theme.of(context).textTheme.labelSmall,
            ),
          ]),
        ),
    ]);
  }
}

class _System extends StatelessWidget {
  const _System({required this.workers, required this.devices});
  final Map<String, dynamic> workers;
  final List<Map<String, dynamic>> devices;

  @override
  Widget build(BuildContext context) {
    Color state(num? seconds, String name) {
      if (seconds == null) return JarvisColors.danger;
      final limit = name == 'heartbeat' ? 3600 : name == 'connector' ? 900 : 120;
      return seconds <= limit ? JarvisColors.success : JarvisColors.warning;
    }

    return Wrap(spacing: 8, runSpacing: 8, children: [
      for (final e in workers.entries)
        _Dot(label: e.key, color: state(e.value as num?, e.key)),
      for (final d in devices)
        _Dot(
          label: d['name'] as String,
          color: d['online'] == true ? JarvisColors.success : JarvisColors.warning,
          icon: d['platform'] == 'macos' ? Icons.laptop_mac : Icons.phone_android,
        ),
    ]);
  }
}

class _Dot extends StatelessWidget {
  const _Dot({required this.label, required this.color, this.icon});
  final String label;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: context.surfaces.surface3,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: 14), const SizedBox(width: 6)],
          Container(width: 7, height: 7, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
          const SizedBox(width: 6),
          Text(label, style: Theme.of(context).textTheme.labelMedium),
        ]),
      );
}


/// Type anywhere on Home; the chat takes it from here.
class _AskBar extends ConsumerStatefulWidget {
  const _AskBar();

  @override
  ConsumerState<_AskBar> createState() => _AskBarState();
}

class _AskBarState extends ConsumerState<_AskBar> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _go() {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    _controller.clear();
    ref.read(chatPrefillProvider.notifier).state = text;
    ref.read(homeTabProvider.notifier).state = 0;
  }

  @override
  Widget build(BuildContext context) {
    return Glass(
      radius: 22,
      padding: const EdgeInsets.fromLTRB(16, 4, 6, 4),
      child: Row(children: [
        Icon(Icons.auto_awesome, size: 18, color: Theme.of(context).colorScheme.primary),
        const SizedBox(width: 10),
        Expanded(
          child: TextField(
            controller: _controller,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => _go(),
            decoration: const InputDecoration(
              hintText: 'Ask anything — "what\'s due this week?", "screenshot my Mac"',
              border: InputBorder.none,
              enabledBorder: InputBorder.none,
              focusedBorder: InputBorder.none,
              filled: false,
              isDense: true,
            ),
          ),
        ),
        IconButton(
          tooltip: 'Ask Jarvis',
          icon: const Icon(Icons.arrow_upward_rounded),
          onPressed: _go,
        ),
      ]),
    );
  }
}


/// Shown once, until the profile has something in it: a gentle push to the 3-minute
/// interview so Jarvis knows who it is working for.
class _OnboardingNudge extends ConsumerWidget {
  const _OnboardingNudge();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = ref.watch(profileProvider).valueOrNull;
    if (profile == null) return const SizedBox.shrink();
    final filled = ['about', 'priorities', 'people', 'style', 'decisions']
        .any((k) => ((profile[k] as String?) ?? '').trim().isNotEmpty);
    if (filled) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Glass(
        padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
        child: Row(children: [
          Icon(Icons.auto_awesome, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Teach Jarvis who you are',
                  style: Theme.of(context).textTheme.titleSmall),
              Text('Six questions, three minutes. It answers you far better after.',
                  style: Theme.of(context).textTheme.bodySmall),
            ]),
          ),
          FilledButton(
            onPressed: () => ref.read(homeTabProvider.notifier).state = 7, // Train
            child: const Text('Start'),
          ),
        ]),
      ),
    );
  }
}

/// "Do this now" — the most overdue deadline, else the soonest. The single most useful
/// sentence on the screen.
class _FocusCard extends ConsumerWidget {
  const _FocusCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final focus = ref.watch(focusProvider).valueOrNull;
    final item = focus?['focus'] as Map<String, dynamic>?;
    if (item == null) return const SizedBox.shrink();
    final overdue = focus?['focus_reason'] == 'overdue';
    final color = overdue ? JarvisColors.danger : Theme.of(context).colorScheme.primary;
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: InkWell(
        borderRadius: BorderRadius.circular(22),
        onTap: () => ref.read(homeTabProvider.notifier).state = 3,
        child: Glass(
          padding: const EdgeInsets.fromLTRB(18, 14, 18, 14),
          child: Row(children: [
            Icon(overdue ? Icons.priority_high : Icons.bolt, color: color),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(overdue ? 'Overdue — do this now' : 'Do this next',
                    style: Theme.of(context).textTheme.labelMedium?.copyWith(color: color)),
                const SizedBox(height: 2),
                Text(item['title'] as String,
                    style: Theme.of(context).textTheme.titleMedium,
                    maxLines: 1, overflow: TextOverflow.ellipsis),
                Text('${item['due_local']}',
                    style: Theme.of(context).textTheme.bodySmall),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}
/// "What matters now" (#25): the few things that actually need you, ranked — a one-glance
/// digest that composes deadlines due, replies owed and who you've gone quiet on. Tapping a
/// row jumps to where it lives.
class _Digest extends ConsumerWidget {
  const _Digest({required this.items});
  final List<Map<String, dynamic>> items;

  static const _route = {'goals': 3, 'insights': 10};
  static const _icon = {
    'deadline': Icons.event_outlined,
    'commitment': Icons.handshake_outlined,
    'reply': Icons.reply_outlined,
    'reconnect': Icons.diversity_3_outlined,
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final scheme = Theme.of(context).colorScheme;
    return Column(children: [
      for (final it in items)
        InkWell(
          onTap: () {
            final tab = _route[it['route']];
            if (tab != null) ref.read(homeTabProvider.notifier).state = tab;
          },
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(children: [
              Icon(_icon[it['kind']] ?? Icons.bolt_outlined,
                  size: 16,
                  color: it['reason'] == 'overdue' ? JarvisColors.danger : scheme.primary),
              const SizedBox(width: 10),
              Expanded(
                child: Text('${it['text']}', maxLines: 1, overflow: TextOverflow.ellipsis),
              ),
              const SizedBox(width: 8),
              Text('${it['reason']}',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                      color: it['reason'] == 'overdue' ? JarvisColors.danger : null)),
            ]),
          ),
        ),
    ]);
  }
}
