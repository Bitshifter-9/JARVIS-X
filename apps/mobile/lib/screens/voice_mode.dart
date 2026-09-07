import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:speech_to_text/speech_to_text.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../voice/speaker.dart';
import '../widgets/orb.dart';

/// A conversation, not a command: listen → answer aloud → listen again, until you
/// close it. Tap the orb to interrupt Jarvis mid-sentence and talk over it. Same
/// brain, same policy — an effectful request still becomes an approval.
class VoiceModeScreen extends ConsumerStatefulWidget {
  const VoiceModeScreen({super.key, this.conversationId});

  final String? conversationId;

  @override
  ConsumerState<VoiceModeScreen> createState() => _VoiceModeScreenState();
}

enum _Phase { starting, listening, thinking, speaking, ended }

class _VoiceModeScreenState extends ConsumerState<VoiceModeScreen> {
  final _stt = SpeechToText();
  late final Speaker _speaker = Speaker(ref.read(clientProvider));
  final _lines = <(String, String)>[];
  final _history = <Map<String, String>>[];
  _Phase _phase = _Phase.starting;
  String _partial = '';
  bool _closed = false;

  @override
  void initState() {
    super.initState();
    _speaker.onSpeaking = (s) {
      if (mounted && s) setState(() => _phase = _Phase.speaking);
    };
    WidgetsBinding.instance.addPostFrameCallback((_) => _loop());
  }

  @override
  void dispose() {
    _closed = true;
    _stt.stop();
    _speaker.dispose();
    super.dispose();
  }

  Future<void> _loop() async {
    final ok = await _stt.initialize();
    if (!ok) {
      setState(() => _phase = _Phase.ended);
      return;
    }
    while (!_closed) {
      setState(() {
        _phase = _Phase.listening;
        _partial = '';
      });
      final heard = await _listenOnce();
      if (_closed) return;
      if (heard.isEmpty) continue;
      setState(() {
        _lines.add(('user', heard));
        _phase = _Phase.thinking;
      });
      _history.add({'role': 'user', 'content': heard});
      String reply;
      try {
        final r = await ref.read(clientProvider).chat(
              _history.length <= 12 ? _history : _history.sublist(_history.length - 12),
            );
        reply = (r['text'] as String?) ?? '';
      } on ProblemException catch (e) {
        reply = 'Sorry — $e';
      } catch (_) {
        reply = 'Sorry, I could not reach the server.';
      }
      _history.add({'role': 'assistant', 'content': reply});
      if (_closed) return;
      setState(() {
        _lines.add(('assistant', reply));
        _phase = _Phase.speaking;
      });
      await _speaker.say(reply);
    }
  }

  Future<String> _listenOnce() async {
    final done = Completer<String>();
    await _stt.listen(
      listenOptions: SpeechListenOptions(
        listenFor: const Duration(seconds: 20),
        pauseFor: const Duration(seconds: 2),
        partialResults: true,
      ),
      onResult: (r) {
        if (mounted) setState(() => _partial = r.recognizedWords);
        if (r.finalResult && !done.isCompleted) done.complete(r.recognizedWords);
      },
    );
    final words = await done.future.timeout(const Duration(seconds: 24), onTimeout: () => '');
    await _stt.stop();
    return words.trim();
  }

  Future<void> _interrupt() async {
    HapticFeedback.mediumImpact();
    await _speaker.stop();
    await _stt.stop();
    // The loop notices speaking ended and listens again.
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final orb = switch (_phase) {
      _Phase.listening => OrbState.listening,
      _Phase.thinking => OrbState.thinking,
      _Phase.speaking => OrbState.speaking,
      _ => OrbState.idle,
    };
    final label = switch (_phase) {
      _Phase.starting => 'Starting…',
      _Phase.listening => _partial.isEmpty ? 'Listening' : _partial,
      _Phase.thinking => 'Thinking…',
      _Phase.speaking => 'Tap to interrupt',
      _Phase.ended => 'Microphone unavailable',
    };
    return Scaffold(
      backgroundColor: scheme.surface,
      body: SafeArea(
        child: Column(children: [
          Row(children: [
            const SizedBox(width: 8),
            Text('Voice', style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            IconButton(
              tooltip: 'End',
              icon: const Icon(Icons.close),
              onPressed: () => Navigator.of(context).pop(),
            ),
          ]),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              reverse: true,
              children: [
                for (final (role, text) in _lines.reversed)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    child: Align(
                      alignment: role == 'user' ? Alignment.centerRight : Alignment.centerLeft,
                      child: Text(
                        text,
                        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                              color: role == 'user'
                                  ? scheme.onSurface.withValues(alpha: 0.7)
                                  : scheme.onSurface,
                            ),
                      ),
                    ),
                  ).animate().fadeIn(),
              ],
            ),
          ),
          GestureDetector(
            onTap: _phase == _Phase.speaking ? _interrupt : null,
            child: JarvisOrb(state: orb, size: 180),
          ),
          const SizedBox(height: 18),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(label,
                textAlign: TextAlign.center,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: scheme.onSurface.withValues(alpha: 0.75),
                    )),
          ),
          const SizedBox(height: 36),
        ]),
      ),
    );
  }
}
