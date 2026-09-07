import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// A single, in-place-updating "live" notification that mirrors what Jarvis is doing —
/// the Android take on a Dynamic-Island live activity. No-op off Android.
class LiveActivity {
  static const _channel = MethodChannel('jarvis/live');

  static bool get supported => !kIsWeb && Platform.isAndroid;

  static String? _shownBody;

  /// Show or update the live status. [progress] 0–100, or negative for indeterminate.
  static Future<void> show(String title, String body, {int? progress}) async {
    if (!supported) return;
    if (body == _shownBody && progress == null) return; // avoid redundant updates
    _shownBody = body;
    try {
      await _channel.invokeMethod('show', {
        'title': title,
        'body': body,
        if (progress != null) 'progress': progress,
      });
    } catch (_) {}
  }

  static Future<void> hide() async {
    if (!supported) return;
    _shownBody = null;
    try {
      await _channel.invokeMethod('hide');
    } catch (_) {}
  }
}
