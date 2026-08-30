import 'package:flutter/material.dart';

import '../api/models.dart';

Color severityColor(String severity, ColorScheme scheme) => switch (severity) {
      'critical' => scheme.error,
      'at_risk' => Colors.orange.shade700,
      _ => Colors.green.shade600,
    };

String severityLabel(String severity) => switch (severity) {
      'critical' => 'Critical',
      'at_risk' => 'At risk',
      _ => 'On track',
    };

/// The failure-prediction card — the screen the product exists for.
///
/// It leads with the generated explanation rather than the probability, because a
/// number without its arithmetic invites disbelief.
class RiskCard extends StatelessWidget {
  const RiskCard({super.key, required this.prediction, this.onOptionSelected});

  final Prediction prediction;
  final void Function(RecoveryOption option)? onOptionSelected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = severityColor(prediction.severity, scheme);

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: color.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    severityLabel(prediction.severity),
                    style: TextStyle(color: color, fontWeight: FontWeight.w600),
                  ),
                ),
                const Spacer(),
                Text(
                  '${(prediction.probability * 100).round()}%',
                  style: Theme.of(context)
                      .textTheme
                      .headlineSmall
                      ?.copyWith(color: color, fontWeight: FontWeight.bold),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(prediction.explanation, style: Theme.of(context).textTheme.bodyMedium),
            if (prediction.options.isNotEmpty) ...[
              const SizedBox(height: 16),
              Text('Recovery options', style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 8),
              for (final option in prediction.options)
                _OptionTile(option: option, onSelected: onOptionSelected),
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
    return ListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      leading: CircleAvatar(
        radius: 20,
        backgroundColor:
            Theme.of(context).colorScheme.primary.withValues(alpha: 0.12),
        child: Text(
          '${(option.probabilityAfter * 100).round()}%',
          style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold),
        ),
      ),
      title: Text(option.title),
      subtitle: Text(
        option.requiresApproval
            ? '${option.detail}  ·  needs someone else'
            : option.detail,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      onTap: onSelected == null ? null : () => onSelected!(option),
    );
  }
}
