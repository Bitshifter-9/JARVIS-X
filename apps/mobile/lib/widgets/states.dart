import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';

/// The three states every list has, drawn once.

class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, this.body, this.action});

  final IconData icon;
  final String title;
  final String? body;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return ListView(children: [
      const SizedBox(height: 88),
      Center(
        child: Container(
          width: 96,
          height: 96,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: RadialGradient(colors: [
              scheme.primary.withValues(alpha: 0.22),
              scheme.primary.withValues(alpha: 0.0),
            ]),
          ),
          child: Center(
            child: Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: scheme.primary.withValues(alpha: 0.12),
                border: Border.all(color: scheme.primary.withValues(alpha: 0.25)),
              ),
              child: Icon(icon, size: 34, color: scheme.primary),
            ),
          ),
        ),
      ).animate().scale(duration: 500.ms, curve: Curves.easeOutBack),
      const SizedBox(height: 16),
      Text(title, textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleLarge)
          .animate().fadeIn(delay: 120.ms),
      if (body != null) ...[
        const SizedBox(height: 6),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 48),
          child: Text(body!,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: scheme.onSurface.withValues(alpha: 0.65))),
        ).animate().fadeIn(delay: 200.ms),
      ],
      if (action != null) ...[
        const SizedBox(height: 20),
        Center(child: action!).animate().fadeIn(delay: 280.ms).slideY(begin: 0.2),
      ],
    ]);
  }
}

class ErrorState extends StatelessWidget {
  const ErrorState({super.key, required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => ListView(children: [
        const SizedBox(height: 88),
        Icon(Icons.cloud_off_rounded, size: 44, color: Theme.of(context).colorScheme.error),
        const SizedBox(height: 12),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(message, textAlign: TextAlign.center),
        ),
        const SizedBox(height: 16),
        Center(child: FilledButton.tonal(onPressed: onRetry, child: const Text('Retry'))),
      ]).animate().fadeIn();
}

/// Shimmering placeholders while a list loads — the shape of what is coming, not a spinner.
class SkeletonList extends StatelessWidget {
  const SkeletonList({super.key, this.rows = 4, this.height = 84});

  final int rows;
  final double height;

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.06);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        for (var i = 0; i < rows; i++)
          Container(
            height: height,
            margin: const EdgeInsets.only(bottom: 12),
            decoration: BoxDecoration(color: base, borderRadius: BorderRadius.circular(18)),
          )
              .animate(onPlay: (c) => c.repeat())
              .shimmer(duration: 1400.ms, delay: (i * 120).ms,
                  color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.12)),
      ],
    );
  }
}

/// Entrance for list rows: each fades and rises a little later than the one before it.
extension StaggeredEntrance on Widget {
  Widget enter(int index) => animate(delay: (40 * index.clamp(0, 12)).ms)
      .fadeIn(duration: 320.ms, curve: Curves.easeOut)
      .slideY(begin: 0.06, end: 0, duration: 320.ms, curve: Curves.easeOutCubic);
}
