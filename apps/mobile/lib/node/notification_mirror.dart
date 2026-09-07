import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../api/client.dart';

/// Forwards this phone's notification titles to the server so routines can react to
/// them ("when a WhatsApp from Amma arrives…"). Opt-in twice: the user grants
/// notification access in system settings *and* switches this on. Only allowlisted
/// apps are forwarded, titles only unless `withText` is on.
class NotificationMirror extends ChangeNotifier {
  NotificationMirror(this._storage);

  static const _channel = MethodChannel('jarvis/notifications');
  static bool get supported => !kIsWeb && Platform.isAndroid;

  static const defaultPackages = {
    'com.whatsapp',
    'com.google.android.gm',
    'com.Slack',
    'com.google.android.apps.messaging',
    'com.google.android.dialer',
    'com.google.android.calendar',
  };

  final FlutterSecureStorage _storage;
  bool on = false;
  bool withText = false;
  bool accessGranted = false;
  int forwarded = 0;
  String? lastError;
  Set<String> packages = defaultPackages;
  Timer? _timer;

  Future<void> restore() async {
    try {
      on = await _storage.read(key: 'notif_mirror_on') == '1';
      withText = await _storage.read(key: 'notif_mirror_text') == '1';
    } catch (_) {}
    await refreshAccess();
    notifyListeners();
  }

  Future<void> refreshAccess() async {
    if (!supported) return;
    try {
      accessGranted = await _channel.invokeMethod<bool>('enabled') ?? false;
    } catch (_) {
      accessGranted = false;
    }
  }

  Future<void> openSettings() => _channel.invokeMethod('openSettings');

  Future<void> setOn(bool value) async {
    on = value;
    await _storage.write(key: 'notif_mirror_on', value: value ? '1' : '0');
    notifyListeners();
  }

  Future<void> setWithText(bool value) async {
    withText = value;
    await _storage.write(key: 'notif_mirror_text', value: value ? '1' : '0');
    notifyListeners();
  }

  /// Poll the native queue every 20 s while signed in; forward what is allowed.
  void start(JarvisClient client, Future<String?> Function() deviceId) {
    _timer?.cancel();
    if (!supported) return;
    _timer = Timer.periodic(const Duration(seconds: 20), (_) => _tick(client, deviceId));
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> _tick(JarvisClient client, Future<String?> Function() deviceId) async {
    if (!on) return;
    try {
      final raw = await _channel.invokeMethod<List<dynamic>>('drain') ?? const [];
      final items = [
        for (final e in raw.cast<Map<dynamic, dynamic>>())
          if (packages.contains(e['package']))
            {
              'package': e['package'],
              'app': e['app'] ?? '',
              'title': e['title'] ?? '',
              'text': withText ? (e['text'] ?? '') : '',
              'at': e['at'],
              'key': e['key'],
            },
      ];
      if (items.isEmpty) return;
      final id = await deviceId();
      if (id == null) return;
      final r = await client.postNotifications(id, items);
      forwarded += (r['new'] as num?)?.toInt() ?? 0;
      lastError = null;
    } catch (e) {
      lastError = '$e';
    }
    notifyListeners();
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }
}
