import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// What's new (FEATURES-50 #43). A static, curated list — shown from Settings and,
/// once, on the first launch after the version at the top changes.
class Changelog {
  static const seenKey = 'seen_changelog';

  /// Newest first. Bump the top version when you add an entry.
  static const entries = <(String, String, List<String>)>[
    (
      '0.9',
      'September 2026',
      [
        'Free, on-device wake word — "Hey Jarvis" needs no Picovoice key any more.',
        'Insights: spending, bills due, travel, and what changed while you were away.',
        'Next-up meeting prep and focus/done streaks.',
        'Checklists, estimates and time-left on deadlines.',
        'A library of role personas to switch Jarvis\'s voice for the task.',
      ],
    ),
    (
      '0.8',
      'September 2026',
      [
        'Scan Slack like Gmail, group DMs included; both scan every 4 hours.',
        'Ring a device, request its location, and place real calls from chat.',
        'Command palette (⌘K) and quick capture from anywhere.',
      ],
    ),
  ];

  static String get latest => entries.first.$1;

  /// Show the sheet once if the stored version differs from the latest.
  static Future<void> showIfNew(BuildContext context) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (prefs.getString(seenKey) == latest) return;
      await prefs.setString(seenKey, latest);
    } catch (_) {
      return; // no prefs: don't nag
    }
    if (context.mounted) open(context);
  }

  static void open(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => const _ChangelogSheet(),
    );
  }
}

class _ChangelogSheet extends StatelessWidget {
  const _ChangelogSheet();

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.6,
      maxChildSize: 0.9,
      builder: (context, controller) => ListView(
        controller: controller,
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
        children: [
          Text("What's new", style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 12),
          for (final (version, date, changes) in Changelog.entries) ...[
            Row(children: [
              Text('v$version', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(width: 8),
              Text(date, style: Theme.of(context).textTheme.bodySmall),
            ]),
            const SizedBox(height: 6),
            for (final c in changes)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('•  '),
                  Expanded(child: Text(c)),
                ]),
              ),
            const SizedBox(height: 16),
          ],
        ],
      ),
    );
  }
}
