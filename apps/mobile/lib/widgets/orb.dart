import 'dart:math' as math;

import 'package:flutter/material.dart';

/// Jarvis's presence: one orb, four states.
///
/// Idle breathes slowly. Listening pulses with a bright ring. Thinking spins a gradient
/// arc. Speaking shows a bar waveform. No image assets — it is drawn, so it scales from
/// a 16 px island dot to a 160 px hero without blurring, and it costs one painter.
enum OrbState { idle, listening, thinking, speaking }

class JarvisOrb extends StatefulWidget {
  const JarvisOrb({
    super.key,
    required this.state,
    this.size = 96,
    this.color,
    this.amplitude = 0,
  });

  final OrbState state;
  final double size;
  final Color? color;

  /// Live audio energy, 0–1: your voice while listening, Jarvis's while speaking. Makes
  /// the orb physically react instead of just animating on a timer.
  final double amplitude;

  @override
  State<JarvisOrb> createState() => _JarvisOrbState();
}

class _JarvisOrbState extends State<JarvisOrb> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2400),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = widget.color ?? Theme.of(context).colorScheme.primary;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) => CustomPaint(
        size: Size.square(widget.size),
        painter: _OrbPainter(
          t: _controller.value,
          state: widget.state,
          color: color,
          amplitude: widget.amplitude.clamp(0.0, 1.0),
        ),
      ),
    );
  }
}

class _OrbPainter extends CustomPainter {
  _OrbPainter({
    required this.t,
    required this.state,
    required this.color,
    this.amplitude = 0,
  });

  final double t;
  final OrbState state;
  final Color color;
  final double amplitude;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.shortestSide / 2;
    final phase = t * 2 * math.pi;
    final amp = amplitude; // 0–1, live audio energy

    // Core glow: brighter and larger the more active the state — and it swells with the
    // live audio, so the orb visibly breathes with your voice.
    final activity = (switch (state) {
      OrbState.idle => 0.35 + 0.08 * math.sin(phase),
      OrbState.listening => 0.7 + 0.25 * math.sin(phase * 3) + 0.5 * amp,
      OrbState.thinking => 0.6,
      OrbState.speaking => 0.7 + 0.2 * math.sin(phase * 6) + 0.45 * amp,
    }).clamp(0.0, 1.3);
    canvas.drawCircle(
      c,
      r * 0.55 * (0.9 + 0.1 * activity),
      Paint()
        ..shader = RadialGradient(colors: [
          color.withValues(alpha: 0.9 * activity),
          color.withValues(alpha: 0.25 * activity),
          color.withValues(alpha: 0),
        ], stops: const [0, 0.55, 1]).createShader(Rect.fromCircle(center: c, radius: r * 0.6)),
    );

    final ring = Paint()
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    switch (state) {
      case OrbState.idle:
        ring
          ..strokeWidth = r * 0.06
          ..color = color.withValues(alpha: 0.55);
        canvas.drawArc(Rect.fromCircle(center: c, radius: r * 0.78), -math.pi / 2 + 0.6, 2 * math.pi - 1.2, false, ring);
      case OrbState.listening:
        for (var i = 0; i < 3; i++) {
          final k = ((t + i / 3) % 1.0);
          ring
            ..strokeWidth = r * 0.05 * (1 - k)
            ..color = color.withValues(alpha: (1 - k) * (0.5 + 0.4 * amp));
          // Rings ride out further the louder you speak.
          canvas.drawCircle(c, r * (0.55 + (0.4 + 0.2 * amp) * k), ring);
        }
      case OrbState.thinking:
        ring
          ..strokeWidth = r * 0.08
          ..shader = SweepGradient(
            startAngle: 0,
            endAngle: 2 * math.pi,
            transform: GradientRotation(phase),
            colors: [color.withValues(alpha: 0), color, color.withValues(alpha: 0)],
            stops: const [0.0, 0.5, 1.0],
          ).createShader(Rect.fromCircle(center: c, radius: r));
        canvas.drawCircle(c, r * 0.78, ring);
      case OrbState.speaking:
        final bars = 5;
        final barPaint = Paint()
          ..color = color
          ..strokeCap = StrokeCap.round
          ..strokeWidth = r * 0.12;
        for (var i = 0; i < bars; i++) {
          final x = c.dx + (i - (bars - 1) / 2) * r * 0.32;
          final wobble = 0.5 + 0.5 * math.sin(phase * 4 + i * 1.3);
          final h = r * (0.2 + (0.3 + 0.5 * amp) * wobble);
          canvas.drawLine(Offset(x, c.dy - h / 2), Offset(x, c.dy + h / 2), barPaint);
        }
        ring
          ..strokeWidth = r * 0.05
          ..color = color.withValues(alpha: 0.35);
        canvas.drawCircle(c, r * 0.85, ring);
    }
  }

  @override
  bool shouldRepaint(_OrbPainter old) =>
      old.t != t || old.state != state || old.color != color || old.amplitude != amplitude;
}
