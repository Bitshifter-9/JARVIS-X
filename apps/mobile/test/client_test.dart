import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:jarvis_x/api/client.dart';
import 'package:jarvis_x/api/models.dart';

http.Response _json(Object body, {int status = 200}) =>
    http.Response(jsonEncode(body), status,
        headers: {'content-type': 'application/json'});

void main() {
  group('models', () {
    test('a prediction parses its options and severity', () {
      final prediction = Prediction.fromJson({
        'goal_id': 'g1',
        'severity': 'critical',
        'probability': 0.03,
        'available_minutes': 170.0,
        'p80_remaining_minutes': 504.0,
        'explanation': 'You have 170 usable minutes…',
        'options': [
          {
            'key': 'reduce_scope',
            'title': 'Drop 2 optional task(s)',
            'detail': 'Remove work marked optional',
            'probability_after': 0.22,
            'minutes_saved': 210.0,
            'tasks_affected': ['Alexa animation'],
            'requires_approval': false,
          }
        ],
      });

      expect(prediction.needsAttention, isTrue);
      expect(prediction.options.single.tasksAffected, ['Alexa animation']);
      expect(prediction.options.single.requiresApproval, isFalse);
    });

    test('an on-track prediction does not demand attention', () {
      final prediction = Prediction.fromJson({
        'goal_id': 'g1',
        'severity': 'on_track',
        'probability': 0.95,
        'available_minutes': 500.0,
        'p80_remaining_minutes': 100.0,
        'explanation': 'fine',
        'options': <dynamic>[],
      });
      expect(prediction.needsAttention, isFalse);
    });

    test('a task keeps the evidence span it was extracted from', () {
      final task = Task.fromJson({
        'id': 't1',
        'goal_id': null,
        'title': 'Submit',
        'status': 'open',
        'due_at': '2026-09-05T18:29:00+00:00',
        'estimate_minutes': 90,
        'remaining_minutes': 60,
        'priority': 2,
        'is_optional': false,
        'evidence_span': 'due on 5 September 2026 at 11:59 PM',
        'version': 3,
      });
      expect(task.evidenceSpan, contains('5 September 2026'));
      expect(task.dueAt!.isUtc, isFalse, reason: 'times are shown in local zone');
    });

    test('a pending approval is one with no decision', () {
      final approval = Approval.fromJson({
        'id': 'a1',
        'action_id': 'act1',
        'decision': null,
        'expires_at': '2026-09-05T18:29:00+00:00',
        'requires_local_confirmation': true,
        'locally_confirmed': false,
      });
      expect(approval.isPending, isTrue);
      expect(approval.requiresLocalConfirmation, isTrue);
    });
  });

  group('client', () {
    test('login stores tokens and authorizes later calls', () async {
      final seen = <String, String?>{};
      final client = JarvisClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          if (request.url.path == '/v1/auth/login') {
            return _json({
              'access_token': 'acc',
              'refresh_token': 'ref',
              'token_type': 'Bearer',
              'expires_in': 900,
            });
          }
          seen['auth'] = request.headers['Authorization'];
          return _json(<dynamic>[]);
        }),
      );

      await client.login('a@b.test', 'password');
      await client.goals();
      expect(seen['auth'], 'Bearer acc');
    });

    test('a 401 triggers one refresh and one retry', () async {
      var goalCalls = 0;
      var refreshCalls = 0;
      final client = JarvisClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          if (request.url.path == '/v1/auth/refresh') {
            refreshCalls++;
            return _json({
              'access_token': 'fresh',
              'refresh_token': 'ref2',
              'token_type': 'Bearer',
              'expires_in': 900,
            });
          }
          goalCalls++;
          if (goalCalls == 1) {
            return _json({'status': 401, 'title': 'Unauthorized'}, status: 401);
          }
          return _json(<dynamic>[]);
        }),
      );

      client.setTokens('stale', 'ref');
      await client.goals();

      expect(refreshCalls, 1);
      expect(goalCalls, 2, reason: 'exactly one retry, never a loop');
    });

    test('a failed refresh clears the session rather than retrying forever', () async {
      var refreshCalls = 0;
      final client = JarvisClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          if (request.url.path == '/v1/auth/refresh') {
            refreshCalls++;
            return _json({'status': 401, 'title': 'Unauthorized'}, status: 401);
          }
          return _json({'status': 401, 'title': 'Unauthorized'}, status: 401);
        }),
      );

      client.setTokens('stale', 'ref');
      await expectLater(client.goals(), throwsA(isA<ProblemException>()));
      expect(refreshCalls, 1);
      expect(client.isAuthenticated, isFalse);
    });

    test('a problem document surfaces its correlation id', () async {
      final client = JarvisClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((_) async => _json({
              'type': 'https://jarvis-x.dev/problems/conflict',
              'title': 'Conflict',
              'status': 409,
              'detail': 'Task was modified by someone else',
              'correlation_id': 'cor_01ABC',
            }, status: 409)),
      );
      client.setTokens('a', 'r');

      try {
        await client.goals();
        fail('expected a ProblemException');
      } on ProblemException catch (e) {
        expect(e.status, 409);
        expect(e.correlationId, 'cor_01ABC');
        expect(e.toString(), contains('modified by someone else'));
      }
    });

    test('a non-JSON error body does not crash the client', () async {
      final client = JarvisClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((_) async => http.Response('<html>502</html>', 502)),
      );
      client.setTokens('a', 'r');
      await expectLater(client.goals(), throwsA(isA<ProblemException>()));
    });

    test('updating a task sends its version as If-Match', () async {
      String? ifMatch;
      final client = JarvisClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          ifMatch = request.headers['If-Match'];
          return _json({
            'id': 't1',
            'goal_id': null,
            'title': 'Renamed',
            'status': 'open',
            'due_at': null,
            'estimate_minutes': null,
            'remaining_minutes': null,
            'priority': 2,
            'is_optional': false,
            'evidence_span': null,
            'version': 4,
          });
        }),
      );
      client.setTokens('a', 'r');

      await client.updateTask('t1', {'title': 'Renamed'}, version: 3);
      expect(ifMatch, '3');
    });

    test('deciding an approval reports the deciding surface', () async {
      Map<String, dynamic>? body;
      final client = JarvisClient(
        baseUrl: 'http://api.test',
        httpClient: MockClient((request) async {
          body = jsonDecode(request.body) as Map<String, dynamic>;
          return _json({
            'id': 'a1',
            'action_id': 'act1',
            'decision': 'approved',
            'expires_at': '2026-09-05T18:29:00+00:00',
            'requires_local_confirmation': false,
            'locally_confirmed': false,
          });
        }),
      );
      client.setTokens('a', 'r');

      final approval = await client.decide('a1', approved: true);
      expect(body!['decided_by'], 'mobile');
      expect(approval.decision, 'approved');
    });
  });
}
