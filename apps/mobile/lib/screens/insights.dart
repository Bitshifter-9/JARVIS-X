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
  List<Map<String, dynamic>> _travel = const [];
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
      ]);
      if (!mounted) return;
      setState(() {
        _spending = results[0] as Map<String, dynamic>;
        _travel = results[1] as List<Map<String, dynamic>>;
        _labels = results[2] as Map<String, dynamic>;
        _away = results[3] as Map<String, dynamic>;
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
