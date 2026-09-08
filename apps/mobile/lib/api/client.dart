import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../state/connectivity.dart';
import 'cache.dart';
import 'models.dart';

/// Typed HTTP client for the JARVIS X API.
///
/// Refresh is handled here rather than at call sites: an access token lives 15 minutes,
/// and a screen that has to think about that will eventually forget.
/// The session's tokens, held outside any one client instance. When `clientProvider`
/// rebuilds (e.g. the base URL is set on launch), the new client seeds from here, so it
/// never comes up unauthenticated and hits "Missing bearer token".
class SessionTokens {
  static String? access;
  static String? refresh;
}

class JarvisClient {
  JarvisClient({
    required this.baseUrl,
    http.Client? httpClient,
    this.onTokens,
    ResponseCache? cache,
  })  : _http = httpClient ?? http.Client(),
        cache = cache ?? ResponseCache();

  final String baseUrl;
  final http.Client _http;
  /// Last good GET bodies, so a screen shows something before the network answers.
  final ResponseCache cache;
  final Map<String, Uint8List> _artifacts = {};

  /// Called whenever tokens change, so they can be persisted.
  final void Function(String access, String refresh)? onTokens;

  // Seeded from the process-wide holder so a rebuilt client keeps the session.
  String? _accessToken = SessionTokens.access;
  String? _refreshToken = SessionTokens.refresh;

  bool get isAuthenticated => _accessToken != null;

  void setTokens(String access, String refresh) {
    _accessToken = access;
    _refreshToken = refresh;
    SessionTokens.access = access;
    SessionTokens.refresh = refresh;
    onTokens?.call(access, refresh);
  }

  void clearTokens() {
    _accessToken = null;
    _refreshToken = null;
    SessionTokens.access = null;
    SessionTokens.refresh = null;
    cache.clear();
    _artifacts.clear();
  }

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse('$baseUrl$path').replace(queryParameters: query);

  // A client built during startup (before restore) may have a null instance token;
  // always fall back to the process-wide holder so a request is never anonymous.
  String? get _token => _accessToken ?? SessionTokens.access;

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  Future<dynamic> _send(
    String method,
    String path, {
    Object? body,
    Map<String, String>? query,
    Map<String, String>? extraHeaders,
    bool retryOnUnauthorized = true,
  }) async {
    final request = http.Request(method, _uri(path, query))
      ..headers.addAll({..._headers, ...?extraHeaders});
    if (body != null) request.body = jsonEncode(body);

    final http.StreamedResponse streamed;
    try {
      streamed = await _http.send(request);
    } on Object catch (e) {
      if (Net.isNetworkError(e)) Net.markOffline();
      rethrow;
    }
    final response = await http.Response.fromStream(streamed);
    Net.markOnline();

    if (response.statusCode == 401 &&
        retryOnUnauthorized &&
        (_refreshToken ?? SessionTokens.refresh) != null) {
      if (await refresh()) {
        return _send(method, path,
            body: body,
            query: query,
            extraHeaders: extraHeaders,
            retryOnUnauthorized: false);
      }
    }

    if (response.statusCode >= 400) {
      throw _problem(response);
    }
    if (response.body.isEmpty) return null;
    final decoded = jsonDecode(response.body);
    if (method == 'GET') cache.put(_cacheKey(path, query), decoded);
    return decoded;
  }

  static String _cacheKey(String path, Map<String, String>? query) =>
      query == null || query.isEmpty ? path : '$path?${Uri(queryParameters: query).query}';

  /// Cached body first (if any), then the fresh one: the shape every list provider uses.
  Stream<T> staleWhileRevalidate<T>(
    String path, {
    Map<String, String>? query,
    required T Function(dynamic json) parse,
  }) async* {
    final cached = cache.get(_cacheKey(path, query));
    if (cached != null) {
      try {
        yield parse(cached);
      } catch (_) {
        // an old shape on disk; the fresh answer replaces it
      }
    }
    yield parse(await _send('GET', path, query: query));
  }

  ProblemException _problem(http.Response response) {
    try {
      final decoded = jsonDecode(response.body) as Map<String, dynamic>;
      return ProblemException.fromJson(response.statusCode, decoded);
    } on FormatException {
      return ProblemException(
        status: response.statusCode,
        title: 'Request failed',
        detail: response.body.isEmpty ? null : response.body,
      );
    }
  }

  // ── auth ──────────────────────────────────────────────────────────
  Future<void> login(String email, String password) async {
    final data = await _send('POST', '/v1/auth/login',
        body: {'email': email, 'password': password},
        retryOnUnauthorized: false) as Map<String, dynamic>;
    setTokens(data['access_token'] as String, data['refresh_token'] as String);
  }

  Future<void> register(String email, String password, {String? displayName}) async {
    await _send('POST', '/v1/auth/register', body: {
      'email': email,
      'password': password,
      if (displayName != null) 'display_name': displayName,
    }, retryOnUnauthorized: false);
  }

  Future<Map<String, dynamic>> googleLoginStart() async =>
      await _send('GET', '/v1/auth/google/start', retryOnUnauthorized: false)
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> googleLoginPoll(String pollToken) async =>
      await _send('POST', '/v1/auth/google/poll',
          body: {'poll_token': pollToken},
          retryOnUnauthorized: false) as Map<String, dynamic>;

  Future<bool> refresh() async {
    final token = _refreshToken ?? SessionTokens.refresh;
    if (token == null) return false;
    try {
      final data = await _send('POST', '/v1/auth/refresh',
          body: {'refresh_token': token},
          retryOnUnauthorized: false) as Map<String, dynamic>;
      setTokens(data['access_token'] as String, data['refresh_token'] as String);
      return true;
    } on ProblemException {
      clearTokens();
      return false;
    }
  }

  Future<Map<String, dynamic>> me() async =>
      await _send('GET', '/v1/auth/me') as Map<String, dynamic>;

  // ── goals and tasks ───────────────────────────────────────────────
  Future<List<Goal>> goals() async {
    final data = await _send('GET', '/v1/goals') as List<dynamic>;
    return data.map((g) => Goal.fromJson(g as Map<String, dynamic>)).toList();
  }

  Future<Goal> createGoal(String title, {DateTime? deadline, String? timezone}) async {
    final data = await _send('POST', '/v1/goals', body: {
      'title': title,
      if (deadline != null) 'deadline': deadline.toUtc().toIso8601String(),
      if (timezone != null) 'timezone': timezone,
    }) as Map<String, dynamic>;
    return Goal.fromJson(data);
  }

  Future<Prediction> prediction(String goalId) async {
    final data =
        await _send('GET', '/v1/goals/$goalId/prediction') as Map<String, dynamic>;
    return Prediction.fromJson(data);
  }

  Future<Task> setTaskStatus(String id, String status, {int? version}) =>
      updateTask(id, {'status': status}, version: version);

  Future<List<Task>> tasks({String status = 'open'}) async {
    final data = await _send('GET', '/v1/tasks', query: {'status': status}) as List<dynamic>;
    return data.map((e) => Task.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<Task> quickAdd(String text) async {
    final data = await _send('POST', '/v1/tasks/quick', body: {'text': text})
        as Map<String, dynamic>;
    return Task.fromJson(data);
  }

  Future<Task> createTask(
    String title, {
    String? goalId,
    DateTime? dueAt,
    int? estimateMinutes,
    bool isOptional = false,
    String? recurrence,
  }) async {
    final data = await _send('POST', '/v1/tasks', body: {
      'title': title,
      if (goalId != null) 'goal_id': goalId,
      if (dueAt != null) 'due_at': dueAt.toUtc().toIso8601String(),
      if (estimateMinutes != null) 'estimate_minutes': estimateMinutes,
      if (recurrence != null) 'recurrence': recurrence,
      'is_optional': isOptional,
    }) as Map<String, dynamic>;
    return Task.fromJson(data);
  }

  /// Sends `If-Match` with the version, so two clients editing one task cannot
  /// silently overwrite each other.
  Future<Task> updateTask(String taskId, Map<String, dynamic> changes, {int? version}) async {
    final data = await _send('PATCH', '/v1/tasks/$taskId',
        body: changes,
        extraHeaders: version == null ? null : {'If-Match': '$version'})
        as Map<String, dynamic>;
    return Task.fromJson(data);
  }

  Future<int> acknowledgeTask(String taskId) async {
    final data =
        await _send('POST', '/v1/tasks/$taskId/acknowledge') as Map<String, dynamic>;
    return data['cancelled_alerts'] as int;
  }

  // ── approvals ─────────────────────────────────────────────────────
  Future<List<Approval>> approvals() async {
    final data = await _send('GET', '/v1/approvals') as List<dynamic>;
    return data.map((a) => Approval.fromJson(a as Map<String, dynamic>)).toList();
  }

  Future<Approval> decide(String approvalId, {required bool approved}) async {
    final data = await _send('POST', '/v1/approvals/$approvalId/decision',
        body: {'approved': approved, 'decided_by': 'mobile'}) as Map<String, dynamic>;
    return Approval.fromJson(data);
  }

  Future<Map<String, dynamic>> simulate(String tool, Map<String, dynamic> args) async =>
      await _send('POST', '/v1/actions/simulate',
          body: {'tool': tool, 'args': args}) as Map<String, dynamic>;

  // ── youtube pipeline ──────────────────────────────────────────────
  Future<List<Map<String, dynamic>>> videoRuns() async {
    final data = await _send('GET', '/v1/youtube/videos') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> generateVideo(String topic) async =>
      await _send('POST', '/v1/youtube/videos', body: {'topic': topic})
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> videoAssets() async =>
      await _send('GET', '/v1/youtube/assets') as Map<String, dynamic>;

  Future<Map<String, dynamic>> videoAnalytics() async =>
      await _send('GET', '/v1/youtube/analytics') as Map<String, dynamic>;

  Future<void> refreshAnalytics() async =>
      _send('POST', '/v1/youtube/analytics/refresh');

  Future<void> checkComments() async => _send('POST', '/v1/youtube/comments/check');

  // ── chat ──────────────────────────────────────────────────────────
  Future<Map<String, dynamic>> chat(List<Map<String, String>> messages) async =>
      await _send('POST', '/v1/chat', body: {'messages': messages})
          as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> chatHistory() async {
    final data = await _send('GET', '/v1/chat/history') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  /// Multipart upload of a voice sample, hero video, or BGM track.
  Future<Map<String, dynamic>> uploadAsset(
      String kind, String filename, List<int> bytes) async {
    Future<http.Response> attempt() async {
      final request = http.MultipartRequest('POST', _uri('/v1/youtube/assets'))
        ..headers.addAll(
            {if (_accessToken != null) 'Authorization': 'Bearer $_accessToken'})
        ..fields['kind'] = kind
        ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
      return http.Response.fromStream(await _http.send(request));
    }

    var response = await attempt();
    if (response.statusCode == 401 && await refresh()) {
      response = await attempt();
    }
    if (response.statusCode >= 400) throw _problem(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> publishVideo(String jobId) async =>
      await _send('POST', '/v1/youtube/videos/$jobId/publish')
          as Map<String, dynamic>;

  Future<String> videoPreviewUrl(String actionId) async {
    final data = await _send('GET', '/v1/youtube/actions/$actionId/preview_url')
        as Map<String, dynamic>;
    return data['url'] as String;
  }

  Future<List<Map<String, dynamic>>> connectors() async {
    final data = await _send('GET', '/v1/connectors') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<String> googleAuthUrl({bool youtube = true, bool write = true}) async {
    final data = await _send('GET', '/v1/connectors/google/authorize', query: {
      'include_write': '$write',
      'include_youtube': '$youtube',
    }) as Map<String, dynamic>;
    return data['authorization_url'] as String;
  }

  // ── settings ──────────────────────────────────────────────────────
  Future<Map<String, dynamic>> settings() async =>
      await _send('GET', '/v1/settings') as Map<String, dynamic>;

  Future<Map<String, dynamic>> updateSettings(Map<String, dynamic> changes) async =>
      await _send('PUT', '/v1/settings', body: changes) as Map<String, dynamic>;

  // ── devices and kill switch ───────────────────────────────────────
  Future<List<DeviceInfo>> devices() async {
    final data = await _send('GET', '/v1/devices') as List<dynamic>;
    return data.map((d) => DeviceInfo.fromJson(d as Map<String, dynamic>)).toList();
  }

  /// One typed action, straight from a button. An R1 runs now; an R2 comes back as
  /// `awaiting_approval` with the approval id; a denial says why.
  Future<Map<String, dynamic>> runAction(
    String tool, {
    Map<String, dynamic> args = const {},
    String? deviceId,
  }) async =>
      await _send('POST', '/v1/actions', body: {
        'tool': tool,
        'args': args,
        if (deviceId != null) 'device_id': deviceId,
      }) as Map<String, dynamic>;

  // ── chat: streaming, conversations, memories, runs ────────────────
  /// The reply as it is written. Yields the server's events: delta / final / error / done.
  Stream<Map<String, dynamic>> chatStream(
    List<Map<String, String>> messages, {
    String? conversationId,
    String? provider,
    String? persona,
  }) async* {
    Future<http.StreamedResponse> open() {
      final request = http.Request('POST', _uri('/v1/chat/stream'))
        ..headers.addAll(_headers)
        ..body = jsonEncode({
          'messages': messages,
          if (conversationId != null) 'conversation_id': conversationId,
          if (provider != null && provider != 'auto') 'provider': provider,
          if (persona != null) 'persona': persona,
        });
      return _http.send(request);
    }

    yield* _events(open);
  }

  /// Server-Sent Events, one decoded `data:` frame at a time. Shared by the chat stream
  /// and the HUD's live feed.
  Stream<Map<String, dynamic>> _events(Future<http.StreamedResponse> Function() open) async* {
    var streamed = await open();
    if (streamed.statusCode == 401 && _refreshToken != null && await refresh()) {
      streamed = await open();
    }
    if (streamed.statusCode >= 400) {
      throw _problem(await http.Response.fromStream(streamed));
    }
    var buffer = '';
    await for (final chunk in streamed.stream.transform(utf8.decoder)) {
      buffer += chunk;
      while (true) {
        final end = buffer.indexOf('\n\n');
        if (end == -1) break;
        final frame = buffer.substring(0, end);
        buffer = buffer.substring(end + 2);
        for (final line in frame.split('\n')) {
          if (line.startsWith('data: ')) {
            yield jsonDecode(line.substring(6)) as Map<String, dynamic>;
          }
        }
      }
    }
  }

  // ── the HUD and its live feed ─────────────────────────────────────
  Future<Map<String, dynamic>> hud() async =>
      await _send('GET', '/v1/hud') as Map<String, dynamic>;

  /// What the system does, as it happens. Ends when the server closes it; the caller
  /// reconnects.
  Stream<Map<String, dynamic>> live() =>
      _events(() => _http.send(http.Request('GET', _uri('/v1/live'))..headers.addAll(_headers)));

  // ── personas and the interview ────────────────────────────────────
  Future<List<Map<String, dynamic>>> personas() async {
    final data = await _send('GET', '/v1/personas') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> savePersona(String key, Map<String, dynamic> body) async =>
      await _send('PUT', '/v1/personas/$key', body: body) as Map<String, dynamic>;

  Future<void> deletePersona(String key) async => _send('DELETE', '/v1/personas/$key');

  Future<List<Map<String, dynamic>>> interviewQuestions() async {
    final data = await _send('GET', '/v1/profile/interview') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> submitInterview(Map<String, String> answers) async =>
      await _send('POST', '/v1/profile/interview', body: {'answers': answers})
          as Map<String, dynamic>;

  /// A paired device hands over bytes it produced (a photo, a screenshot).
  Future<Map<String, dynamic>> uploadArtifact(
      String deviceId, String kind, String filename, List<int> bytes) async {
    Future<http.Response> attempt() async {
      final request = http.MultipartRequest('POST', _uri('/v1/devices/$deviceId/artifacts'))
        ..headers.addAll(
            {if (_accessToken != null) 'Authorization': 'Bearer $_accessToken'})
        ..fields['kind'] = kind
        ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
      return http.Response.fromStream(await _http.send(request));
    }

    var response = await attempt();
    if (response.statusCode == 401 && await refresh()) {
      response = await attempt();
    }
    if (response.statusCode >= 400) throw _problem(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  /// Attach an image to chat and get a description (FEATURES-50 #16).
  Future<String> describeImage(List<int> bytes, String filename, {String? question}) async {
    Future<http.Response> attempt() async {
      final request = http.MultipartRequest('POST', _uri('/v1/chat/vision'))
        ..headers.addAll({if (_token != null) 'Authorization': 'Bearer $_token'})
        ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
      if (question != null && question.isNotEmpty) request.fields['question'] = question;
      return http.Response.fromStream(await _http.send(request));
    }

    var response = await attempt();
    if (response.statusCode == 401 && await refresh()) {
      response = await attempt();
    }
    if (response.statusCode >= 400) throw _problem(response);
    return (jsonDecode(response.body) as Map<String, dynamic>)['text'] as String? ?? '';
  }

  // ── standing permissions (PLAN.md 10.9.2) ─────────────────────────
  Future<List<Map<String, dynamic>>> permissions() async {
    final data = await _send('GET', '/v1/permissions') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> grantPermission(
    String tool, {
    Map<String, dynamic> conditions = const {},
    int? maxPerDay,
    int days = 7,
  }) async =>
      await _send('POST', '/v1/permissions', body: {
        'tool': tool,
        'conditions': conditions,
        if (maxPerDay != null) 'max_per_day': maxPerDay,
        'days': days,
      }) as Map<String, dynamic>;

  Future<void> revokePermission(String id) async => _send('DELETE', '/v1/permissions/$id');

  /// "My own Mac and phone may act for me without asking" (PLAN.md 11.8).
  Future<Map<String, dynamic>> trustDevices({int days = 30}) async =>
      await _send('POST', '/v1/permissions/trust-devices', body: {'days': days})
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> trustStatus() async =>
      await _send('GET', '/v1/permissions/trust-devices') as Map<String, dynamic>;

  Future<void> untrustDevices() async => _send('DELETE', '/v1/permissions/trust-devices');

  Future<void> revokeDevice(String id) async => _send('POST', '/v1/devices/$id/revoke');

  Future<Map<String, dynamic>> exportData() async =>
      await _send('GET', '/v1/export') as Map<String, dynamic>;

  /// A portable, curated model of you — style, words, rhythm, decisions (#50).
  Future<Map<String, dynamic>> selfModel() async =>
      await _send('GET', '/v1/self-model') as Map<String, dynamic>;

  /// Ask your digital twin — "what would I say?" — answered in your voice (#19).
  Future<Map<String, dynamic>> askTwin(String question) async =>
      await _send('POST', '/v1/twin', body: {'question': question}) as Map<String, dynamic>;

  /// Everything you've copied, newest first, optionally filtered (#7).
  Future<List<Map<String, dynamic>>> clipboardHistory({String? q}) async =>
      (await _send('GET', '/v1/clipboard/history',
              query: q == null || q.isEmpty ? null : {'q': q}) as List<dynamic>)
          .cast<Map<String, dynamic>>();

  Future<List<Map<String, dynamic>>> auditLog({String? action}) async {
    final data = await _send('GET', '/v1/audit',
        query: action == null ? null : {'action': action}) as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<List<String>> auditActions() async {
    final data = await _send('GET', '/v1/audit/actions') as List<dynamic>;
    return data.cast<String>();
  }

  Future<Map<String, dynamic>> focus() async =>
      await _send('GET', '/v1/focus') as Map<String, dynamic>;

  Future<Map<String, dynamic>> weeklyReview() async =>
      await _send('GET', '/v1/review/weekly') as Map<String, dynamic>;

  // ── mail intelligence: labels, spending, travel, away digest ───────
  Future<Map<String, dynamic>> insightsScan() async =>
      await _send('POST', '/v1/insights/scan') as Map<String, dynamic>;

  Future<Map<String, dynamic>> spending({int days = 30}) async =>
      await _send('GET', '/v1/insights/spending?days=$days') as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> travel() async =>
      (await _send('GET', '/v1/insights/travel') as List<dynamic>).cast<Map<String, dynamic>>();

  Future<Map<String, dynamic>> insightLabels() async =>
      await _send('GET', '/v1/insights/labels') as Map<String, dynamic>;

  Future<Map<String, dynamic>> awayDigest({int hours = 24}) async =>
      await _send('GET', '/v1/insights/away?hours=$hours') as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> groupedActivity() async =>
      (await _send('GET', '/v1/insights/grouped') as List<dynamic>).cast<Map<String, dynamic>>();

  Future<List<Map<String, dynamic>>> anomalies() async =>
      (await _send('GET', '/v1/insights/anomalies') as List<dynamic>).cast<Map<String, dynamic>>();

  /// About-to-forget: deadlines & promises coming due soon (second-brain #24).
  Future<List<Map<String, dynamic>>> upcoming() async =>
      (await _send('GET', '/v1/proactivity/upcoming') as List<dynamic>).cast<Map<String, dynamic>>();

  /// Dropped-thread finder: people who asked something you may owe a reply (#28).
  Future<List<Map<String, dynamic>>> owedReplies() async =>
      (await _send('GET', '/v1/proactivity/owed') as List<dynamic>).cast<Map<String, dynamic>>();

  /// Relationship cadence: who you keep up with, who you've gone quiet on (#16).
  Future<List<Map<String, dynamic>>> relationships() async =>
      (await _send('GET', '/v1/relationships') as List<dynamic>).cast<Map<String, dynamic>>();

  /// "What matters now" digest: the few things that need you, ranked (#25).
  Future<List<Map<String, dynamic>>> digest() async =>
      (await _send('GET', '/v1/proactivity/digest') as List<dynamic>)
          .cast<Map<String, dynamic>>();

  /// Spaced-repetition: important things you'd forget, resurfaced on a curve (#21).
  Future<List<Map<String, dynamic>>> resurfacedMemories() async =>
      (await _send('GET', '/v1/memories/resurface') as List<dynamic>).cast<Map<String, dynamic>>();

  // ── disliked reminders: mute a sender/channel (#14) ───────────────
  /// Dislike a reminder: mute its sender so future ones stop, and clear the ones
  /// already in the list from that sender. Returns {muted, dismissed, signature}.
  Future<Map<String, dynamic>> dislikeReminder(String taskId) async =>
      await _send('POST', '/v1/reminders/dislike/$taskId') as Map<String, dynamic>;

  /// The Muted section: every sender/channel you've disliked.
  Future<List<Map<String, dynamic>>> reminderMutes() async =>
      (await _send('GET', '/v1/reminders/mutes') as List<dynamic>).cast<Map<String, dynamic>>();

  /// Un-mute a sender so its reminders come back.
  Future<void> unmuteReminder(String muteId) async =>
      _send('DELETE', '/v1/reminders/mutes/$muteId');

  // ── commitment tracking (second-brain #22) ────────────────────────
  Future<List<Map<String, dynamic>>> commitments() async =>
      (await _send('GET', '/v1/commitments') as List<dynamic>).cast<Map<String, dynamic>>();

  Future<Map<String, dynamic>> scanCommitments() async =>
      await _send('POST', '/v1/commitments/scan') as Map<String, dynamic>;

  Future<void> commitmentDone(String id) async =>
      _send('POST', '/v1/commitments/$id/done');

  Future<void> commitmentDrop(String id) async =>
      _send('POST', '/v1/commitments/$id/drop');

  /// Quick-capture a thought: filed as a deadline if dated, else a memory (#9).
  Future<Map<String, dynamic>> capture(String text) async =>
      await _send('POST', '/v1/capture', body: {'text': text}) as Map<String, dynamic>;

  /// Rhythm: your energy curve by hour — when you focus and when you slump (#15).
  Future<Map<String, dynamic>> rhythm() async =>
      await _send('GET', '/v1/rhythm') as Map<String, dynamic>;

  /// Focus analytics: deep-work minutes, distraction sources, best window (#36).
  Future<Map<String, dynamic>> focusAnalytics() async =>
      await _send('GET', '/v1/focus-analytics') as Map<String, dynamic>;

  /// Your significant places, learned from coarse location (#5).
  Future<List<Map<String, dynamic>>> places() async =>
      (await _send('GET', '/v1/places') as List<dynamic>).cast<Map<String, dynamic>>();

  /// Report coarse location fixes for place learning (#5) — the server rounds to ~500 m.
  Future<void> postLocation(String deviceId, List<Map<String, dynamic>> fixes) async =>
      _send('POST', '/v1/devices/$deviceId/location', body: fixes);

  /// Time-travel: reconstruct a past day (#30). date = YYYY-MM-DD.
  Future<Map<String, dynamic>> timetravel(String date) async =>
      await _send('GET', '/v1/timetravel', query: {'day': date}) as Map<String, dynamic>;

  // ── decision journal (second-brain #39) ───────────────────────────
  Future<Map<String, dynamic>> logDecision(String text,
          {String? reasoning, String? expected, int reviewInDays = 30}) async =>
      await _send('POST', '/v1/decisions', body: {
        'text': text,
        if (reasoning != null && reasoning.isNotEmpty) 'reasoning': reasoning,
        if (expected != null && expected.isNotEmpty) 'expected': expected,
        'review_in_days': reviewInDays,
      }) as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> decisionsDue() async =>
      (await _send('GET', '/v1/decisions/due') as List<dynamic>).cast<Map<String, dynamic>>();

  Future<void> reviewDecision(String id, String outcome, {String? note}) async =>
      _send('POST', '/v1/decisions/$id/review',
          body: {'outcome': outcome, if (note != null && note.isNotEmpty) 'note': note});

  /// Private mood trend from your own words (#18) — {enough_data, points, latest, mood}.
  Future<Map<String, dynamic>> mood() async =>
      await _send('GET', '/v1/mood') as Map<String, dynamic>;

  /// Speech-pattern profile: fillers, favourite phrases, sentence length (#12).
  Future<Map<String, dynamic>> speechProfile() async =>
      await _send('GET', '/v1/profile/speech') as Map<String, dynamic>;

  /// A 3-minute micro-lesson for a topic you keep asking about (#35).
  Future<Map<String, dynamic>> microLesson(String topic) async =>
      await _send('GET', '/v1/micro-lesson', query: {'topic': topic}) as Map<String, dynamic>;

  /// Knowledge gaps: topics you keep asking about (#29).
  Future<List<Map<String, dynamic>>> knowledgeGaps() async =>
      ((await _send('GET', '/v1/profile/knowledge-gaps') as Map<String, dynamic>)['gaps']
              as List<dynamic>? ??
          const [])
          .cast<Map<String, dynamic>>();

  /// Interest drift: topics rising/fading in your own words (second-brain #17).
  Future<Map<String, dynamic>> interests() async =>
      await _send('GET', '/v1/interests') as Map<String, dynamic>;

  /// Your recurring names/jargon/acronyms, learned from your own words (second-brain #13).
  Future<List<Map<String, dynamic>>> phrasebook() async =>
      ((await _send('GET', '/v1/profile/phrasebook') as Map<String, dynamic>)['terms']
              as List<dynamic>? ??
          const [])
          .cast<Map<String, dynamic>>();

  /// Ask your life a question, get a sourced answer (second-brain #23).
  Future<Map<String, dynamic>> askLife(String question) async =>
      await _send('POST', '/v1/recall', body: {'question': question}) as Map<String, dynamic>;

  /// Life search: one box over everything captured (second-brain #26).
  Future<List<Map<String, dynamic>>> lifeSearch(String q) async {
    final data = await _send('GET', '/v1/search', query: {'q': q}) as Map<String, dynamic>;
    return (data['results'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
  }

  // ── clipboard synced across devices (FEATURES-50 #26) ─────────────
  Future<Map<String, dynamic>> getClipboard() async =>
      await _send('GET', '/v1/clipboard') as Map<String, dynamic>;

  Future<Map<String, dynamic>> setClipboard(String text, {String? device}) async =>
      await _send('POST', '/v1/clipboard',
          body: {'text': text, if (device != null) 'device': device}) as Map<String, dynamic>;

  /// Edit what a device is allowed to do (FEATURES-50 #30).
  Future<DeviceInfo> editAllowlist(String deviceId,
      {List<String>? capabilities, List<String>? bundleIds}) async {
    final data = await _send('PATCH', '/v1/devices/$deviceId/allowlist', body: {
      if (capabilities != null) 'capabilities': capabilities,
      if (bundleIds != null) 'allowed_bundle_ids': bundleIds,
    }) as Map<String, dynamic>;
    return DeviceInfo.fromJson(data);
  }

  // ── proactivity: streaks and meeting prep ─────────────────────────
  Future<Map<String, dynamic>> streaks() async =>
      await _send('GET', '/v1/proactivity/streaks') as Map<String, dynamic>;

  /// Habit coach: streaks + a kind next-step / celebration line (#31).
  Future<Map<String, dynamic>> coach() async =>
      await _send('GET', '/v1/proactivity/coach') as Map<String, dynamic>;

  /// Rediscover: an old note relevant to what you're doing now (#27).
  Future<Map<String, dynamic>> rediscover() async =>
      await _send('GET', '/v1/rediscover') as Map<String, dynamic>;

  Future<Map<String, dynamic>?> meetingPrep() async =>
      await _send('GET', '/v1/proactivity/meeting') as Map<String, dynamic>?;

  Future<Map<String, dynamic>> wipeAccount() async =>
      await _send('POST', '/v1/account/wipe', query: {'confirm': 'DELETE'})
          as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> identities() async {
    final data = await _send('GET', '/v1/identities') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> linkIdentity(String provider, String subject) async =>
      await _send('POST', '/v1/identities', body: {'provider': provider, 'subject': subject})
          as Map<String, dynamic>;

  /// The activity sampler hands over app names with timestamps (PLAN.md 10.6.3).
  Future<Map<String, dynamic>> postActivity(
          String deviceId, List<Map<String, dynamic>> items) async =>
      await _send('POST', '/v1/devices/$deviceId/activity', body: items)
          as Map<String, dynamic>;

  /// What the nightly loop proposes for the profile (PLAN.md 10.6.5).
  Future<List<Map<String, dynamic>>> suggestions() async {
    final data = await _send('GET', '/v1/profile/suggestions') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> resolveSuggestion(String id, {required bool accept}) async =>
      await _send('POST', '/v1/profile/suggestions/$id/${accept ? 'accept' : 'dismiss'}')
          as Map<String, dynamic>;

  /// The notification mirror hands over allowlisted titles (PLAN.md 10.4.4).
  Future<Map<String, dynamic>> postNotifications(
          String deviceId, List<Map<String, dynamic>> items) async =>
      await _send('POST', '/v1/devices/$deviceId/notifications', body: items)
          as Map<String, dynamic>;

  // ── routines ──────────────────────────────────────────────────────
  Future<List<Map<String, dynamic>>> routines() async {
    final data = await _send('GET', '/v1/routines') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> createRoutine(Map<String, dynamic> body) async =>
      await _send('POST', '/v1/routines', body: body) as Map<String, dynamic>;

  Future<Map<String, dynamic>> updateRoutine(String id, Map<String, dynamic> changes) async =>
      await _send('PATCH', '/v1/routines/$id', body: changes) as Map<String, dynamic>;

  Future<void> deleteRoutine(String id) async => _send('DELETE', '/v1/routines/$id');

  Future<Map<String, dynamic>> runRoutine(String id) async =>
      await _send('POST', '/v1/routines/$id/run') as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> conversations() async {
    final data = await _send('GET', '/v1/conversations') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> createConversation() async =>
      await _send('POST', '/v1/conversations') as Map<String, dynamic>;

  Future<Map<String, dynamic>> pinConversation(String id, bool pinned) async =>
      await _send('PATCH', '/v1/conversations/$id', body: {'pinned': pinned})
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> renameConversation(String id, String title) async =>
      await _send('PATCH', '/v1/conversations/$id', body: {'title': title})
          as Map<String, dynamic>;

  Future<void> deleteConversation(String id) async =>
      _send('DELETE', '/v1/conversations/$id');

  Future<Map<String, dynamic>> exportConversation(String id, {String fmt = 'md'}) async =>
      await _send('POST', '/v1/conversations/$id/export?fmt=$fmt') as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> conversationHistory(String? conversationId) async {
    final data = await _send('GET', '/v1/chat/history',
        query: conversationId == null ? null : {'conversation_id': conversationId}) as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<List<Map<String, dynamic>>> memories() async {
    final data = await _send('GET', '/v1/memories') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<void> forgetMemory(String id) async => _send('DELETE', '/v1/memories/$id');

  Future<Map<String, dynamic>> run(String runId) async =>
      await _send('GET', '/v1/runs/$runId') as Map<String, dynamic>;

  Future<Map<String, dynamic>> health() async =>
      await _send('GET', '/healthz', retryOnUnauthorized: false) as Map<String, dynamic>;

  // ── second brain: status, profile, feedback, sync ─────────────────
  Future<Map<String, dynamic>> systemStatus({bool fresh = false}) async =>
      await _send('GET', '/v1/system/status', query: fresh ? {'fresh': 'true'} : null)
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> profile() async =>
      await _send('GET', '/v1/profile') as Map<String, dynamic>;

  Future<Map<String, dynamic>> updateProfile(Map<String, dynamic> fields) async =>
      await _send('PUT', '/v1/profile', body: fields) as Map<String, dynamic>;

  Future<Map<String, dynamic>> learnStyle() async =>
      await _send('POST', '/v1/profile/learn-style') as Map<String, dynamic>;

  Future<Map<String, dynamic>> feedbackStats() async =>
      await _send('GET', '/v1/profile/feedback') as Map<String, dynamic>;

  Future<void> sendFeedback({
    String? messageId,
    required int score,
    String? note,
    String? excerpt,
  }) async =>
      _send('POST', '/v1/chat/feedback', body: {
        if (messageId != null) 'message_id': messageId,
        'score': score,
        if (note != null && note.isNotEmpty) 'note': note,
        if (excerpt != null) 'excerpt': excerpt,
      });

  Future<Map<String, dynamic>> syncAllConnectors() async =>
      await _send('POST', '/v1/connectors/sync') as Map<String, dynamic>;

  Future<Map<String, dynamic>> scanSlack() async =>
      await _send('POST', '/v1/connectors/slack/scan') as Map<String, dynamic>;

  Future<Map<String, dynamic>> syncConnector(String id) async =>
      await _send('POST', '/v1/connectors/$id/sync') as Map<String, dynamic>;

  // ── notification endpoints ────────────────────────────────────────
  Future<Map<String, dynamic>> registerEndpoint(
          {required String channel, required String address, bool enabled = true}) async =>
      await _send('PUT', '/v1/notifications/endpoints',
          body: {'channel': channel, 'address': address, 'enabled': enabled}) as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> endpoints() async {
    final data = await _send('GET', '/v1/notifications/endpoints') as List<dynamic>;
    return data.cast<Map<String, dynamic>>();
  }

  Future<void> deleteEndpoint(String id) async =>
      _send('DELETE', '/v1/notifications/endpoints/$id');

  // ── voice ─────────────────────────────────────────────────────────
  /// The server's neural voice for a reply. Null when offline — the caller falls
  /// back to the device voice rather than to silence.
  Future<Uint8List?> ttsBytes(String text) async {
    try {
      final response = await _http.post(
        _uri('/v1/tts'),
        headers: _headers,
        body: jsonEncode({'text': text.length > 2000 ? text.substring(0, 2000) : text}),
      );
      if (response.statusCode != 200) return null;
      return response.bodyBytes;
    } catch (_) {
      return null;
    }
  }

  // ── timeline and artifacts ────────────────────────────────────────
  Future<List<TimelineEntry>> timeline({int limit = 100}) async {
    final data =
        await _send('GET', '/v1/timeline', query: {'limit': '$limit'}) as List<dynamic>;
    return data
        .map((e) => TimelineEntry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<Map<String, dynamic>>> listArtifacts({int limit = 20}) async =>
      (await _send('GET', '/v1/artifacts', query: {'limit': '$limit'}) as List<dynamic>)
          .cast<Map<String, dynamic>>();

  /// Artifact bytes, fetched with the bearer token — an `Image.network` cannot carry it.
  Future<Uint8List> artifactBytes(String url) async {
    final hit = _artifacts[url];
    if (hit != null) return hit;
    final bytes = await _fetchArtifact(url);
    if (_artifacts.length > 30) _artifacts.remove(_artifacts.keys.first);
    _artifacts[url] = bytes;
    return bytes;
  }

  Future<Uint8List> _fetchArtifact(String url) async {
    final response = await _http.get(_uri(url), headers: _headers);
    if (response.statusCode >= 400) throw _problem(response);
    return response.bodyBytes;
  }

  // ── this phone as a device ────────────────────────────────────────
  Future<Map<String, dynamic>> beginPairing(Map<String, dynamic> body) async =>
      await _send('POST', '/v1/devices/pair', body: body) as Map<String, dynamic>;

  Future<Map<String, dynamic>> completePairing(Map<String, dynamic> body) async =>
      await _send('POST', '/v1/devices/pair/complete', body: body) as Map<String, dynamic>;

  Future<String> serverPublicKey() async {
    final data = await _send('GET', '/v1/devices/server-key') as Map<String, dynamic>;
    return data['public_key_pem'] as String;
  }

  String? get accessToken => _accessToken;

  String get wsBase => baseUrl.replaceFirst('https://', 'wss://').replaceFirst('http://', 'ws://');

  Future<Map<String, dynamic>> pause({String reason = 'mobile emergency'}) async =>
      await _send('POST', '/v1/agent/pause', query: {'reason': reason})
          as Map<String, dynamic>;

  Future<void> resume() async => _send('POST', '/v1/agent/resume');

  void close() => _http.close();
}
