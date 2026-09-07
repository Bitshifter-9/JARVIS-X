import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// The last good answer for every GET, in memory and on disk.
///
/// This is why screens open instantly: a provider yields the cached body first and the
/// fresh one when the network answers (stale-while-revalidate). Nothing sensitive lives
/// here that the screen would not show anyway; sign-out clears it.
class ResponseCache {
  ResponseCache({SharedPreferences? prefs}) : _prefs = prefs;

  static const _prefix = 'cache:';
  static const maxEntries = 40;

  SharedPreferences? _prefs;
  final Map<String, dynamic> _memory = {};

  Future<void> warm() async {
    try {
      _prefs ??= await SharedPreferences.getInstance();
      for (final key in _prefs!.getKeys().where((k) => k.startsWith(_prefix))) {
        final raw = _prefs!.getString(key);
        if (raw != null) _memory[key.substring(_prefix.length)] = jsonDecode(raw);
      }
    } catch (_) {
      // No disk cache on this platform — memory only is fine.
    }
  }

  dynamic get(String key) => _memory[key];

  void put(String key, dynamic body) {
    _memory[key] = body;
    if (_memory.length > maxEntries) _memory.remove(_memory.keys.first);
    final prefs = _prefs;
    if (prefs == null) return;
    // Fire and forget: a failed write costs nothing but a slower next launch.
    prefs.setString('$_prefix$key', jsonEncode(body)).catchError((_) => false);
  }

  Future<void> clear() async {
    _memory.clear();
    final prefs = _prefs;
    if (prefs == null) return;
    for (final key in prefs.getKeys().where((k) => k.startsWith(_prefix)).toList()) {
      await prefs.remove(key);
    }
  }
}
