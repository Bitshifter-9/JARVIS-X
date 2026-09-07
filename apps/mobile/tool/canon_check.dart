// ignore_for_file: avoid_print
// Prints Dart's canonical JSON for the fixtures Python also canonicalizes.
// scratchpad/canon_fixtures.py prints the same list from the server's function;
// the two outputs must be identical byte for byte.
import 'dart:convert';

import 'package:jarvis_x/node/canonical.dart';

void main() {
  final cases = <Object>[
    {
      'b': 1,
      'a': {
        'z': [1, 2.5, 'x'],
        'y': null,
        'm': true,
      },
    },
    {
      'job_id': 'job_1',
      'action': 'phone.open_url',
      'args': {'url': 'https://e.test/?q=a&b=ü'},
      'risk': 'R1',
      'nonce': 'n',
      'issued_at': '2026-09-06T10:00:00+00:00',
      'expires_at': '2026-09-06T10:05:00+00:00',
      'policy_version': 1,
      'device_id': 'd',
    },
    {'text': 'quote" back\\ nl\n tab\t ctl\x01 two '},
    {
      'observed': {
        'opened': true,
        'opened_url': 'whatsapp://send?phone=91&text=hi%20there',
      },
      'status': 'completed',
      'job_id': 'j',
      'error': null,
      'reject_reason': null,
    },
  ];
  print(jsonEncode(cases.map(canonicalJson).toList()));
}
