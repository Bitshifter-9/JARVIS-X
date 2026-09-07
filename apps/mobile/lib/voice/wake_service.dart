import 'dart:async';
import 'dart:convert';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;
import 'package:speech_to_text/speech_to_text.dart';

/// "Hey Jarvis" that keeps working even when the app is closed.
///
/// The wake loop runs inside the **foreground service's own isolate** (not the UI
/// isolate), so it survives the app being swiped away and is restarted on boot. It reads
/// the API base URL and token straight from secure storage, listens on-device for
/// "jarvis", and on a hit captures the command, asks `/v1/chat`, and speaks the reply —
/// all free and on-device except the one chat call you actually triggered.
enum WakePhase { off, armed, listening, thinking, speaking, error }

/// The command that follows the wake word in [heard], or null if "jarvis" is not
/// present. "jarvis what's next" → "what's next"; "jarvis" → "". Pure, so it is tested.
String? commandAfterWake(String heard) {
  final m = RegExp(r'\bjarvis\b[\s,.:;!?-]*', caseSensitive: false).firstMatch(heard);
  if (m == null) return null;
  return heard.substring(m.end).trim();
}

class WakeState {
  const WakeState({this.phase = WakePhase.off, this.lastHeard, this.lastReply, this.error});
  final WakePhase phase;
  final String? lastHeard;
  final String? lastReply;
  final String? error;

  WakeState copyWith({WakePhase? phase, String? lastHeard, String? lastReply, String? error}) =>
      WakeState(
        phase: phase ?? this.phase,
        lastHeard: lastHeard ?? this.lastHeard,
        lastReply: lastReply ?? this.lastReply,
        error: error,
      );

  bool get on => phase != WakePhase.off && phase != WakePhase.error;
}

/// The entry point the OS calls to (re)start the service — on demand, and on boot.
@pragma('vm:entry-point')
void wakeServiceEntry() => FlutterForegroundTask.setTaskHandler(_WakeTaskHandler());

/// Runs in the service isolate. No Riverpod, no widgets — raw HTTP + on-device STT/TTS.
class _WakeTaskHandler extends TaskHandler {
  final _stt = SpeechToText();
  final _tts = FlutterTts();
  final _storage = const FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  bool _armed = false;
  bool _handling = false;
  String? _base;
  final _history = <Map<String, String>>[];

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    _base = await _storage.read(key: 'base_url');
    try {
      final ok = await _stt.initialize(onStatus: _onSttStatus, onError: (_) {});
      if (!ok) return;
    } catch (_) {
      return;
    }
    _armed = true;
    _send('armed');
    await _listen();
  }

  void _onSttStatus(String status) {
    if (!_armed || _handling) return;
    if (status == 'done' || status == 'notListening') {
      scheduleMicrotask(() {
        if (_armed && !_handling && !_stt.isListening) _listen();
      });
    }
  }

  Future<void> _listen() async {
    if (!_armed || _handling || _stt.isListening) return;
    try {
      await _stt.listen(
        onResult: (r) {
          if (_handling) return;
          if (commandAfterWake(r.recognizedWords) != null) _onWake(r.recognizedWords);
        },
        listenOptions: SpeechListenOptions(
          listenMode: ListenMode.dictation,
          partialResults: true,
          cancelOnError: false,
          listenFor: const Duration(minutes: 4),
          pauseFor: const Duration(seconds: 6),
        ),
      );
    } catch (_) {}
  }

  Future<void> _onWake(String heard) async {
    if (_handling) return;
    _handling = true;
    await _stt.stop();
    _send('listening');
    try {
      var command = commandAfterWake(heard) ?? '';
      if (command.isEmpty) command = await _listenOnce();
      if (command.isEmpty) return;
      _send('thinking');
      _history.add({'role': 'user', 'content': command});
      final reply = await _chat();
      _history.add({'role': 'assistant', 'content': reply});
      _send('speaking');
      await _tts.speak(reply);
    } catch (_) {
      try {
        await _tts.speak('Sorry, that did not work.');
      } catch (_) {}
    } finally {
      _handling = false;
      if (_armed) {
        _send('armed');
        await _listen();
      }
    }
  }

  Future<String> _listenOnce() async {
    final done = Completer<String>();
    await _stt.listen(
      listenOptions: SpeechListenOptions(
        listenFor: Duration(seconds: 12),
        pauseFor: Duration(seconds: 2),
      ),
      onResult: (r) {
        if (r.finalResult && !done.isCompleted) done.complete(r.recognizedWords);
      },
    );
    final words = await done.future.timeout(const Duration(seconds: 14), onTimeout: () => '');
    await _stt.stop();
    return words.trim();
  }

  /// One chat call, refreshing the access token once if it has expired.
  Future<String> _chat() async {
    if (_base == null) return '';
    final trimmed = _history.length <= 10 ? _history : _history.sublist(_history.length - 10);
    final body = jsonEncode({'messages': trimmed});

    Future<http.Response> post(String? token) => http.post(
          Uri.parse('$_base/v1/chat'),
          headers: {
            'Content-Type': 'application/json',
            if (token != null) 'Authorization': 'Bearer $token',
          },
          body: body,
        );

    var token = await _storage.read(key: 'access_token');
    var res = await post(token);
    if (res.statusCode == 401 && await _refresh()) {
      token = await _storage.read(key: 'access_token');
      res = await post(token);
    }
    if (res.statusCode >= 400) return 'Sorry, that did not work.';
    return (jsonDecode(res.body)['text'] as String?) ?? '';
  }

  Future<bool> _refresh() async {
    final refresh = await _storage.read(key: 'refresh_token');
    if (refresh == null || _base == null) return false;
    try {
      final res = await http.post(
        Uri.parse('$_base/v1/auth/refresh'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'refresh_token': refresh}),
      );
      if (res.statusCode >= 400) return false;
      final data = jsonDecode(res.body) as Map<String, dynamic>;
      await _storage.write(key: 'access_token', value: data['access_token'] as String);
      await _storage.write(key: 'refresh_token', value: data['refresh_token'] as String);
      return true;
    } catch (_) {
      return false;
    }
  }

  void _send(String phase) => FlutterForegroundTask.sendDataToMain({'wake': phase});

  @override
  void onRepeatEvent(DateTime timestamp) {}

  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {
    _armed = false;
    try {
      await _stt.stop();
    } catch (_) {}
  }
}

class WakeController extends StateNotifier<WakeState> {
  WakeController() : super(const WakeState());

  static bool get supported => !kIsWeb && Platform.isAndroid;

  Future<void> start() async {
    if (!supported) {
      state = state.copyWith(phase: WakePhase.error, error: 'Always-on listening is Android only');
      return;
    }
    try {
      FlutterForegroundTask.init(
        androidNotificationOptions: AndroidNotificationOptions(
          channelId: 'jarvis_listening',
          channelName: 'JARVIS is listening',
          channelDescription: 'Say "Jarvis" to talk, even with the screen off',
          channelImportance: NotificationChannelImportance.LOW,
          priority: NotificationPriority.LOW,
        ),
        iosNotificationOptions: const IOSNotificationOptions(),
        // Keep running when the app is closed, and come back on reboot.
        foregroundTaskOptions: ForegroundTaskOptions(
          eventAction: ForegroundTaskEventAction.nothing(),
          allowWakeLock: true,
          allowWifiLock: true,
          autoRunOnBoot: true,
          autoRunOnMyPackageReplaced: true,
        ),
      );
      await FlutterForegroundTask.requestNotificationPermission();
      FlutterForegroundTask.addTaskDataCallback(_onServiceData);
      await FlutterForegroundTask.startService(
        serviceId: 256,
        notificationTitle: 'JARVIS is listening',
        notificationText: 'Say "Jarvis" to talk',
        callback: wakeServiceEntry,
      );
      state = const WakeState(phase: WakePhase.armed);
    } catch (e) {
      state = state.copyWith(phase: WakePhase.error, error: '$e');
    }
  }

  void _onServiceData(Object data) {
    if (data is! Map || data['wake'] is! String) return;
    final phase = switch (data['wake'] as String) {
      'armed' => WakePhase.armed,
      'listening' => WakePhase.listening,
      'thinking' => WakePhase.thinking,
      'speaking' => WakePhase.speaking,
      _ => WakePhase.armed,
    };
    if (mounted) state = state.copyWith(phase: phase);
  }

  Future<void> stop() async {
    FlutterForegroundTask.removeTaskDataCallback(_onServiceData);
    if (supported && await FlutterForegroundTask.isRunningService) {
      await FlutterForegroundTask.stopService();
    }
    state = const WakeState();
  }
}

final wakeProvider = StateNotifierProvider<WakeController, WakeState>((ref) => WakeController());
