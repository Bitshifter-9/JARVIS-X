import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';

/// Offline detection + a tiny outbox (FEATURES-50 #46). The client flips [offline] on a
/// network error and clears it on the next success; actions taken while offline are
/// queued and replayed when connectivity returns.
class Net {
  Net._();

  static final ValueNotifier<bool> offline = ValueNotifier<bool>(false);

  /// True for the errors that mean "no network", not "the server said no".
  static bool isNetworkError(Object e) {
    final s = e.toString().toLowerCase();
    return s.contains('socketexception') ||
        s.contains('failed host lookup') ||
        s.contains('connection refused') ||
        s.contains('connection closed') ||
        s.contains('network is unreachable') ||
        s.contains('timeoutexception') ||
        s.contains('clientexception');
  }

  static void markOffline() {
    if (!offline.value) offline.value = true;
  }

  static void markOnline() {
    if (offline.value) offline.value = false;
  }
}

/// A durable queue of actions to replay when back online. Kept tiny on purpose — the one
/// thing worth not losing offline is a captured task.
class Outbox {
  static const _key = 'outbox';

  static Future<void> enqueue(Map<String, dynamic> action) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final list = prefs.getStringList(_key) ?? <String>[];
      list.add(jsonEncode(action));
      await prefs.setStringList(_key, list);
    } catch (_) {}
  }

  static Future<int> pending() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return (prefs.getStringList(_key) ?? const []).length;
    } catch (_) {
      return 0;
    }
  }

  /// Replay every queued action; anything that fails with a network error is kept for
  /// the next attempt. Returns how many were sent.
  static Future<int> flush(JarvisClient client) async {
    List<String> list;
    SharedPreferences prefs;
    try {
      prefs = await SharedPreferences.getInstance();
      list = prefs.getStringList(_key) ?? <String>[];
    } catch (_) {
      return 0;
    }
    if (list.isEmpty) return 0;
    final remaining = <String>[];
    var sent = 0;
    for (final raw in list) {
      try {
        final action = jsonDecode(raw) as Map<String, dynamic>;
        if (action['type'] == 'quickAdd') {
          await client.quickAdd(action['text'] as String);
          sent++;
        }
      } on Object catch (e) {
        if (Net.isNetworkError(e)) {
          remaining.add(raw); // still offline — keep it
        }
        // a real rejection (400) is dropped, not retried forever
      }
    }
    await prefs.setStringList(_key, remaining);
    return sent;
  }
}
