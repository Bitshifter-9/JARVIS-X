import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/states.dart';

/// Everything the system did, newest first — actions with their verdict and any
/// screenshot or file, agent runs, alerts, approvals. One list, so "what did Jarvis do
/// while I was away" is one scroll.
class TimelineScreen extends ConsumerStatefulWidget {
  const TimelineScreen({super.key});

  @override
  ConsumerState<TimelineScreen> createState() => _TimelineScreenState();
}

class _TimelineScreenState extends ConsumerState<TimelineScreen> {
  String _filter = 'all';

  static const _filters = {
    'all': 'Everything',
    'action': 'Actions',
    'run': 'Runs',
    'event': 'Alerts & events',
    'artifacts': 'With files',
  };

  @override
  Widget build(BuildContext context) {
    final rows = ref.watch(timelineProvider);
    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(timelineProvider),
      child: rows.when(
        loading: () => const SkeletonList(rows: 6, height: 72),
        error: (e, _) =>
            ErrorState(message: '$e', onRetry: () => ref.invalidate(timelineProvider)),
        data: (all) {
          final list = switch (_filter) {
            'all' => all,
            'artifacts' => all.where((e) => e.hasArtifacts).toList(),
            _ => all.where((e) => e.kind == _filter).toList(),
          };
          final chips = Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(children: [
                for (final e in _filters.entries) ...[
                  ChoiceChip(
                    label: Text(e.value),
                    selected: _filter == e.key,
                    onSelected: (_) => setState(() => _filter = e.key),
                  ),
                  const SizedBox(width: 8),
                ],
              ]),
            ),
          );
          if (list.isEmpty) {
            return ListView(children: [
              chips,
              const SizedBox(
                height: 360,
                child: EmptyState(
                  icon: Icons.timeline,
                  title: 'Nothing yet',
                  body: 'Ask Jarvis for something and it shows up here — with its evidence.',
                ),
              ),
            ]);
          }
          // Day headers, so "while I was away" reads as a story rather than a log.
          final items = <Widget>[chips];
          String? lastDay;
          for (final (i, entry) in list.indexed) {
            final day = _dayLabel(entry.at);
            if (day != lastDay) {
              items.add(Padding(
                padding: const EdgeInsets.fromLTRB(20, 18, 20, 6),
                child: Text(day,
                    style: Theme.of(context).textTheme.labelLarge?.copyWith(
                        color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.55))),
              ));
              lastDay = day;
            }
            items.add(_TimelineTile(entry: entry).enter(i));
          }
          return ListView(padding: const EdgeInsets.only(bottom: 24), children: items);
        },
      ),
    );
  }

  static String _dayLabel(DateTime at) {
    final now = DateTime.now();
    final d = DateTime(at.year, at.month, at.day);
    final today = DateTime(now.year, now.month, now.day);
    final diff = today.difference(d).inDays;
    if (diff == 0) return 'Today';
    if (diff == 1) return 'Yesterday';
    return DateFormat('EEEE d MMMM').format(at);
  }
}

class _TimelineTile extends ConsumerWidget {
  const _TimelineTile({required this.entry});

  final TimelineEntry entry;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final scheme = Theme.of(context).colorScheme;
    final (icon, color) = switch ((entry.kind, entry.verdict ?? entry.status)) {
      ('action', 'verified') => (Icons.verified, JarvisColors.success),
      ('action', 'failed') => (Icons.error_outline, scheme.error),
      ('action', 'denied') => (Icons.block, scheme.error),
      ('action', 'awaiting_approval') => (Icons.hourglass_top, JarvisColors.warning),
      ('action', 'dispatched') => (Icons.send, scheme.primary),
      ('action', _) => (Icons.bolt, scheme.primary),
      ('run', 'succeeded') => (Icons.check_circle_outline, JarvisColors.success),
      ('run', _) => (Icons.auto_awesome, scheme.secondary),
      (_, _) => (Icons.notifications_none, scheme.secondary),
    };
    final when = DateFormat('d MMM, HH:mm').format(entry.at);
    final subtitle = [
      when,
      if (entry.risk != null) entry.risk!,
      if (entry.status != null) entry.status!.replaceAll('_', ' '),
      if (entry.verdict != null) 'evidence: ${entry.verdict}',
      if (entry.simulated) 'simulated',
    ].join(' · ');

    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      ListTile(
        leading: Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.14),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Icon(icon, color: color, size: 20),
        ),
        title: Text(entry.title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(subtitle),
        trailing: entry.verdict == null
            ? null
            : Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text(entry.verdict!,
                    style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w700)),
              ),
        onTap: entry.detail.isEmpty ? null : () => _showDetail(context),
      ),
      for (final artifact in entry.artifacts)
        Padding(
          padding: const EdgeInsets.fromLTRB(72, 0, 16, 12),
          child: _ArtifactPreview(artifact: artifact),
        ),
    ]);
  }

  void _showDetail(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      builder: (context) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(entry.title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            for (final e in entry.detail.entries)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text('${e.key}: ${e.value}'),
              ),
            if (entry.correlationId != null) ...[
              const SizedBox(height: 12),
              Text('correlation ${entry.correlationId}',
                  style: Theme.of(context).textTheme.labelSmall),
            ],
          ],
        ),
      ),
    );
  }
}

/// A screenshot inline; anything else as a chip with its type.
class _ArtifactPreview extends ConsumerWidget {
  const _ArtifactPreview({required this.artifact});

  final Map<String, dynamic> artifact;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final url = artifact['url'] as String;
    final type = (artifact['content_type'] as String?) ?? '';
    if (!type.startsWith('image/')) {
      return Chip(
        avatar: const Icon(Icons.attach_file, size: 16),
        label: Text('${artifact['kind']} · $type'),
      );
    }
    return FutureBuilder(
      future: ref.read(clientProvider).artifactBytes(url),
      builder: (context, snapshot) {
        if (!snapshot.hasData) {
          return const SizedBox(
              height: 120, child: Center(child: CircularProgressIndicator()));
        }
        return ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: Image.memory(snapshot.data!, fit: BoxFit.cover),
        );
      },
    );
  }
}
