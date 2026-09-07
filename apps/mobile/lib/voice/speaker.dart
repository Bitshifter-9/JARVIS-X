import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_tts/flutter_tts.dart';

import '../api/client.dart';

/// One voice for every surface: the server's neural voice when reachable, the device's
/// own voice when not. Never silence.
class Speaker {
  Speaker(this._client);

  final JarvisClient _client;
  final _player = AudioPlayer();
  final _tts = FlutterTts();
  void Function(bool speaking)? onSpeaking;

  Future<void> say(String text) async {
    if (text.trim().isEmpty) return;
    onSpeaking?.call(true);
    try {
      final bytes = await _client.ttsBytes(text);
      if (bytes != null) {
        await _player.play(BytesSource(bytes));
        await _player.onPlayerComplete.first.timeout(
          Duration(seconds: 5 + text.length ~/ 12),
          onTimeout: () {},
        );
        return;
      }
      await _tts.awaitSpeakCompletion(true);
      await _tts.speak(text);
    } catch (_) {
      try {
        await _tts.speak(text);
      } catch (_) {}
    } finally {
      onSpeaking?.call(false);
    }
  }

  Future<void> stop() async {
    await _player.stop();
    await _tts.stop();
    onSpeaking?.call(false);
  }

  void dispose() {
    _player.dispose();
    _tts.stop();
  }
}
