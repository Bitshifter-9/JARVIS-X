import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

/// Typed HTTP client for the JARVIS X API.
///
/// Refresh is handled here rather than at call sites: an access token lives 15 minutes,
/// and a screen that has to think about that will eventually forget.
class JarvisClient {
  JarvisClient({required this.baseUrl, http.Client? httpClient, this.onTokens})
      : _http = httpClient ?? http.Client();

  final String baseUrl;
  final http.Client _http;

  /// Called whenever tokens change, so they can be persisted.
  final void Function(String access, String refresh)? onTokens;

  String? _accessToken;
  String? _refreshToken;

  bool get isAuthenticated => _accessToken != null;

  void setTokens(String access, String refresh) {
    _accessToken = access;
    _refreshToken = refresh;
    onTokens?.call(access, refresh);
  }

  void clearTokens() {
    _accessToken = null;
    _refreshToken = null;
  }

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse('$baseUrl$path').replace(queryParameters: query);

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_accessToken != null) 'Authorization': 'Bearer $_accessToken',
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

    final streamed = await _http.send(request);
    final response = await http.Response.fromStream(streamed);

    if (response.statusCode == 401 && retryOnUnauthorized && _refreshToken != null) {
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
    return jsonDecode(response.body);
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

  Future<bool> refresh() async {
    if (_refreshToken == null) return false;
    try {
      final data = await _send('POST', '/v1/auth/refresh',
          body: {'refresh_token': _refreshToken},
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

  Future<Task> createTask(
    String title, {
    String? goalId,
    DateTime? dueAt,
    int? estimateMinutes,
    bool isOptional = false,
  }) async {
    final data = await _send('POST', '/v1/tasks', body: {
      'title': title,
      if (goalId != null) 'goal_id': goalId,
      if (dueAt != null) 'due_at': dueAt.toUtc().toIso8601String(),
      if (estimateMinutes != null) 'estimate_minutes': estimateMinutes,
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

  // ── devices and kill switch ───────────────────────────────────────
  Future<List<DeviceInfo>> devices() async {
    final data = await _send('GET', '/v1/devices') as List<dynamic>;
    return data.map((d) => DeviceInfo.fromJson(d as Map<String, dynamic>)).toList();
  }

  Future<Map<String, dynamic>> pause({String reason = 'mobile emergency'}) async =>
      await _send('POST', '/v1/agent/pause', query: {'reason': reason})
          as Map<String, dynamic>;

  Future<void> resume() async => _send('POST', '/v1/agent/resume');

  void close() => _http.close();
}
