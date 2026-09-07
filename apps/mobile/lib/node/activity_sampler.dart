import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../api/client.dart';

/// Which app is in front, once a minute, app names only (PLAN.md 10.6.3). Android
/// only — the Mac helper samples the Mac. Off until the owner grants usage access in
/// system settings *and* switches it on here.
class ActivitySampler extends ChangeNotifier {
  ActivitySampler(this._storage);

  static const _channel = MethodChannel('jarvis/usage');
  static bool get supported => !kIsWeb && Platform.isAndroid;

  final FlutterSecureStorage _storage;
  bool on = false;
  bool accessGranted = false;
  int posted = 0;
  String? lastError;
  int _since = 0;
  Timer? _timer;

  Future<void> restore() async {
    try {
      on = await _storage.read(key: 'activity_share_on') == '1';
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
    await _storage.write(key: 'activity_share_on', value: value ? '1' : '0');
    notifyListeners();
  }

  void start(JarvisClient client, Future<String?> Function() deviceId) {
    _timer?.cancel();
    if (!supported) return;
    _timer = Timer.periodic(const Duration(seconds: 60), (_) => _tick(client, deviceId));
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> _tick(JarvisClient client, Future<String?> Function() deviceId) async {
    if (!on) return;
    try {
      final raw = await _channel.invokeMethod<List<dynamic>>('recent', {'since': _since}) ??
          const [];
      final items = [
        for (final e in raw.cast<Map<dynamic, dynamic>>())
          {
            'app': e['app'] ?? e['package'],
            'title': null,
            'at': DateTime.fromMillisecondsSinceEpoch((e['at'] as num).toInt(), isUtc: true)
                .toIso8601String(),
          },
      ];
      _since = DateTime.now().millisecondsSinceEpoch;
      if (items.isEmpty) return;
      final id = await deviceId();
      if (id == null) return;
      final r = await client.postActivity(id, items);
      posted += (r['stored'] as num?)?.toInt() ?? 0;
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
