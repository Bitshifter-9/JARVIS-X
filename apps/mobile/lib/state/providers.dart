import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../api/client.dart';
import '../api/models.dart';

/// Default API host. Web builds talk to localhost; an Android emulator reaches the
/// host machine at 10.0.2.2, which is the single most common first-run failure.
const defaultBaseUrl = String.fromEnvironment(
  'JARVIS_API',
  defaultValue: kIsWeb ? 'http://localhost:8000' : 'http://10.0.2.2:8000',
);

final baseUrlProvider = StateProvider<String>((ref) => defaultBaseUrl);

final secureStorageProvider = Provider<FlutterSecureStorage>(
  (ref) => const FlutterSecureStorage(),
);

final clientProvider = Provider<JarvisClient>((ref) {
  final storage = ref.watch(secureStorageProvider);
  final client = JarvisClient(
    baseUrl: ref.watch(baseUrlProvider),
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

  Future<void> signOut() async {
    _client.clearTokens();
    await _storage.deleteAll();
    state = const AuthState();
  }
}

final authProvider = StateNotifierProvider<AuthController, AuthState>(
  (ref) => AuthController(ref.watch(clientProvider), ref.watch(secureStorageProvider)),
);

final goalsProvider = FutureProvider.autoDispose<List<Goal>>(
  (ref) => ref.watch(clientProvider).goals(),
);

final predictionProvider =
    FutureProvider.autoDispose.family<Prediction, String>(
  (ref, goalId) => ref.watch(clientProvider).prediction(goalId),
);

final approvalsProvider = FutureProvider.autoDispose<List<Approval>>(
  (ref) => ref.watch(clientProvider).approvals(),
);

final devicesProvider = FutureProvider.autoDispose<List<DeviceInfo>>(
  (ref) => ref.watch(clientProvider).devices(),
);
