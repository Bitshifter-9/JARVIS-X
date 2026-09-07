import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_x/state/connectivity.dart';

void main() {
  group('Net.isNetworkError', () {
    test('recognises transport errors, not server rejections', () {
      expect(Net.isNetworkError(Exception('SocketException: Failed host lookup')), isTrue);
      expect(Net.isNetworkError(Exception('TimeoutException after 0:00:10')), isTrue);
      expect(Net.isNetworkError('ClientException: Connection closed'), isTrue);
      expect(Net.isNetworkError(Exception('400 Bad Request')), isFalse);
      expect(Net.isNetworkError(Exception('Task was modified by someone else')), isFalse);
    });

    test('markOffline/markOnline flip the notifier', () {
      Net.offline.value = false;
      Net.markOffline();
      expect(Net.offline.value, isTrue);
      Net.markOnline();
      expect(Net.offline.value, isFalse);
    });
  });
}
