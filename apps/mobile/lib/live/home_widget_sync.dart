import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:home_widget/home_widget.dart';
import 'package:intl/intl.dart';

import '../api/models.dart';

/// Pushes the next deadline to the Android home-screen widget (FEATURES-50 #48).
class NextDeadlineWidgetSync {
  static bool get supported => !kIsWeb && Platform.isAndroid;

  static Future<void> update(List<Task> tasks) async {
    if (!supported) return;
    final dated = tasks
        .where((t) => t.dueAt != null && t.status == 'open')
        .toList()
      ..sort((a, b) => a.dueAt!.compareTo(b.dueAt!));
    final next = dated.isNotEmpty ? dated.first : null;
    try {
      await HomeWidget.saveWidgetData<String>(
          'next_deadline', next?.title ?? 'No deadlines');
      await HomeWidget.saveWidgetData<String>('next_deadline_sub',
          next != null ? _when(next.dueAt!) : "You're all clear");
      await HomeWidget.updateWidget(androidName: 'NextDeadlineWidget');
    } catch (_) {}
  }

  static String _when(DateTime due) {
    final left = due.difference(DateTime.now());
    if (left.isNegative) return 'Overdue';
    if (left.inHours < 24) return 'Due ${DateFormat('HH:mm').format(due)}';
    return 'Due ${DateFormat('EEE d MMM').format(due)}';
  }
}
