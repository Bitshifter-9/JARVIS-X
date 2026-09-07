// ignore_for_file: avoid_print
// End-to-end protocol interop, driven from Python:
//   stdin  ← {"server_public_pem": ..., "job": {...signed envelope...}}
//   stdout → {"phone_public_pem": ..., "result": {...signed job.result...}}
// Python then verifies the phone's signature over the result with its own verifier.
import 'dart:convert';
import 'dart:io';

import 'package:jarvis_x/node/phone_node.dart';

Future<void> main() async {
  final input = jsonDecode(await stdin.transform(utf8.decoder).join()) as Map<String, dynamic>;
  final opened = <String>[];
  final node = PhoneNode(
    launchUrl: (uri) async {
      opened.add(uri.toString());
      return true;
    },
    launchApp: (_) async => true,
    notify: (_, __) async {},
  );
  node.restore(serverPublicPem: input['server_public_pem'] as String);
  node.ensureKey();
  node.deviceId = 'phone-1';

  final result = await node.execute((input['job'] as Map).cast<String, dynamic>());
  stdout.write(jsonEncode({
    'phone_public_pem': node.publicPem,
    'result': result,
    'opened': opened,
  }));
}
