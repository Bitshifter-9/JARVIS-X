import 'dart:math' as math;

import 'package:flutter/material.dart';

/// Completion probability as an arc that fills to the value when it appears.
///
/// The number is the headline; the arc makes 34% *feel* like 34% before the eye reads
/// it. Colour is the severity, not the value — 60% can be "at risk" for a big goal.
class ProbabilityGauge extends StatelessWidget {
  const ProbabilityGauge({
    super.key,
    required this.probability,
    required this.color,
    this.size = 84,
    this.label,
  });

  final double probability;
  final Color color;
  final double size;
  final String? label;

  @override
  Widget build(BuildContext context) {
    final track = Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.08);
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: probability.clamp(0, 1)),
      duration: const Duration(milliseconds: 900),
      curve: Curves.easeOutCubic,
      builder: (context, value, _) => SizedBox.square(
        dimension: size,
        child: CustomPaint(
          painter: _GaugePainter(value: value, color: color, track: track),
          child: Center(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Text('${(value * 100).round()}%',
                  style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      color: color, fontWeight: FontWeight.w800, letterSpacing: -0.5)),
              if (label != null)
                Text(label!,
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.6))),
            ]),
          ),
        ),
      ),
    );
  }
}

class _GaugePainter extends CustomPainter {
  _GaugePainter({required this.value, required this.color, required this.track});

  final double value;
  final Color color;
  final Color track;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Rect.fromCircle(center: size.center(Offset.zero), radius: size.shortestSide / 2 - 6);
    const start = math.pi * 0.75;
    const sweep = math.pi * 1.5;
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 7
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(rect, start, sweep, false, paint..color = track);
    canvas.drawArc(rect, start, sweep * value, false, paint..color = color);
    // A soft glow under the lit part of the arc.
    canvas.drawArc(rect, start, sweep * value, false,
        paint..color = color.withValues(alpha: 0.35)..strokeWidth = 12
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 6));
  }

  @override
  bool shouldRepaint(_GaugePainter old) => old.value != value || old.color != color;
}
