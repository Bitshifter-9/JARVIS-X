import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';

/// A slow field of blurred light behind a screen. Three blobs drift on Lissajous
/// paths at different speeds so the motion never repeats visibly; opacity is low
/// enough that text sits on it without effort. One painter, no assets, no shader.
class AmbientBackground extends StatefulWidget {
  const AmbientBackground({super.key, required this.child, this.intensity = 1.0});

  final Widget child;
  final double intensity;

  @override
  State<AmbientBackground> createState() => _AmbientBackgroundState();
}

class _AmbientBackgroundState extends State<AmbientBackground>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 40),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Stack(fit: StackFit.expand, children: [
      AnimatedBuilder(
        animation: _controller,
        builder: (context, _) => CustomPaint(
          painter: _AmbientPainter(
            t: _controller.value,
            primary: scheme.primary,
            secondary: scheme.secondary,
            dark: Theme.of(context).brightness == Brightness.dark,
            intensity: widget.intensity,
          ),
        ),
      ),
      widget.child,
    ]);
  }
}

class _AmbientPainter extends CustomPainter {
  _AmbientPainter({
    required this.t,
    required this.primary,
    required this.secondary,
    required this.dark,
    required this.intensity,
  });

  final double t;
  final Color primary;
  final Color secondary;
  final bool dark;
  final double intensity;

  @override
  void paint(Canvas canvas, Size size) {
    final phase = t * 2 * math.pi;
    final blobs = [
      (0.25 + 0.15 * math.sin(phase), 0.2 + 0.1 * math.cos(phase * 1.3), primary, 0.34),
      (0.8 + 0.1 * math.cos(phase * 0.7), 0.3 + 0.15 * math.sin(phase * 0.9), secondary, 0.26),
      (0.5 + 0.2 * math.sin(phase * 0.5), 0.85 + 0.08 * math.cos(phase * 1.1), primary, 0.24),
    ];
    final alpha = (dark ? 0.16 : 0.10) * intensity;
    for (final (x, y, color, r) in blobs) {
      final center = Offset(size.width * x, size.height * y);
      final radius = size.shortestSide * r;
      canvas.drawCircle(
        center,
        radius,
        Paint()
          ..color = color.withValues(alpha: alpha)
          ..maskFilter = MaskFilter.blur(BlurStyle.normal, radius * 0.6),
      );
    }
  }

  @override
  bool shouldRepaint(_AmbientPainter old) => old.t != t || old.dark != dark;
}

/// Frosted surface for panels that float over the ambient field.
class Glass extends StatelessWidget {
  const Glass({super.key, required this.child, this.radius = 18, this.padding});

  final Widget child;
  final double radius;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
        child: Container(
          padding: padding,
          decoration: BoxDecoration(
            color: scheme.surface.withValues(alpha: 0.55),
            borderRadius: BorderRadius.circular(radius),
            border: Border.all(color: scheme.onSurface.withValues(alpha: 0.08)),
          ),
          child: child,
        ),
      ),
    );
  }
}
