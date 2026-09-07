import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/gauge.dart';
import '../widgets/risk.dart';
import '../widgets/states.dart';

class GoalsScreen extends ConsumerWidget {
  const GoalsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final goals = ref.watch(goalsProvider);
    final tasks = ref.watch(tasksProvider);
    final formatter = DateFormat('EEE d MMM, HH:mm');

    return Scaffold(
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(goalsProvider);
          ref.invalidate(tasksProvider);
        },
        child: goals.when(
          loading: () => const SkeletonList(rows: 5, height: 76),
          error: (e, _) => ErrorState(message: '$e', onRetry: () => ref.invalidate(goalsProvider)),
          data: (list) => ListView(
            padding: const EdgeInsets.only(top: 8, bottom: 96),
            children: [
              _Deadlines(tasks: tasks.valueOrNull ?? const []),
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 18, 20, 6),
                child: Text('Goals', style: Theme.of(context).textTheme.titleMedium),
              ),
              if (list.isEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
                  child: Text(
                    'A goal is a deadline plus the tasks between you and it. Add one below.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              for (final (i, goal) in list.indexed)
                Card(
                  child: ListTile(
                    contentPadding: const EdgeInsets.fromLTRB(16, 6, 12, 6),
                    title: Text(goal.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                    subtitle: Text(goal.deadline == null
                        ? 'No deadline'
                        : 'Due ${formatter.format(goal.deadline!)}'),
                    trailing: goal.deadline == null ? null : _SeverityGauge(goalId: goal.id),
                  ),
                ).enter(i),
            ],
          ),
        ),
      ),
      floatingActionButton: Column(mainAxisSize: MainAxisSize.min, children: [
        FloatingActionButton.small(
          heroTag: 'addDeadline',
          tooltip: 'Add a deadline',
          onPressed: () => _addDeadline(context, ref),
          child: const Icon(Icons.event_available_outlined),
        ),
        const SizedBox(height: 10),
        FloatingActionButton.extended(
          key: const Key('addGoal'),
          heroTag: 'addGoal',
          onPressed: () => _addGoal(context, ref),
          icon: const Icon(Icons.add),
          label: const Text('Goal'),
        ),
      ]),
    );
  }

  Future<void> _addDeadline(BuildContext context, WidgetRef ref) async {
    final controller = TextEditingController();
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now.add(const Duration(days: 1)),
      firstDate: now,
      lastDate: now.add(const Duration(days: 365)),
      helpText: 'When is it due?',
    );
    if (date == null || !context.mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: const TimeOfDay(hour: 9, minute: 0),
      helpText: 'What time?',
    );
    if (time == null || !context.mounted) return;
    final title = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('What is due?'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(labelText: 'Deadline', hintText: 'Exam · Submission · Rent'),
          onSubmitted: (v) => Navigator.pop(context, v.trim()),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Add'),
          ),
        ],
      ),
    );
    if (title == null || title.isEmpty) return;
    final due = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    if (!context.mounted) return;
    final repeat = await showModalBottomSheet<String?>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const ListTile(title: Text('Repeat?')),
          for (final r in const [
            (null, 'Once'),
            ('daily', 'Every day'),
            ('weekdays', 'Weekdays'),
            ('weekly', 'Every week'),
            ('monthly', 'Every month'),
          ])
            ListTile(
              leading: Icon(r.$1 == null ? Icons.event : Icons.repeat),
              title: Text(r.$2),
              onTap: () => Navigator.pop(context, r.$1),
            ),
        ]),
      ),
    );
    await ref.read(clientProvider).createTask(title, dueAt: due, recurrence: repeat);
    ref.invalidate(tasksProvider);
    ref.invalidate(hudProvider);
  }

  Future<void> _addGoal(BuildContext context, WidgetRef ref) async {
    final controller = TextEditingController();
    final title = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('New goal'),
        content: TextField(
          key: const Key('goalTitle'),
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(labelText: 'Title'),
          onSubmitted: (v) => Navigator.pop(context, v.trim()),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Create'),
          ),
        ],
      ),
    );
    if (title == null || title.isEmpty) return;
    await ref.read(clientProvider).createGoal(title);
    ref.invalidate(goalsProvider);
  }
}

class _SeverityGauge extends ConsumerWidget {
  const _SeverityGauge({required this.goalId});

  final String goalId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prediction = ref.watch(predictionProvider(goalId));
    return prediction.when(
      loading: () => const SizedBox(
          height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2)),
      error: (_, __) => const Icon(Icons.help_outline, size: 16),
      data: (p) => ProbabilityGauge(
        probability: p.probability,
        color: severityColor(p.severity, Theme.of(context).colorScheme),
        size: 54,
      ),
    );
  }
}


/// Every deadline Jarvis knows — the ones it read out of your mail included — with
/// where it came from and the exact words it read, so you can trust it or correct it.
class _Deadlines extends ConsumerWidget {
  const _Deadlines({required this.tasks});

  final List<Task> tasks;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dated = tasks.where((t) => t.dueAt != null).toList();
    dated.sort((a, b) => a.dueAt!.compareTo(b.dueAt!));
    final children = <Widget>[
      Padding(
        padding: const EdgeInsets.fromLTRB(20, 10, 20, 6),
        child: Row(children: [
          Text('Agenda', style: Theme.of(context).textTheme.titleMedium),
          const Spacer(),
          Text('${dated.length} open', style: Theme.of(context).textTheme.labelMedium),
        ]),
      ),
      if (dated.isEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 8),
          child: Text(
            'None yet. When a scanned mail carries a date, it lands here with the sentence '
            'it was read from. Tap + to add one.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
    ];
    String? lastGroup;
    for (final (i, t) in dated.indexed) {
      final group = _dayGroup(t.dueAt!);
      if (group != lastGroup) {
        children.add(Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 2),
          child: Text(group,
              style: Theme.of(context).textTheme.labelLarge?.copyWith(
                  color: group == 'Overdue'
                      ? JarvisColors.danger
                      : Theme.of(context).colorScheme.primary)),
        ));
        lastGroup = group;
      }
      children.add(_DeadlineTile(task: t).enter(i));
    }
    children.add(const _CompletedDeadlines());
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: children);
  }
}

/// Deadlines you have finished — collapsed, so a done item is never simply gone. Tap
/// the circle to bring one back.
class _CompletedDeadlines extends ConsumerWidget {
  const _CompletedDeadlines();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final all = ref.watch(allTasksProvider).valueOrNull ?? const [];
    final done = all
        .where((t) => t.dueAt != null && (t.status == 'done' || t.status == 'cancelled'))
        .toList()
      ..sort((a, b) => b.dueAt!.compareTo(a.dueAt!));
    if (done.isEmpty) return const SizedBox.shrink();
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: const EdgeInsets.symmetric(horizontal: 20),
        leading: const Icon(Icons.check_circle_outline, color: JarvisColors.success),
        title: Text('Completed (${done.length})',
            style: Theme.of(context).textTheme.titleSmall),
        children: [
          for (final t in done)
            ListTile(
              dense: true,
              leading: IconButton(
                tooltip: 'Reopen',
                icon: const Icon(Icons.restart_alt),
                onPressed: () async {
                  try {
                    await ref.read(clientProvider)
                        .setTaskStatus(t.id, 'open', version: t.version);
                    ref.invalidate(tasksProvider);
                    ref.invalidate(allTasksProvider);
                    ref.invalidate(hudProvider);
                  } on ProblemException catch (e) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context)
                          .showSnackBar(SnackBar(content: Text('$e')));
                    }
                  }
                },
              ),
              title: Text(t.title,
                  style: const TextStyle(decoration: TextDecoration.lineThrough)),
              subtitle: Text('was due ${DateFormat('d MMM, HH:mm').format(t.dueAt!)}'),
            ),
        ],
      ),
    );
  }
}

class _DeadlineTile extends ConsumerWidget {
  const _DeadlineTile({required this.task});

  final Task task;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final due = task.dueAt!;
    final left = due.difference(DateTime.now());
    final urgency = left.isNegative
        ? JarvisColors.danger
        : left.inHours < 24
            ? JarvisColors.warning
            : JarvisColors.success;
    final when = left.isNegative
        ? 'overdue by ${_span(-left)}'
        : 'in ${_span(left)}';
    final source = task.sourceProvider == null
        ? null
        : '${task.sourceProvider} · ${(task.sourceAuthor ?? '').replaceAll(RegExp(r'<[^>]*>'), '').trim()}';
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 8),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Container(
              width: 10,
              height: 10,
              decoration: BoxDecoration(color: urgency, shape: BoxShape.circle),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(task.title, style: Theme.of(context).textTheme.titleMedium),
            ),
            IconButton(
              tooltip: 'Mark done',
              icon: const Icon(Icons.check_circle_outline),
              onPressed: () => _setDone(context, ref, task, done: true),
            ),
          ]),
          const SizedBox(height: 4),
          Row(children: [
            Expanded(
              child: Text('${DateFormat('EEE d MMM, HH:mm').format(due)} · $when',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: urgency)),
            ),
            if (task.recurrence != null)
              Chip(
                visualDensity: VisualDensity.compact,
                avatar: const Icon(Icons.repeat, size: 14),
                label: Text(task.recurrence!, style: const TextStyle(fontSize: 11)),
              ),
          ]),
          if (source != null) ...[
            const SizedBox(height: 6),
            InkWell(
              onTap: task.sourceUrl == null
                  ? null
                  : () => launchUrl(Uri.parse(task.sourceUrl!), mode: LaunchMode.externalApplication),
              child: Row(children: [
                Icon(task.sourceProvider == 'gmail' ? Icons.mail_outline : Icons.source_outlined,
                    size: 14, color: Theme.of(context).colorScheme.primary),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(
                    '$source${task.sourceTitle != null ? ' · "${task.sourceTitle}"' : ''}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: Theme.of(context).colorScheme.primary),
                  ),
                ),
              ]),
            ),
          ],
          if (task.evidenceSpan != null) ...[
            const SizedBox(height: 4),
            Text('“${task.evidenceSpan}”',
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    fontStyle: FontStyle.italic,
                    color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.6))),
          ],
        ]),
      ),
    );
  }

  Future<void> _setDone(BuildContext context, WidgetRef ref, Task task,
      {required bool done}) async {
    try {
      await ref.read(clientProvider)
          .setTaskStatus(task.id, done ? 'done' : 'open', version: task.version);
      ref.invalidate(tasksProvider);
      ref.invalidate(allTasksProvider);
      ref.invalidate(hudProvider);
      if (done && context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('“${task.title}” done'),
          action: SnackBarAction(
            label: 'Undo',
            onPressed: () => _setDone(context, ref, task, done: false),
          ),
        ));
      }
    } on ProblemException catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }

  static String _span(Duration d) {
    if (d.inDays >= 1) return '${d.inDays}d ${d.inHours % 24}h';
    if (d.inHours >= 1) return '${d.inHours}h ${d.inMinutes % 60}m';
    return '${d.inMinutes}m';
  }
}


String _dayGroup(DateTime at) {
  final now = DateTime.now();
  final d = DateTime(at.year, at.month, at.day);
  final today = DateTime(now.year, now.month, now.day);
  final diff = d.difference(today).inDays;
  if (at.isBefore(now)) return 'Overdue';
  if (diff == 0) return 'Today';
  if (diff == 1) return 'Tomorrow';
  if (diff < 7) return DateFormat('EEEE').format(at);
  if (diff < 14) return 'Next week';
  return DateFormat('MMMM').format(at);
}
