// ignore_for_file: avoid_print
// Prints a public key, a message, and a signature so Python can verify the format.
import 'dart:convert';

import 'package:jarvis_x/node/keys.dart';

void main(List<String> args) {
  final key = DeviceKey.generate();
  final restored = DeviceKey.fromScalarHex(key.scalarHex);
  const message = 'challenge-123';
  final signature = restored.sign(utf8.encode(message));
  final selfVerify = DeviceKey.verify(key.publicPem, utf8.encode(message), signature);
  print(jsonEncode({
    'public_pem': key.publicPem,
    'message': message,
    'signature': signature,
    'self_verify': selfVerify,
  }));
}
