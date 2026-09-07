import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';

/// Mail intelligence in one place: what you spent, what's due, where you're going, and
/// what changed while you were away — all derived on the server from your mail, no
/// setup. "Scan mail" runs the derivation now; it also runs on the 4-hour connector tick.
class InsightsScreen extends ConsumerStatefulWidget {
  const InsightsScreen({super.key});

  @override
  ConsumerState<InsightsScreen> createState() => _InsightsScreenState();
}

class _InsightsScreenState extends ConsumerState<InsightsScreen> {
  Map<String, dynamic>? _spending;
  Map<String, dynamic>? _away;
  Map<String, dynamic>? _streaks;
  Map<String, dynamic>? _meeting;
  List<Map<String, dynamic>> _travel = const [];
  List<Map<String, dynamic>> _grouped = const [];
  List<Map<String, dynamic>> _anomalies = const [];
  List<Map<String, dynamic>> _commitments = const [];
  Map<String, dynamic> _labels = const {};
  bool _loading = true;
  bool _scanning = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final client = ref.read(clientProvider);
      final results = await Future.wait([
        client.spending(),
        client.travel(),
        client.insightLabels(),
        client.awayDigest(),
        client.streaks(),
        client.meetingPrep(),
        client.groupedActivity(),
        client.anomalies(),
        client.commitments(),
      ]);
      if (!mounted) return;
      setState(() {
        _spending = results[0] as Map<String, dynamic>;
        _travel = results[1] as List<Map<String, dynamic>>;
        _labels = results[2] as Map<String, dynamic>;
        _away = results[3] as Map<String, dynamic>;
        _streaks = results[4] as Map<String, dynamic>?;
        _meeting = results[5] as Map<String, dynamic>?;
        _grouped = results[6] as List<Map<String, dynamic>>;
        _anomalies = results[7] as List<Map<String, dynamic>>;
        _commitments = results[8] as List<Map<String, dynamic>>;
        _loading = false;
      });
    } on ProblemException catch (e) {
      if (mounted) {
        setState(() {
          _error = '$e';
          _loading = false;
        });
      }
    }
  }

  Future<void> _scan() async {
    setState(() => _scanning = true);
    try {
      final r = await ref.read(clientProvider).insightsScan();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Read ${r['new']} new mail')));
      }
      await _load();
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _scanning = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Insights'),
        actions: [
          IconButton(
            tooltip: 'Scan mail now',
            icon: _scanning
                ? const SizedBox(
                    width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.refresh),
            onPressed: _scanning ? null : _scan,
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(12),
                    children: [
                      if (_commitments.isNotEmpty)
                        _CommitmentsCard(
                          commitments: _commitments,
                          onDone: (id) async {
                            await ref.read(clientProvider).commitmentDone(id);
                            _load();
                          },
                          onDrop: (id) async {
                            await ref.read(clientProvider).commitmentDrop(id);
                            _load();
                          },
                        ),
                      if (_commitments.isNotEmpty) const SizedBox(height: 8),
                      if (_anomalies.isNotEmpty) _AnomalyCard(nudges: _anomalies),
                      if (_anomalies.isNotEmpty) const SizedBox(height: 8),
                      if (_meeting != null) _MeetingCard(meeting: _meeting!),
                      if (_meeting != null) const SizedBox(height: 8),
                      _StreaksCard(streaks: _streaks),
                      const SizedBox(height: 8),
                      if (_grouped.isNotEmpty) _GroupedCard(groups: _grouped),
                      if (_grouped.isNotEmpty) const SizedBox(height: 8),
                      _AwayCard(away: _away),
                      const SizedBox(height: 8),
                      _SpendingCard(spending: _spending),
                      const SizedBox(height: 8),
                      if (_travel.isNotEmpty) _TravelCard(trips: _travel),
                      if (_travel.isNotEmpty) const SizedBox(height: 8),
                      _LabelsCard(labels: _labels),
                    ],
                  ),
                ),
    );
  }
}

String _money(num amount, String? currency) {
  const symbols = {'INR': '₹', 'USD': '\$', 'EUR': '€', 'GBP': '£'};
  final sym = symbols[currency] ?? (currency == null ? '' : '$currency ');
  return '$sym${amount.toStringAsFixed(2)}';
}

class _AwayCard extends StatelessWidget {
  const _AwayCard({required this.away});
  final Map<String, dynamic>? away;

  @override
  Widget build(BuildContext context) {
    final total = away?['total'] as int? ?? 0;
    final byLabel = (away?['by_label'] as Map<String, dynamic>? ?? {});
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('While you were away', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          Text(total == 0 ? 'Nothing new in the last day.' : '$total new in the last day',
              style: Theme.of(context).textTheme.bodySmall),
          if (byLabel.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final e in byLabel.entries)
                Chip(
                  label: Text('${e.key} · ${e.value}'),
                  visualDensity: VisualDensity.compact,
                ),
            ]),
          ],
        ]),
      ),
    );
  }
}

class _SpendingCard extends StatelessWidget {
  const _SpendingCard({required this.spending});
  final Map<String, dynamic>? spending;

  @override
  Widget build(BuildContext context) {
    final totals = (spending?['totals'] as Map<String, dynamic>? ?? {});
    final bills = (spending?['bills_due'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Spending · last 30 days', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          if (totals.isEmpty)
            Text('No receipts read yet.', style: Theme.of(context).textTheme.bodySmall)
          else
            Wrap(spacing: 16, children: [
              for (final e in totals.entries)
                Text(_money(e.value as num, e.key),
                    style: Theme.of(context)
                        .textTheme
                        .headlineSmall
                        ?.copyWith(fontWeight: FontWeight.bold)),
            ]),
          if (bills.isNotEmpty) ...[
            const Divider(height: 20),
            Text('Bills due', style: Theme.of(context).textTheme.labelLarge),
            for (final b in bills)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.receipt_long, size: 20),
                title: Text(b['merchant'] as String? ?? 'Bill'),
                trailing: Text(_money(b['amount'] as num, b['currency'] as String?)),
              ),
          ],
        ]),
      ),
    );
  }
}

class _TravelCard extends StatelessWidget {
  const _TravelCard({required this.trips});
  final List<Map<String, dynamic>> trips;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Travel', style: Theme.of(context).textTheme.titleMedium),
          for (final t in trips)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(t['type'] == 'flight' ? Icons.flight : Icons.hotel, size: 20),
              title: Text(t['where'] as String? ?? (t['type'] as String? ?? 'Trip')),
              subtitle: Text([
                if (t['when'] != null) t['when'],
                if (t['ref'] != null) 'Ref ${t['ref']}',
              ].join(' · ')),
            ),
        ]),
      ),
    );
  }
}

class _LabelsCard extends StatelessWidget {
  const _LabelsCard({required this.labels});
  final Map<String, dynamic> labels;

  @override
  Widget build(BuildContext context) {
    if (labels.isEmpty) return const SizedBox.shrink();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Mail by project', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final e in labels.entries)
              Chip(
                label: Text('${e.key} · ${e.value}'),
                visualDensity: VisualDensity.compact,
              ),
          ]),
        ]),
      ),
    );
  }
}

class _MeetingCard extends StatelessWidget {
  const _MeetingCard({required this.meeting});
  final Map<String, dynamic> meeting;

  @override
  Widget build(BuildContext context) {
    final mail = (meeting['related_mail'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
    final deadlines =
        (meeting['nearby_deadlines'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
    final when = meeting['when'] != null
        ? DateTime.tryParse(meeting['when'] as String)?.toLocal()
        : null;
    return Card(
      color: Theme.of(context).colorScheme.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.event_note, size: 20),
            const SizedBox(width: 8),
            Text('Next up', style: Theme.of(context).textTheme.labelLarge),
          ]),
          const SizedBox(height: 6),
          Text(meeting['title'] as String? ?? 'Meeting',
              style: Theme.of(context).textTheme.titleMedium),
          if (when != null)
            Text(
              '${_weekday(when)} ${when.hour.toString().padLeft(2, '0')}:'
              '${when.minute.toString().padLeft(2, '0')}'
              '${meeting['organizer'] != null ? ' · ${meeting['organizer']}' : ''}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          if (mail.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('Recent mail', style: Theme.of(context).textTheme.labelMedium),
            for (final m in mail.take(3))
              Text('• ${m['subject'] ?? ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodySmall),
          ],
          if (deadlines.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('Around then', style: Theme.of(context).textTheme.labelMedium),
            for (final d in deadlines)
              Text('• ${d['title'] ?? ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodySmall),
          ],
        ]),
      ),
    );
  }

  static String _weekday(DateTime d) =>
      const ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][d.weekday - 1];
}

class _StreaksCard extends StatelessWidget {
  const _StreaksCard({required this.streaks});
  final Map<String, dynamic>? streaks;

  @override
  Widget build(BuildContext context) {
    final focus = (streaks?['focus'] as Map<String, dynamic>? ?? const {});
    final done = (streaks?['done'] as Map<String, dynamic>? ?? const {});
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Streaks', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(child: _streak(context, '🔥', 'Focus days', focus)),
            Expanded(child: _streak(context, '✅', 'Done days', done)),
          ]),
        ]),
      ),
    );
  }

  Widget _streak(BuildContext context, String emoji, String label, Map<String, dynamic> s) {
    final current = s['current'] as int? ?? 0;
    final longest = s['longest'] as int? ?? 0;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('$emoji $current day${current == 1 ? '' : 's'}',
          style: Theme.of(context).textTheme.titleLarge),
      Text('$label · best $longest', style: Theme.of(context).textTheme.bodySmall),
    ]);
  }
}

class _AnomalyCard extends StatelessWidget {
  const _AnomalyCard({required this.nudges});
  final List<Map<String, dynamic>> nudges;

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Theme.of(context).colorScheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.notification_important_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Worth a look', style: Theme.of(context).textTheme.labelLarge),
          ]),
          const SizedBox(height: 6),
          for (final n in nudges)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Text(n['message'] as String? ?? '',
                  style: Theme.of(context).textTheme.bodyMedium),
            ),
        ]),
      ),
    );
  }
}

class _GroupedCard extends StatelessWidget {
  const _GroupedCard({required this.groups});
  final List<Map<String, dynamic>> groups;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Grouped · last 48h', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          for (final g in groups)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(
                  g['provider'] == 'slack' ? Icons.tag : Icons.mail_outline,
                  size: 20),
              title: Text('${g['who']} · ${g['count']}',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: Text(g['latest'] as String? ?? '',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
            ),
        ]),
      ),
    );
  }
}

/// Promises you made, caught from your own words (second-brain #22). Tick when kept.
class _CommitmentsCard extends StatelessWidget {
  const _CommitmentsCard(
      {required this.commitments, required this.onDone, required this.onDrop});

  final List<Map<String, dynamic>> commitments;
  final Future<void> Function(String id) onDone;
  final Future<void> Function(String id) onDrop;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.handshake_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Promises you made', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 4),
          for (final c in commitments)
            Dismissible(
              key: ValueKey(c['id']),
              direction: DismissDirection.endToStart,
              onDismissed: (_) => onDrop(c['id'] as String),
              background: Container(
                alignment: Alignment.centerRight,
                padding: const EdgeInsets.only(right: 16),
                child: const Icon(Icons.delete_outline),
              ),
              child: ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: IconButton(
                  tooltip: 'Kept it',
                  icon: const Icon(Icons.check_circle_outline),
                  onPressed: () => onDone(c['id'] as String),
                ),
                title: Text(c['text'] as String? ?? '',
                    maxLines: 2, overflow: TextOverflow.ellipsis),
                subtitle: c['due'] != null
                    ? Text('by ${_due(c['due'] as String)}',
                        style: Theme.of(context).textTheme.bodySmall)
                    : null,
              ),
            ),
        ]),
      ),
    );
  }

  static String _due(String iso) {
    final d = DateTime.tryParse(iso)?.toLocal();
    if (d == null) return iso;
    return '${const [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ][d.month - 1]} ${d.day}';
  }
}
