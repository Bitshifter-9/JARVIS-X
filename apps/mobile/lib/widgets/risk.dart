import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../api/models.dart';
import '../theme.dart';
import 'gauge.dart';

Color severityColor(String severity, ColorScheme scheme) => switch (severity) {
      'critical' => JarvisColors.danger,
      'at_risk' => JarvisColors.warning,
      _ => JarvisColors.success,
    };

String severityLabel(String severity) => switch (severity) {
      'critical' => 'Critical',
      'at_risk' => 'At risk',
      _ => 'On track',
    };

/// The failure-prediction card — the screen the product exists for.
///
/// It leads with the generated explanation rather than the probability, because a
/// number without its arithmetic invites disbelief. The gauge makes the number felt.
class RiskCard extends StatelessWidget {
  const RiskCard({super.key, required this.prediction, this.onOptionSelected});

  final Prediction prediction;
  final void Function(RecoveryOption option)? onOptionSelected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = severityColor(prediction.severity, scheme);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
              ProbabilityGauge(
                probability: prediction.probability,
                color: color,
                size: 92,
                label: 'to finish',
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: color.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(
                      severityLabel(prediction.severity),
                      style: TextStyle(color: color, fontWeight: FontWeight.w700, fontSize: 12),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(prediction.explanation,
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(height: 1.4)),
                ]),
              ),
            ]),
            if (prediction.options.isNotEmpty) ...[
              const SizedBox(height: 16),
              Text('Ways out', style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 8),
              for (final (i, option) in prediction.options.indexed)
                _OptionTile(option: option, onSelected: onOptionSelected)
                    .animate(delay: (80 * i).ms)
                    .fadeIn()
                    .slideX(begin: 0.05),
            ],
          ],
        ),
      ),
    );
  }
}

class _OptionTile extends StatelessWidget {
  const _OptionTile({required this.option, this.onSelected});

  final RecoveryOption option;
  final void Function(RecoveryOption option)? onSelected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: context.surfaces.surface3,
        borderRadius: BorderRadius.circular(14),
      ),
      child: ListTile(
        dense: true,
        leading: CircleAvatar(
          radius: 20,
          backgroundColor: scheme.primary.withValues(alpha: 0.14),
          child: Text(
            '${(option.probabilityAfter * 100).round()}%',
            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: scheme.primary),
          ),
        ),
        title: Text(option.title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
          option.requiresApproval ? '${option.detail}  ·  needs someone else' : option.detail,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: onSelected == null ? null : const Icon(Icons.chevron_right),
        onTap: onSelected == null ? null : () => onSelected!(option),
      ),
    );
  }
}
