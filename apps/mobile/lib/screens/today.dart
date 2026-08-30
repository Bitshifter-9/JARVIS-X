import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../widgets/risk.dart';

/// Today: what is at risk, and what to do about it.
class TodayScreen extends ConsumerWidget {
  const TodayScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final goals = ref.watch(goalsProvider);

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(goalsProvider),
      child: goals.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => _Failure(message: '$e', onRetry: () => ref.invalidate(goalsProvider)),
        data: (list) {
          final withDeadlines = list.where((g) => g.deadline != null).toList()
            ..sort((a, b) => a.deadline!.compareTo(b.deadline!));

          if (withDeadlines.isEmpty) {
            return const _Empty(
              icon: Icons.check_circle_outline,
              title: 'Nothing due',
              body: 'Goals with deadlines appear here, with a forecast for each.',
            );
          }

          return ListView(
            padding: const EdgeInsets.only(bottom: 24),
            children: [
              for (final goal in withDeadlines) _GoalRisk(goal: goal),
            ],
          );
        },
      ),
    );
  }
}

class _GoalRisk extends ConsumerWidget {
  const _GoalRisk({required this.goal});

  final Goal goal;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prediction = ref.watch(predictionProvider(goal.id));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
          child: Text(goal.title, style: Theme.of(context).textTheme.titleMedium),
        ),
        prediction.when(
          loading: () => const Padding(
            padding: EdgeInsets.all(24),
            child: Center(child: CircularProgressIndicator()),
          ),
          error: (e, _) => Padding(
            padding: const EdgeInsets.all(16),
            child: Text('Forecast unavailable: $e'),
          ),
          data: (p) => RiskCard(prediction: p),
        ),
      ],
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.icon, required this.title, required this.body});

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) => ListView(
        children: [
          const SizedBox(height: 96),
          Icon(icon, size: 48, color: Theme.of(context).disabledColor),
          const SizedBox(height: 12),
          Text(title, textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 48),
            child: Text(body,
                textAlign: TextAlign.center, style: Theme.of(context).textTheme.bodySmall),
          ),
        ],
      );
}

class _Failure extends StatelessWidget {
  const _Failure({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => ListView(
        children: [
          const SizedBox(height: 96),
          Icon(Icons.cloud_off, size: 48, color: Theme.of(context).colorScheme.error),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(message, textAlign: TextAlign.center),
          ),
          const SizedBox(height: 16),
          Center(child: FilledButton(onPressed: onRetry, child: const Text('Retry'))),
        ],
      );
}
