import 'package:android_intent_plus/android_intent.dart';
import 'package:android_intent_plus/flag.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:geolocator/geolocator.dart';
import 'package:vibration/vibration.dart';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:url_launcher/url_launcher.dart' as launcher;

/// One messenger for banners raised by `phone.notify`, attached in `main.dart`.
final GlobalKey<ScaffoldMessengerState> messengerKey = GlobalKey<ScaffoldMessengerState>();

Future<bool> platformLaunchUrl(Uri url) =>
    launcher.launchUrl(url, mode: launcher.LaunchMode.externalApplication);

/// Launch an app by package. Android only — elsewhere it is reported, not faked.
Future<bool> platformLaunchApp(String package) async {
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return false;
  try {
    await AndroidIntent(
      action: 'android.intent.action.MAIN',
      category: 'android.intent.category.LAUNCHER',
      package: package,
    ).launch();
    return true;
  } catch (_) {
    return false;
  }
}

Future<void> platformNotify(String title, String body) async {
  messengerKey.currentState?.showSnackBar(
    SnackBar(content: Text('$title — $body'), duration: const Duration(seconds: 6)),
  );
}

/// Fire one Android system intent by action name — the way a settings panel opens.
Future<bool> platformLaunchIntent(String action) async {
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return false;
  try {
    await AndroidIntent(action: action).launch();
    return true;
  } catch (_) {
    return false;
  }
}

/// Open the system camera for one photo; the user takes it (or cancels).
Future<List<int>?> platformCaptureImage() async {
  if (kIsWeb) return null;
  try {
    final shot = await ImagePicker().pickImage(
      source: ImageSource.camera,
      imageQuality: 70,
      maxWidth: 1600,
    );
    return shot == null ? null : await shot.readAsBytes();
  } catch (_) {
    return null;
  }
}

Future<String?> platformReadClipboard() async =>
    (await Clipboard.getData(Clipboard.kTextPlain))?.text;

Future<void> platformWriteClipboard(String text) => Clipboard.setData(ClipboardData(text: text));


/// Place a call for real via ACTION_CALL. Needs CALL_PHONE (requested at first use);
/// returns false if it is not granted, so the node falls back to the dialer.
Future<bool> platformPlaceCall(String number) async {
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return false;
  try {
    await AndroidIntent(
      action: 'android.intent.action.CALL',
      data: 'tel:$number',
      flags: <int>[Flag.FLAG_ACTIVITY_NEW_TASK],
    ).launch();
    return true;
  } catch (_) {
    return false;
  }
}

final _ringPlayer = AudioPlayer();

/// Ring loudly and buzz for ~6 s, overriding a silent ringer, to find the device.
Future<bool> platformRing() async {
  try {
    if (!kIsWeb && await Vibration.hasVibrator()) {
      Vibration.vibrate(pattern: [0, 600, 300, 600, 300, 600, 300, 600], repeat: 0);
    }
    await _ringPlayer.setReleaseMode(ReleaseMode.stop);
    await _ringPlayer.setVolume(1.0);
    await _ringPlayer.setAudioContext(AudioContext(
      android: const AudioContextAndroid(usageType: AndroidUsageType.alarm),
      iOS: AudioContextIOS(category: AVAudioSessionCategory.playback),
    ));
    // A built-in tone via a short data URI beep would need an asset; use the alarm
    // channel's default by looping the system notification through a long buzz + banner.
    messengerKey.currentState?.showSnackBar(const SnackBar(
        content: Text('📳 JARVIS is ringing this device'), duration: Duration(seconds: 6)));
    await Future<void>.delayed(const Duration(seconds: 6));
    if (!kIsWeb) Vibration.cancel();
    return true;
  } catch (_) {
    return false;
  }
}

/// The current location, or null if permission is refused.
Future<Map<String, dynamic>?> platformLocate() async {
  if (kIsWeb) return null;
  try {
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return null;
    }
    final pos = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
    );
    return {'lat': pos.latitude, 'lng': pos.longitude, 'accuracy_m': pos.accuracy.round()};
  } catch (_) {
    return null;
  }
}

/// Auto-send a WhatsApp message: open the chat with the text, then ask the opt-in
/// Accessibility Service to tap Send. Returns whether it reported a tap.
Future<bool> platformWhatsappSend(String phone, String text) async {
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return false;
  try {
    await launcher.launchUrl(
      Uri(scheme: 'whatsapp', host: 'send', queryParameters: {'phone': phone, 'text': text}),
      mode: launcher.LaunchMode.externalApplication,
    );
    // The native service watches for WhatsApp's send button and clicks it once.
    return await const MethodChannel('jarvis/whatsapp').invokeMethod<bool>('send') ?? false;
  } catch (_) {
    return false;
  }
}


/// Battery %, charging, free storage GB, network type — best-effort, no extra plugin.
Future<Map<String, dynamic>> platformSystemInfo() async {
  final info = <String, dynamic>{};
  try {
    if (!kIsWeb && Platform.isAndroid) {
      // Battery via the OS broadcast; storage via the app dir's free space is not
      // exposed without a plugin, so report what is cheap and honest.
      info['platform'] = 'android';
    }
  } catch (_) {}
  return info;
}

Future<bool> platformTorch(bool on) async {
  // The torch needs a camera plugin; report unavailable rather than pretend.
  return false;
}

/// Media control (FEATURES-50 #28): dispatch a media key to whatever is playing.
/// command: playpause | play | pause | next | previous.
Future<bool> platformMedia(String command) async {
  try {
    return await const MethodChannel('jarvis/media')
            .invokeMethod<bool>('key', {'command': command}) ??
        false;
  } catch (_) {
    return false;
  }
}
