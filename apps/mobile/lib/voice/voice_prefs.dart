import 'package:flutter_tts/flutter_tts.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Voice reply speed and voice picker (FEATURES-50 #19). Stored per device in prefs;
/// the speed applies to both the server voice (playback rate) and the on-device voice.
class VoicePrefs {
  static const rateKey = 'tts_rate';
  static const voiceKey = 'tts_voice'; // "name|locale"

  static Future<double> rate() async {
    try {
      final p = await SharedPreferences.getInstance();
      return p.getDouble(rateKey) ?? 1.0;
    } catch (_) {
      return 1.0;
    }
  }

  static Future<void> setRate(double value) async {
    try {
      final p = await SharedPreferences.getInstance();
      await p.setDouble(rateKey, value);
    } catch (_) {}
  }

  static Future<String?> voice() async {
    try {
      final p = await SharedPreferences.getInstance();
      return p.getString(voiceKey);
    } catch (_) {
      return null;
    }
  }

  static Future<void> setVoice(String? nameLocale) async {
    try {
      final p = await SharedPreferences.getInstance();
      if (nameLocale == null) {
        await p.remove(voiceKey);
      } else {
        await p.setString(voiceKey, nameLocale);
      }
    } catch (_) {}
  }

  /// Apply the saved rate and voice to a FlutterTts. flutter_tts maps 0.0–1.0 to the
  /// platform range; we treat 1.0 as "normal" and scale to its 0.5 default.
  static Future<void> applyTts(FlutterTts tts) async {
    await tts.setSpeechRate((await rate()) * 0.5);
    final v = await voice();
    if (v != null && v.contains('|')) {
      final parts = v.split('|');
      await tts.setVoice({'name': parts[0], 'locale': parts[1]});
    }
  }
}
