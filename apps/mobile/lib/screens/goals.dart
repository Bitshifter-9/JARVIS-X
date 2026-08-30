import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../state/providers.dart';
import '../widgets/risk.dart';

class GoalsScreen extends ConsumerWidget {
  const GoalsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final goals = ref.watch(goalsProvider);
    final formatter = DateFormat('d MMM, HH:mm');

    return Scaffold(
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(goalsProvider),
        child: goals.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => ListView(children: [
            const SizedBox(height: 96),
            Center(child: Text('Could not load goals: $e')),
          ]),
          data: (list) => list.isEmpty
              ? ListView(children: [
                  const SizedBox(height: 96),
                  Icon(Icons.flag_outlined,
                      size: 48, color: Theme.of(context).disabledColor),
                  const SizedBox(height: 12),
                  Text('No goals yet',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.titleMedium),
                ])
              : ListView.builder(
                  itemCount: list.length,
                  itemBuilder: (context, i) {
                    final goal = list[i];
                    return ListTile(
                      title: Text(goal.title),
                      subtitle: Text(goal.deadline == null
                          ? 'No deadline'
                          : 'Due ${formatter.format(goal.deadline!)}'),
                      trailing: goal.deadline == null
                          ? null
                          : _SeverityDot(goalId: goal.id),
                    );
                  },
                ),
        ),
      ),
      floatingActionButton: FloatingActionButton(
        key: const Key('addGoal'),
        onPressed: () => _addGoal(context, ref),
        child: const Icon(Icons.add),
      ),
    );
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

class _SeverityDot extends ConsumerWidget {
  const _SeverityDot({required this.goalId});

  final String goalId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prediction = ref.watch(predictionProvider(goalId));
    return prediction.when(
      loading: () => const SizedBox(
          height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2)),
      error: (_, __) => const Icon(Icons.help_outline, size: 16),
      data: (p) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('${(p.probability * 100).round()}%',
              style: TextStyle(
                color: severityColor(p.severity, Theme.of(context).colorScheme),
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(width: 8),
          Icon(Icons.circle,
              size: 10, color: severityColor(p.severity, Theme.of(context).colorScheme)),
        ],
      ),
    );
  }
}
