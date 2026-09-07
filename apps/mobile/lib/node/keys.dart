import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';

/// The phone's device key: ECDSA P-256, the same curve the Mac helper uses, so the
/// server verifies both with one function (`jarvis.services.device.keys.verify`).
///
/// The private scalar lives in the platform keystore via `flutter_secure_storage`;
/// only the public half ever leaves the phone. Signatures are DER-encoded `(r, s)`
/// over SHA-256, which is what Python's `cryptography` produces and expects.
class DeviceKey {
  DeviceKey(this._private, this._public);

  final ECPrivateKey _private;
  final ECPublicKey _public;

  static final _curve = ECCurve_secp256r1();

  static DeviceKey generate() {
    final generator = ECKeyGenerator()
      ..init(ParametersWithRandom(
        ECKeyGeneratorParameters(_curve),
        _secureRandom(),
      ));
    final pair = generator.generateKeyPair();
    return DeviceKey(pair.privateKey, pair.publicKey);
  }

  /// Rebuild from the stored scalar (hex).
  static DeviceKey fromScalarHex(String hex) {
    final d = BigInt.parse(hex, radix: 16);
    final q = _curve.G * d;
    return DeviceKey(ECPrivateKey(d, _curve), ECPublicKey(q, _curve));
  }

  String get scalarHex => _private.d!.toRadixString(16).padLeft(64, '0');

  /// SubjectPublicKeyInfo, PEM — the format the server stores and fingerprints.
  String get publicPem {
    final point = _public.Q!.getEncoded(false); // 0x04 || X || Y
    final algorithm = _sequence([
      _oid([1, 2, 840, 10045, 2, 1]), // ecPublicKey
      _oid([1, 2, 840, 10045, 3, 1, 7]), // prime256v1
    ]);
    final spki = _sequence([algorithm, _bitString(point)]);
    final b64 = base64.encode(spki);
    final lines = <String>[];
    for (var i = 0; i < b64.length; i += 64) {
      lines.add(b64.substring(i, i + 64 > b64.length ? b64.length : i + 64));
    }
    return '-----BEGIN PUBLIC KEY-----\n${lines.join('\n')}\n-----END PUBLIC KEY-----\n';
  }

  /// Base64 of a DER `ECDSA-Sig-Value` over SHA-256(message).
  String sign(List<int> message) {
    final signer = ECDSASigner(SHA256Digest(), HMac(SHA256Digest(), 64))
      ..init(true, PrivateKeyParameter<ECPrivateKey>(_private));
    final sig = signer.generateSignature(Uint8List.fromList(message)) as ECSignature;
    return base64.encode(_sequence([_integer(sig.r), _integer(sig.s)]));
  }

  /// Verify a base64 DER signature with a PEM public key — used for the *server's*
  /// signature on every job, so a spoofed socket cannot hand the phone work.
  static bool verify(String publicPem, List<int> message, String signatureB64) {
    try {
      final key = _publicFromPem(publicPem);
      final sig = _parseDerSignature(base64.decode(signatureB64));
      final verifier = ECDSASigner(SHA256Digest(), HMac(SHA256Digest(), 64))
        ..init(false, PublicKeyParameter<ECPublicKey>(key));
      return verifier.verifySignature(Uint8List.fromList(message), sig);
    } catch (_) {
      return false;
    }
  }

  // ── DER helpers: just enough ASN.1 for SPKI and ECDSA-Sig-Value ─────
  static Uint8List _sequence(List<Uint8List> items) {
    final body = Uint8List.fromList(items.expand((e) => e).toList());
    return Uint8List.fromList([0x30, ..._length(body.length), ...body]);
  }

  static Uint8List _integer(BigInt value) {
    var bytes = _bigIntBytes(value);
    if (bytes.first & 0x80 != 0) bytes = Uint8List.fromList([0, ...bytes]);
    return Uint8List.fromList([0x02, ..._length(bytes.length), ...bytes]);
  }

  static Uint8List _bitString(Uint8List data) =>
      Uint8List.fromList([0x03, ..._length(data.length + 1), 0x00, ...data]);

  static Uint8List _oid(List<int> arcs) {
    final body = <int>[arcs[0] * 40 + arcs[1]];
    for (final arc in arcs.skip(2)) {
      final chunk = <int>[];
      var v = arc;
      chunk.insert(0, v & 0x7f);
      v >>= 7;
      while (v > 0) {
        chunk.insert(0, (v & 0x7f) | 0x80);
        v >>= 7;
      }
      body.addAll(chunk);
    }
    return Uint8List.fromList([0x06, ..._length(body.length), ...body]);
  }

  static List<int> _length(int n) {
    if (n < 0x80) return [n];
    final bytes = <int>[];
    var v = n;
    while (v > 0) {
      bytes.insert(0, v & 0xff);
      v >>= 8;
    }
    return [0x80 | bytes.length, ...bytes];
  }

  static Uint8List _bigIntBytes(BigInt value) {
    var hex = value.toRadixString(16);
    if (hex.length.isOdd) hex = '0$hex';
    final out = Uint8List(hex.length ~/ 2);
    for (var i = 0; i < out.length; i++) {
      out[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
    }
    return out;
  }

  static BigInt _bytesToBigInt(List<int> bytes) =>
      bytes.fold(BigInt.zero, (acc, b) => (acc << 8) | BigInt.from(b));

  static ECSignature _parseDerSignature(List<int> der) {
    // SEQUENCE { INTEGER r, INTEGER s } — lengths are one byte for P-256.
    var i = 2;
    if (der[i] != 0x02) throw const FormatException('not an ECDSA signature');
    final rLen = der[i + 1];
    final r = _bytesToBigInt(der.sublist(i + 2, i + 2 + rLen));
    i += 2 + rLen;
    if (der[i] != 0x02) throw const FormatException('not an ECDSA signature');
    final sLen = der[i + 1];
    final s = _bytesToBigInt(der.sublist(i + 2, i + 2 + sLen));
    return ECSignature(r, s);
  }

  static ECPublicKey _publicFromPem(String pem) {
    final b64 = pem
        .replaceAll('-----BEGIN PUBLIC KEY-----', '')
        .replaceAll('-----END PUBLIC KEY-----', '')
        .replaceAll(RegExp(r'\s'), '');
    final der = base64.decode(b64);
    // The uncompressed point is the last 65 bytes of a P-256 SPKI.
    final point = der.sublist(der.length - 65);
    if (point.first != 0x04) throw const FormatException('unsupported public key');
    final q = _curve.curve.decodePoint(point);
    return ECPublicKey(q, _curve);
  }

  static SecureRandom _secureRandom() {
    final random = Random.secure();
    final seed = Uint8List.fromList(List<int>.generate(32, (_) => random.nextInt(256)));
    return FortunaRandom()..seed(KeyParameter(seed));
  }
}
