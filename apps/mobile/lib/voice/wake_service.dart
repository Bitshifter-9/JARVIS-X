import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:speech_to_text/speech_to_text.dart';

import '../state/providers.dart';
import 'speaker.dart';

/// "Hey Jarvis" with the screen off — Android, and **free**: no cloud wake service and
/// no Picovoice AccessKey.
///
/// A foreground service of type *microphone* keeps the process alive and the mic
/// permitted. We listen on-device with the platform recogniser (the same free
/// `speech_to_text` the mic button uses) and watch the transcript for the word
/// "jarvis". The audio is transcribed on the phone by the OS recogniser and never
/// leaves it until the wake word fires; on a hit we take whatever the user said after
/// "jarvis" as the command (or listen once more if they only said the name), ask the
/// same `/v1/chat` the app uses, and speak the reply.
///
/// This trades a little battery for zero cost and zero setup versus a dedicated wake
/// model. The Mac node already runs the neural wake word (openWakeWord's `hey_jarvis`);
/// a Flutter port of that is the upgrade path if battery becomes a concern.
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

/// The foreground service needs a Dart entry point even though the work happens in
/// the main isolate; this one just keeps the notification up.
@pragma('vm:entry-point')
void wakeServiceEntry() => FlutterForegroundTask.setTaskHandler(_IdleHandler());

class _IdleHandler extends TaskHandler {
  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {}
  @override
  void onRepeatEvent(DateTime timestamp) {}
  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {}
}

class WakeController extends StateNotifier<WakeState> {
  WakeController(this._ref) : super(const WakeState());

  final Ref _ref;
  final _stt = SpeechToText();
  Speaker? _speaker;
  final _history = <Map<String, String>>[];
  bool _armed = false; // keep re-listening for the wake word
  bool _handling = false; // a wake hit is being handled; ignore the recogniser

  static bool get supported => !kIsWeb && Platform.isAndroid;

  Future<void> start() async {
    if (!supported) {
      state = state.copyWith(phase: WakePhase.error, error: 'Always-on listening is Android only');
      return;
    }
    try {
      final ok = await _stt.initialize(
        onStatus: _onSttStatus,
        onError: (_) {
          // Recogniser errors (no match, network hiccup) are normal in a long listen;
          // the status handler restarts us. Nothing to surface.
        },
      );
      if (!ok) throw StateError('microphone permission was refused');

      FlutterForegroundTask.init(
        androidNotificationOptions: AndroidNotificationOptions(
          channelId: 'jarvis_listening',
          channelName: 'JARVIS is listening',
          channelDescription: 'Say "Jarvis" to talk, even with the screen off',
          channelImportance: NotificationChannelImportance.LOW,
          priority: NotificationPriority.LOW,
        ),
        iosNotificationOptions: const IOSNotificationOptions(),
        foregroundTaskOptions: ForegroundTaskOptions(
          eventAction: ForegroundTaskEventAction.nothing(),
          allowWakeLock: true,
          allowWifiLock: false,
        ),
      );
      await FlutterForegroundTask.requestNotificationPermission();
      await FlutterForegroundTask.startService(
        serviceId: 256,
        notificationTitle: 'JARVIS is listening',
        notificationText: 'Say "Jarvis" to talk',
        callback: wakeServiceEntry,
      );

      _speaker = Speaker(_ref.read(clientProvider))
        ..onSpeaking = (s) {
          if (s) state = state.copyWith(phase: WakePhase.speaking);
        };
      _armed = true;
      state = const WakeState(phase: WakePhase.armed);
      await _listenForWake();
    } catch (e) {
      await stop();
      state = state.copyWith(phase: WakePhase.error, error: '$e');
    }
  }

  Future<void> stop() async {
    _armed = false;
    try {
      await _stt.stop();
    } catch (_) {}
    _speaker?.dispose();
    _speaker = null;
    if (supported && await FlutterForegroundTask.isRunningService) {
      await FlutterForegroundTask.stopService();
    }
    state = const WakeState();
  }

  /// The recogniser stops itself on silence; while armed and idle, start it again so
  /// listening is effectively continuous.
  void _onSttStatus(String status) {
    if (!_armed || _handling) return;
    if (status == 'done' || status == 'notListening') {
      scheduleMicrotask(() {
        if (_armed && !_handling && !_stt.isListening) _listenForWake();
      });
    }
  }

  Future<void> _listenForWake() async {
    if (!_armed || _handling || _stt.isListening) return;
    try {
      await _stt.listen(
        onResult: (r) {
          if (_handling) return;
          if (commandAfterWake(r.recognizedWords) != null) {
            _onWake(r.recognizedWords);
          }
        },
        listenOptions: SpeechListenOptions(
          listenMode: ListenMode.dictation,
          partialResults: true,
          cancelOnError: false,
          listenFor: const Duration(minutes: 4),
          pauseFor: const Duration(seconds: 6),
        ),
      );
    } catch (_) {
      // Retry on the next status callback.
    }
  }

  Future<void> _onWake(String heard) async {
    if (_handling) return;
    _handling = true;
    await _stt.stop();
    HapticFeedback.lightImpact();
    SystemSound.play(SystemSoundType.click);
    try {
      // If they said "jarvis, <command>" in one breath, use the tail; otherwise listen.
      var command = commandAfterWake(heard) ?? '';
      if (command.isEmpty) {
        state = state.copyWith(phase: WakePhase.listening);
        command = await _listenOnce();
      }
      if (command.isEmpty) return;
      state = state.copyWith(phase: WakePhase.thinking, lastHeard: command);
      _history.add({'role': 'user', 'content': command});
      final reply = await _ref.read(clientProvider).chat(
            _history.length <= 10 ? _history : _history.sublist(_history.length - 10),
          );
      final text = (reply['text'] as String?) ?? '';
      _history.add({'role': 'assistant', 'content': text});
      state = state.copyWith(phase: WakePhase.speaking, lastReply: text);
      await _speaker?.say(text);
    } catch (e) {
      await _speaker?.say('Sorry, that did not work.');
      state = state.copyWith(error: '$e');
    } finally {
      _handling = false;
      if (_armed) {
        state = state.copyWith(phase: WakePhase.armed);
        await _listenForWake();
      }
    }
  }

  Future<String> _listenOnce() async {
    final done = Completer<String>();
    await _stt.listen(
      listenOptions: SpeechListenOptions(
        listenFor: const Duration(seconds: 12),
        pauseFor: const Duration(seconds: 2),
      ),
      onResult: (r) {
        if (r.finalResult && !done.isCompleted) done.complete(r.recognizedWords);
      },
    );
    final words = await done.future.timeout(const Duration(seconds: 14), onTimeout: () => '');
    await _stt.stop();
    return words.trim();
  }
}

final wakeProvider = StateNotifierProvider<WakeController, WakeState>((ref) => WakeController(ref));
