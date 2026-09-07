import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../api/cache.dart';
import '../api/client.dart';
import '../api/models.dart';
import 'dart:io' show Platform;

import '../node/activity_sampler.dart';
import '../node/mac_hands.dart';
import '../node/notification_mirror.dart';
import '../node/phone_node.dart';
import '../node/platform_hooks.dart';

/// Default API host. Web builds talk to localhost; an Android emulator reaches the
/// host machine at 10.0.2.2, which is the single most common first-run failure.
const defaultBaseUrl = String.fromEnvironment(
  'JARVIS_API',
  defaultValue: kIsWeb ? 'http://localhost:8000' : 'http://10.0.2.2:8000',
);

final baseUrlProvider = StateProvider<String>((ref) => defaultBaseUrl);

final secureStorageProvider = Provider<FlutterSecureStorage>(
  (ref) => const FlutterSecureStorage(
    // The data-protection keychain needs a Developer-ID-signed app with a team id; an
    // ad-hoc build's writes fail silently and every launch asks you to sign in again.
    mOptions: MacOsOptions(useDataProtectionKeyChain: false),
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  ),
);

/// One disk-backed cache for the life of the app, warmed before the first screen.
final responseCacheProvider = Provider<ResponseCache>((ref) => ResponseCache());

final clientProvider = Provider<JarvisClient>((ref) {
  final storage = ref.watch(secureStorageProvider);
  final client = JarvisClient(
    baseUrl: ref.watch(baseUrlProvider),
    cache: ref.watch(responseCacheProvider),
    onTokens: (access, refresh) async {
      // Keychain on iOS, Keystore-backed on Android. On web this degrades to
      // localStorage, which is why web is a development surface only.
      await storage.write(key: 'access_token', value: access);
      await storage.write(key: 'refresh_token', value: refresh);
    },
  );
  ref.onDispose(client.close);
  return client;
});

class AuthState {
  const AuthState({this.email, this.loading = false, this.error});

  final String? email;
  final bool loading;
  final String? error;

  bool get signedIn => email != null;

  AuthState copyWith({String? email, bool? loading, String? error, bool clearError = false}) =>
      AuthState(
        email: email ?? this.email,
        loading: loading ?? this.loading,
        error: clearError ? null : (error ?? this.error),
      );
}

class AuthController extends StateNotifier<AuthState> {
  AuthController(this._client, this._storage) : super(const AuthState());

  final JarvisClient _client;
  final FlutterSecureStorage _storage;

  Future<void> restore() async {
    final access = await _storage.read(key: 'access_token');
    final refresh = await _storage.read(key: 'refresh_token');
    if (access == null || refresh == null) return;

    _client.setTokens(access, refresh);
    try {
      final me = await _client.me();
      state = state.copyWith(email: me['email'] as String);
    } on ProblemException {
      _client.clearTokens();
      await _storage.deleteAll();
    }
  }

  Future<void> signIn(String email, String password) async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      await _client.login(email, password);
      final me = await _client.me();
      state = AuthState(email: me['email'] as String);
    } on ProblemException catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    } catch (e) {
      state = state.copyWith(loading: false, error: 'Cannot reach the API: $e');
    }
  }

  /// Sign in with Google: the browser flow finished and handed us a session.
  Future<void> adoptTokens(String access, String refresh) async {
    _client.setTokens(access, refresh);
    try {
      final me = await _client.me();
      state = AuthState(email: me['email'] as String);
    } on ProblemException catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  void setLoading(bool loading) =>
      state = state.copyWith(loading: loading, clearError: true);

  Future<void> signOut() async {
    _client.clearTokens();
    await _storage.deleteAll();
    state = const AuthState();
  }
}

final authProvider = StateNotifierProvider<AuthController, AuthState>(
  (ref) => AuthController(ref.watch(clientProvider), ref.watch(secureStorageProvider)),
);

final goalsProvider = StreamProvider.autoDispose<List<Goal>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/goals',
        parse: (j) => (j as List).map((e) => Goal.fromJson(e as Map<String, dynamic>)).toList(),
      ),
);

final predictionProvider =
    FutureProvider.autoDispose.family<Prediction, String>(
  (ref, goalId) => ref.watch(clientProvider).prediction(goalId),
);

final approvalsProvider = StreamProvider.autoDispose<List<Approval>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/approvals',
        parse: (j) =>
            (j as List).map((e) => Approval.fromJson(e as Map<String, dynamic>)).toList(),
      ),
);

final devicesProvider = StreamProvider.autoDispose<List<DeviceInfo>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/devices',
        parse: (j) =>
            (j as List).map((e) => DeviceInfo.fromJson(e as Map<String, dynamic>)).toList(),
      ),
);

final videoRunsProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.watch(clientProvider).videoRuns(),
);

final videoAssetsProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(clientProvider).videoAssets(),
);

final videoAnalyticsProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(clientProvider).videoAnalytics(),
);

final settingsProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(clientProvider).settings(),
);

final connectorsProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.watch(clientProvider).connectors(),
);

final timelineProvider = StreamProvider.autoDispose<List<TimelineEntry>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/timeline',
        query: {'limit': '100'},
        parse: (j) =>
            (j as List).map((e) => TimelineEntry.fromJson(e as Map<String, dynamic>)).toList(),
      ),
);

/// This device as an execution node. The node itself is pure Dart (so the protocol is
/// unit-tested and cross-checked against the server without Flutter); this adapter
/// turns its change stream into widget rebuilds.
class PhoneNodeNotifier extends ChangeNotifier {
  PhoneNodeNotifier(this.node) {
    _sub = node.changes.listen((_) => notifyListeners());
  }

  final PhoneNode node;
  late final StreamSubscription<void> _sub;
  bool _booted = false;

  /// Restore the pairing from storage and connect — once per session, no tap needed.
  Future<void> boot(FlutterSecureStorage storage, JarvisClient client) async {
    if (!_booted) {
      _booted = true;
      if (!node.paired) {
        node.restore(
          scalarHex: await storage.read(key: 'phone_node_scalar'),
          deviceId: await storage.read(key: 'phone_node_device_id'),
          serverPublicPem: await storage.read(key: 'phone_node_server_pem'),
        );
      }
    }
    final token = client.accessToken;
    if (node.paired && !node.connected && token != null) {
      try {
        await node.connect(wsBase: client.wsBase, accessToken: token);
      } catch (_) {
        // The reconnect timer inside the node keeps trying.
      }
    }
  }

  @override
  void dispose() {
    _sub.cancel();
    node.dispose();
    super.dispose();
  }
}

final phoneNodeProvider = ChangeNotifierProvider<PhoneNodeNotifier>((ref) {
  Future<String?> upload(String kind, String filename, List<int> bytes) async {
    final id = await ref.read(secureStorageProvider).read(key: 'phone_node_device_id');
    if (id == null) return null;
    final r = await ref.read(clientProvider).uploadArtifact(id, kind, filename, bytes);
    return r['id'] as String?;
  }

  return PhoneNodeNotifier(PhoneNode(
    launchUrl: platformLaunchUrl,
    launchApp: platformLaunchApp,
    notify: platformNotify,
    launchIntent: platformLaunchIntent,
    placeCall: platformPlaceCall,
    ring: platformRing,
    locate: platformLocate,
    whatsappSend: platformWhatsappSend,
    systemInfo: platformSystemInfo,
    torch: platformTorch,
    media: platformMedia,
    captureImage: platformCaptureImage,
    uploadArtifact: upload,
    readClipboard: platformReadClipboard,
    writeClipboard: platformWriteClipboard,
    // On a Mac this app answers the Mac verbs itself (PLAN.md 11.1).
    macHands: !kIsWeb && Platform.isMacOS
        ? MacHands(run: realRunner, uploadArtifact: upload)
        : null,
  ));
});

/// System / dark / light, remembered on the device.
class ThemeModeController extends StateNotifier<ThemeMode> {
  ThemeModeController(this._storage) : super(ThemeMode.dark);
  final FlutterSecureStorage _storage;

  Future<void> restore() async {
    final v = await _storage.read(key: 'theme_mode');
    state = ThemeMode.values.firstWhere((m) => m.name == v, orElse: () => ThemeMode.dark);
  }

  Future<void> set(ThemeMode mode) async {
    state = mode;
    await _storage.write(key: 'theme_mode', value: mode.name);
  }
}

final themeModeProvider = StateNotifierProvider<ThemeModeController, ThemeMode>(
  (ref) => ThemeModeController(ref.watch(secureStorageProvider)),
);

/// Which tab the shell shows. A provider so any screen (the HUD's tiles) can switch it.
final homeTabProvider = StateProvider<int>((ref) => 0);

final hudProvider = StreamProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/hud',
        parse: (j) => j as Map<String, dynamic>,
      ),
);

final routinesProvider = StreamProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/routines',
        parse: (j) => (j as List).cast<Map<String, dynamic>>(),
      ),
);

/// The live feed, reconnecting after a pause whenever the server closes it or it fails.
final liveProvider = StreamProvider.autoDispose<Map<String, dynamic>>((ref) async* {
  final client = ref.watch(clientProvider);
  while (true) {
    try {
      await for (final event in client.live()) {
        yield event;
      }
    } catch (_) {
      // fall through to the pause and reconnect
    }
    await Future<void>.delayed(const Duration(seconds: 3));
  }
});

final personasProvider = StreamProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/personas',
        parse: (j) => (j as List).cast<Map<String, dynamic>>(),
      ),
);

final notificationMirrorProvider = ChangeNotifierProvider<NotificationMirror>(
  (ref) => NotificationMirror(ref.watch(secureStorageProvider)),
);

final activitySamplerProvider = ChangeNotifierProvider<ActivitySampler>(
  (ref) => ActivitySampler(ref.watch(secureStorageProvider)),
);

final suggestionsProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.watch(clientProvider).suggestions(),
);

final permissionsProvider = StreamProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/permissions',
        parse: (j) => (j as List).cast<Map<String, dynamic>>(),
      ),
);

final tasksProvider = StreamProvider.autoDispose<List<Task>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/tasks',
        query: {'status': 'open'},
        parse: (j) => (j as List).map((e) => Task.fromJson(e as Map<String, dynamic>)).toList(),
      ),
);

/// Every task including done ones, for the "Completed" section on Goals.
final allTasksProvider = StreamProvider.autoDispose<List<Task>>(
  (ref) => ref.watch(clientProvider).staleWhileRevalidate(
        '/v1/tasks',
        query: {'status': 'all'},
        parse: (j) => (j as List).map((e) => Task.fromJson(e as Map<String, dynamic>)).toList(),
      ),
);

/// Senders/channels you disliked — the Muted section on Goals (#14).
final reminderMutesProvider =
    FutureProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.watch(clientProvider).reminderMutes(),
);

/// Text handed to the chat from elsewhere (the Home ask-bar): the chat sends it.
final chatPrefillProvider = StateProvider<String?>((ref) => null);

final trustStatusProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(clientProvider).trustStatus(),
);

final profileProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(clientProvider).profile(),
);

final focusProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(clientProvider).focus(),
);
