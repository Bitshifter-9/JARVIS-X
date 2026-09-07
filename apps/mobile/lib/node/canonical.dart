import 'dart:convert';

/// Byte-for-byte the server's `canonical_json`: sorted keys, no whitespace, UTF-8
/// left unescaped. The signature covers these bytes, so the two sides must agree on
/// every character — this mirrors `json.dumps(sort_keys=True, separators=(",", ":"),
/// ensure_ascii=False)` exactly.
String canonicalJson(Object? value) {
  final buffer = StringBuffer();
  _write(value, buffer);
  return buffer.toString();
}

void _write(Object? value, StringBuffer out) {
  if (value == null) {
    out.write('null');
  } else if (value is bool) {
    out.write(value ? 'true' : 'false');
  } else if (value is num) {
    out.write(_number(value));
  } else if (value is String) {
    out.write(_string(value));
  } else if (value is List) {
    out.write('[');
    for (var i = 0; i < value.length; i++) {
      if (i > 0) out.write(',');
      _write(value[i], out);
    }
    out.write(']');
  } else if (value is Map) {
    final keys = value.keys.map((k) => k.toString()).toList()..sort();
    out.write('{');
    for (var i = 0; i < keys.length; i++) {
      if (i > 0) out.write(',');
      out.write(_string(keys[i]));
      out.write(':');
      _write(value[keys[i]], out);
    }
    out.write('}');
  } else {
    throw ArgumentError('cannot canonicalize ${value.runtimeType}');
  }
}

String _number(num value) {
  if (value is int) return value.toString();
  final d = value.toDouble();
  if (d == d.truncateToDouble() && d.abs() < 1e16) {
    // Python prints 2.0 as "2.0"; make the whole-number double case explicit.
    return '${d.toInt()}.0';
  }
  return d.toString();
}

/// Python's escaping with ensure_ascii=False: only `"`, `\` and control characters.
String _string(String value) {
  final out = StringBuffer('"');
  for (final unit in value.codeUnits) {
    switch (unit) {
      case 0x22:
        out.write(r'\"');
      case 0x5c:
        out.write(r'\\');
      case 0x0a:
        out.write(r'\n');
      case 0x0d:
        out.write(r'\r');
      case 0x09:
        out.write(r'\t');
      case 0x08:
        out.write(r'\b');
      case 0x0c:
        out.write(r'\f');
      default:
        if (unit < 0x20) {
          out.write('\\u${unit.toRadixString(16).padLeft(4, '0')}');
        } else {
          out.writeCharCode(unit);
        }
    }
  }
  out.write('"');
  return out.toString();
}

List<int> canonicalBytes(Object? value) => utf8.encode(canonicalJson(value));
