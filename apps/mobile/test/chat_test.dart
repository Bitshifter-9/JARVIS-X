import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_x/screens/chat.dart';

void main() {
  group('followUps', () {
    test('offers a deadline chip when the reply is about tasks', () {
      final chips = followUps('Your assignment is due Friday.');
      expect(chips, contains('Add to my deadlines'));
      expect(chips.length, lessThanOrEqualTo(3));
    });

    test('offers a reply chip for messages/email', () {
      expect(followUps('I drafted an email to your professor.'),
          contains('Draft a reply'));
    });

    test('always ends with a generic follow-up, deduped', () {
      final chips = followUps('Sure.');
      expect(chips, contains('Tell me more'));
      expect(chips.toSet().length, chips.length); // no duplicates
    });
  });
}
