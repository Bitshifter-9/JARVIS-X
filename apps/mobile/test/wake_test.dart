import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_x/voice/wake_service.dart';

void main() {
  group('commandAfterWake', () {
    test('returns the tail after the wake word', () {
      expect(commandAfterWake("jarvis what's my next deadline"), "what's my next deadline");
      expect(commandAfterWake('Jarvis, turn on the lights'), 'turn on the lights');
      expect(commandAfterWake('hey Jarvis - call mom'), 'call mom');
    });

    test('empty string when only the wake word is said', () {
      expect(commandAfterWake('jarvis'), '');
      expect(commandAfterWake('Jarvis.'), '');
    });

    test('null when the wake word is absent', () {
      expect(commandAfterWake('what time is it'), isNull);
      // A substring is not a word-boundary match.
      expect(commandAfterWake('jarvised the report'), isNull);
    });
  });
}
