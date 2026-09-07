import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_x/node/canonical.dart';
import 'package:jarvis_x/node/keys.dart';
import 'package:jarvis_x/node/mac_hands.dart';
import 'package:jarvis_x/node/phone_node.dart';

/// A stand-in for the server: the same curve, so the phone's verifier is exercised
/// with a real signature rather than a mocked "true".
Map<String, dynamic> signedJob(
  DeviceKey server, {
  String action = 'phone.open_url',
  Map<String, dynamic> args = const {'url': 'https://example.test/a'},
  String nonce = 'n1',
  DateTime? expires,
}) {
  final job = {
    'job_id': 'job_1',
    'action': action,
    'args': args,
    'risk': 'R1',
    'nonce': nonce,
    'issued_at': DateTime.now().toUtc().toIso8601String(),
    'expires_at': (expires ?? DateTime.now().toUtc().add(const Duration(minutes: 5)))
        .toIso8601String(),
    'policy_version': 1,
    'device_id': 'phone-1',
  };
  return {...job, 'type': 'job.dispatch', 'signature': server.sign(canonicalBytes(job))};
}

PhoneNode node(
  DeviceKey server, {
  List<String>? opened,
  List<String>? launched,
  List<String>? intents,
}) {
  final n = PhoneNode(
    launchUrl: (uri) async {
      opened?.add(uri.toString());
      return true;
    },
    launchApp: (pkg) async {
      launched?.add(pkg);
      return true;
    },
    notify: (_, __) async {},
    launchIntent: (action) async {
      intents?.add(action);
      return true;
    },
  );
  n.restore(serverPublicPem: server.publicPem);
  n.deviceId = 'phone-1';
  return n;
}

void main() {
  group('canonical json', () {
    test('sorts keys recursively and drops whitespace', () {
      expect(
        canonicalJson({
          'b': 1,
          'a': {'z': [1, 2.5, 'x'], 'y': null, 'm': true},
        }),
        '{"a":{"m":true,"y":null,"z":[1,2.5,"x"]},"b":1}',
      );
    });

    test('escapes only what python escapes', () {
      expect(canonicalJson({'t': 'q" b\\ n\n ü'}), r'{"t":"q\" b\\ n\n ü"}');
    });
  });

  group('device key', () {
    test('a signature verifies with the exported public key and not with another', () {
      final key = DeviceKey.generate();
      final other = DeviceKey.generate();
      final sig = key.sign(utf8.encode('challenge'));
      expect(DeviceKey.verify(key.publicPem, utf8.encode('challenge'), sig), isTrue);
      expect(DeviceKey.verify(key.publicPem, utf8.encode('challengE'), sig), isFalse);
      expect(DeviceKey.verify(other.publicPem, utf8.encode('challenge'), sig), isFalse);
    });

    test('the scalar round-trips through storage', () {
      final key = DeviceKey.generate();
      expect(DeviceKey.fromScalarHex(key.scalarHex).publicPem, key.publicPem);
    });
  });

  group('phone node', () {
    test('a signed job runs, and the result is signed by the phone', () async {
      final server = DeviceKey.generate();
      final opened = <String>[];
      final n = node(server, opened: opened);

      final result = await n.execute(signedJob(server));

      expect(result['status'], 'completed');
      expect(result['observed'], {'opened': true, 'opened_url': 'https://example.test/a'});
      expect(opened, ['https://example.test/a']);
      final unsigned = Map<String, dynamic>.from(result)
        ..remove('type')
        ..remove('signature');
      expect(
        DeviceKey.verify(n.publicPem, canonicalBytes(unsigned), result['signature'] as String),
        isTrue,
      );
    });

    test('a job signed by someone other than the server is refused before it runs',
        () async {
      final server = DeviceKey.generate();
      final impostor = DeviceKey.generate();
      final opened = <String>[];
      final result = await node(server, opened: opened).execute(signedJob(impostor));
      expect(result['reject_reason'], 'bad_signature');
      expect(opened, isEmpty);
    });

    test('a tampered argument breaks the signature', () async {
      final server = DeviceKey.generate();
      final job = signedJob(server);
      job['args'] = {'url': 'https://evil.test'};
      final result = await node(server).execute(job);
      expect(result['reject_reason'], 'bad_signature');
    });

    test('an expired job is refused', () async {
      final server = DeviceKey.generate();
      final result = await node(server).execute(signedJob(
        server,
        expires: DateTime.now().toUtc().subtract(const Duration(seconds: 1)),
      ));
      expect(result['reject_reason'], 'expired');
    });

    test('a replayed job is refused the second time', () async {
      final server = DeviceKey.generate();
      final n = node(server);
      final job = signedJob(server, nonce: 'same');
      expect((await n.execute(job))['status'], 'completed');
      expect((await n.execute(job))['reject_reason'], 'replayed_nonce');
    });

    test('a scheme the phone did not enable is refused', () async {
      final server = DeviceKey.generate();
      final opened = <String>[];
      final result = await node(server, opened: opened)
          .execute(signedJob(server, args: {'url': 'file:///etc/passwd'}));
      expect(result['reject_reason'], 'not_allowlisted');
      expect(opened, isEmpty);
    });

    test('open_app only launches allowlisted packages', () async {
      final server = DeviceKey.generate();
      final launched = <String>[];
      final n = node(server, launched: launched);
      final ok = await n.execute(signedJob(server,
          action: 'phone.open_app', args: {'bundle_id': 'com.whatsapp'}, nonce: 'a'));
      final bad = await n.execute(signedJob(server,
          action: 'phone.open_app', args: {'bundle_id': 'com.evil.app'}, nonce: 'b'));
      expect(ok['observed'], {'is_running': true, 'package': 'com.whatsapp'});
      expect(bad['reject_reason'], 'not_allowlisted');
      expect(launched, ['com.whatsapp']);
    });

    test('a whatsapp draft opens the chat with the text filled in', () async {
      final server = DeviceKey.generate();
      final opened = <String>[];
      await node(server, opened: opened).execute(signedJob(server,
          action: 'phone.whatsapp_draft',
          args: {'phone': '+91 98765 43210', 'text': 'running late'}));
      expect(opened.single, 'whatsapp://send?phone=919876543210&text=running+late');
    });

    test('the stop button outranks a valid signature', () async {
      final server = DeviceKey.generate();
      final n = node(server)..stop();
      final result = await n.execute(signedJob(server));
      expect(result['reject_reason'], 'stopped');
    });

    test('a settings panel opens by intent and an unknown panel is refused', () async {
      final server = DeviceKey.generate();
      final intents = <String>[];
      final n = node(server, intents: intents);
      final ok = await n.execute(
          signedJob(server, action: 'phone.open_settings', args: {'panel': 'wifi'}));
      expect(ok['status'], 'completed');
      expect(intents, ['android.settings.WIFI_SETTINGS']);
      expect(ok['observed']['opened_url'], 'settings:wifi');
      final bad = await n.execute(signedJob(server,
          action: 'phone.open_settings', args: {'panel': 'developer'}, nonce: 'n2'));
      expect(bad['status'], 'rejected');
      expect(intents.length, 1);
    });

    test('a call opens the dialer with the number and an sms carries its text', () async {
      final server = DeviceKey.generate();
      final opened = <String>[];
      final n = node(server, opened: opened);
      await n.execute(signedJob(server,
          action: 'phone.call', args: {'number': '+91 99999 99999'}, nonce: 'c1'));
      await n.execute(signedJob(server,
          action: 'phone.sms_draft',
          args: {'number': '9999999999', 'text': 'Running late, 10 min'},
          nonce: 'c2'));
      expect(opened[0], 'tel:+919999999999');
      expect(opened[1], startsWith('sms:9999999999?body=Running'));
    });

    test('the camera takes one photo and reports the server-minted artifact', () async {
      final server = DeviceKey.generate();
      final uploads = <String>[];
      final n = PhoneNode(
        launchUrl: (_) async => true,
        launchApp: (_) async => true,
        notify: (_, __) async {},
        captureImage: () async => [1, 2, 3],
        uploadArtifact: (kind, filename, bytes) async {
          uploads.add('$kind/$filename/${bytes.length}');
          return 'art-1';
        },
      );
      n.restore(serverPublicPem: server.publicPem);
      n.deviceId = 'phone-1';
      final ok = await n.execute(signedJob(server, action: 'phone.camera', args: {}, nonce: 'p1'));
      expect(ok['status'], 'completed');
      expect(ok['observed']['artifact_id'], 'art-1');
      expect(ok['observed']['digest'], startsWith('sha256:'));
      expect(uploads, ['capture/photo.jpg/3']);
    });

    test('on a Mac the app answers the Mac verbs through argv, never a shell', () async {
      final server = DeviceKey.generate();
      final calls = <String>[];
      Future<ProcessResult> fakeRun(String exe, List<String> args, {String? stdin}) async {
        calls.add('$exe ${args.join(' ')}${stdin != null ? ' <<$stdin' : ''}');
        if (exe.endsWith('pbpaste')) return ProcessResult(1, 0, 'copied text', '');
        if (args.contains('-getairportpower')) return ProcessResult(1, 0, 'Wi-Fi Power (en0): Off', '');
        return ProcessResult(1, 0, '', '');
      }

      final n = PhoneNode(
        launchUrl: (_) async => true,
        launchApp: (_) async => true,
        notify: (_, __) async {},
        macHands: MacHands(run: fakeRun, uploadArtifact: (_, __, ___) async => 'art-9'),
      );
      n.restore(serverPublicPem: server.publicPem);
      n.deviceId = 'mac-1';
      expect(n.allowedActions, contains('mac.clipboard_write'));

      final wrote = await n.execute(signedJob(server,
          action: 'mac.clipboard_write', args: {'text': 'hello'}, nonce: 'm1'));
      expect(wrote['status'], 'completed');
      expect(calls.last, '/usr/bin/pbcopy  <<hello');

      final read = await n.execute(signedJob(server, action: 'mac.clipboard_read', args: {}, nonce: 'm2'));
      expect(read['observed']['text'], 'copied text');

      final wifi = await n.execute(signedJob(server,
          action: 'mac.set_setting', args: {'key': 'wifi', 'value': 'off'}, nonce: 'm3'));
      expect(wifi['observed'], {'status': 200, 'key': 'wifi', 'value': 'off', 'state': 'off'});
      expect(calls.any((c) => c.startsWith('/usr/sbin/networksetup -setairportpower en0 off')), isTrue);

      final bad = await n.execute(signedJob(server,
          action: 'mac.set_setting', args: {'key': 'firewall', 'value': 'off'}, nonce: 'm4'));
      expect(bad['status'], 'rejected');

      final url = await n.execute(signedJob(server,
          action: 'mac.open_url', args: {'url': 'file:///etc/passwd'}, nonce: 'm5'));
      expect(url['status'], 'rejected');

      final said = await n.execute(signedJob(server,
          action: 'mac.say', args: {'text': 'Back to it?'}, nonce: 'm6'));
      expect(said['observed']['status'], 200);
      expect(calls.last, '/usr/bin/say Back to it?');
    });

    test('a phone without Mac hands refuses Mac verbs and answers its clipboard', () async {
      final server = DeviceKey.generate();
      String? board;
      final n = PhoneNode(
        launchUrl: (_) async => true,
        launchApp: (_) async => true,
        notify: (_, __) async {},
        readClipboard: () async => board,
        writeClipboard: (t) async => board = t,
      );
      n.restore(serverPublicPem: server.publicPem);
      n.deviceId = 'phone-1';
      final mac = await n.execute(signedJob(server, action: 'mac.say', args: {'text': 'x'}, nonce: 'c1'));
      expect(mac['reject_reason'], 'unknown_action');
      await n.execute(signedJob(server,
          action: 'phone.clipboard_write', args: {'text': 'from the mac'}, nonce: 'c2'));
      expect(board, 'from the mac');
      final read = await n.execute(signedJob(server, action: 'phone.clipboard_read', args: {}, nonce: 'c3'));
      expect(read['observed']['text'], 'from the mac');
    });

    test('a deep link runs only on an enabled scheme', () async {
      final server = DeviceKey.generate();
      final opened = <String>[];
      final n = node(server, opened: opened);
      final ok = await n.execute(signedJob(server,
          action: 'phone.open_deeplink', args: {'url': 'spotify:track:abc'}, nonce: 'd1'));
      expect(ok['status'], 'completed');
      final bad = await n.execute(signedJob(server,
          action: 'phone.open_deeplink', args: {'url': 'file:///sdcard/x'}, nonce: 'd2'));
      expect(bad['status'], 'rejected');
      expect(opened, ['spotify:track:abc']);
    });
  });
}
