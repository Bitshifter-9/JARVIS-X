import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:porcupine_flutter/porcupine_error.dart';
import 'package:porcupine_flutter/porcupine_manager.dart';
import 'package:porcupine_flutter/porcupine.dart';
import 'package:speech_to_text/speech_to_text.dart';

import '../state/providers.dart';
import 'speaker.dart';

/// "Hey Jarvis" with the screen off — Android.
///
/// A foreground service of type *microphone* keeps the process alive and the mic
/// permitted; Porcupine listens on-device for the single word "Jarvis" (it does not
/// transcribe the room); on a hit it hands the mic to speech recognition for one
/// utterance, asks the same `/v1/chat` the app uses, and speaks the reply. Nothing is
/// sent anywhere until the wake word fires, and the wake model never leaves the phone.
enum WakePhase { off, armed, listening, thinking, speaking, error }

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
  PorcupineManager? _porcupine;
  final _stt = SpeechToText();
  Speaker? _speaker;
  final _history = <Map<String, String>>[];

  static bool get supported => !kIsWeb && Platform.isAndroid;

  Future<void> start({required String accessKey}) async {
    if (!supported) {
      state = state.copyWith(phase: WakePhase.error, error: 'Always-on listening is Android only');
      return;
    }
    if (accessKey.isEmpty) {
      state = state.copyWith(
          phase: WakePhase.error,
          error: 'Add a Picovoice AccessKey in Settings (free at console.picovoice.ai)');
      return;
    }
    try {
      // Microphone permission, via the recogniser's own prompt.
      final ok = await _stt.initialize();
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

      _porcupine = await PorcupineManager.fromBuiltInKeywords(
        accessKey,
        [BuiltInKeyword.JARVIS],
        _onWake,
        errorCallback: (PorcupineException e) =>
            state = state.copyWith(phase: WakePhase.error, error: e.message),
      );
      await _porcupine!.start();
      _speaker = Speaker(_ref.read(clientProvider))
        ..onSpeaking = (s) {
          if (s) state = state.copyWith(phase: WakePhase.speaking);
        };
      state = const WakeState(phase: WakePhase.armed);
    } catch (e) {
      await stop();
      state = state.copyWith(phase: WakePhase.error, error: '$e');
    }
  }

  Future<void> stop() async {
    try {
      await _porcupine?.stop();
      await _porcupine?.delete();
    } catch (_) {}
    _porcupine = null;
    await _stt.stop();
    _speaker?.dispose();
    _speaker = null;
    if (supported && await FlutterForegroundTask.isRunningService) {
      await FlutterForegroundTask.stopService();
    }
    state = const WakeState();
  }

  Future<void> _onWake(int keywordIndex) async {
    if (state.phase != WakePhase.armed) return;
    // Porcupine and the recogniser cannot share the microphone; hand it over.
    await _porcupine?.stop();
    HapticFeedback.lightImpact();
    SystemSound.play(SystemSoundType.click);
    state = state.copyWith(phase: WakePhase.listening);
    try {
      final heard = await _listenOnce();
      if (heard.isEmpty) return;
      state = state.copyWith(phase: WakePhase.thinking, lastHeard: heard);
      _history.add({'role': 'user', 'content': heard});
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
      try {
        await _porcupine?.start();
        state = state.copyWith(phase: WakePhase.armed);
      } catch (e) {
        state = state.copyWith(phase: WakePhase.error, error: '$e');
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
