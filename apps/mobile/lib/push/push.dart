import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../api/client.dart';

/// Registers this phone as the first rung of the escalation ladder.
///
/// Firebase is initialised from the four public client values the server holds in its
/// settings — nothing account-specific is compiled into the APK — then the FCM token is
/// registered as a `push` endpoint. Idempotent: called on every sign-in and launch.
class PushRegistrar {
  static const _channel = MethodChannel('jarvis/push');

  static bool get supported => !kIsWeb && Platform.isAndroid;

  static Future<String?> register(JarvisClient client) async {
    if (!supported) return null;
    final settings = await client.settings();
    final fields = {
      for (final f in (settings['fields'] as List<dynamic>? ?? const []))
        (f as Map<String, dynamic>)['name'] as String: f['value']
    };
    final apiKey = '${fields['fcm_api_key'] ?? ''}';
    final appId = '${fields['fcm_app_id'] ?? ''}';
    final senderId = '${fields['fcm_sender_id'] ?? ''}';
    final projectId = '${fields['fcm_project_id'] ?? ''}';
    if ([apiKey, appId, senderId, projectId].any((v) => v.isEmpty)) return null;

    await _channel.invokeMethod('init', {
      'apiKey': apiKey,
      'appId': appId,
      'senderId': senderId,
      'projectId': projectId,
    });
    final token = await _channel.invokeMethod<String>('token');
    if (token == null || token.isEmpty) return null;
    await client.registerEndpoint(channel: 'push', address: token);
    return token;
  }
}
