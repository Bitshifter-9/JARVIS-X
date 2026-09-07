import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../state/providers.dart';
import '../widgets/states.dart';

/// The account's audit trail — every action, filterable. Nothing here can be edited;
/// it is the record.
class AuditScreen extends ConsumerStatefulWidget {
  const AuditScreen({super.key});

  @override
  ConsumerState<AuditScreen> createState() => _AuditScreenState();
}

class _AuditScreenState extends ConsumerState<AuditScreen> {
  String? _action;
  late Future<List<Map<String, dynamic>>> _rows;
  List<String> _actions = const [];

  @override
  void initState() {
    super.initState();
    _rows = ref.read(clientProvider).auditLog();
    ref.read(clientProvider).auditActions().then((a) {
      if (mounted) setState(() => _actions = a);
    }).catchError((_) {});
  }

  void _reload() => setState(() => _rows = ref.read(clientProvider).auditLog(action: _action));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Audit trail')),
      body: Column(children: [
        if (_actions.isNotEmpty)
          SizedBox(
            height: 52,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              children: [
                ChoiceChip(
                  label: const Text('All'),
                  selected: _action == null,
                  onSelected: (_) {
                    _action = null;
                    _reload();
                  },
                ),
                const SizedBox(width: 8),
                for (final a in _actions) ...[
                  ChoiceChip(
                    label: Text(a),
                    selected: _action == a,
                    onSelected: (_) {
                      _action = a;
                      _reload();
                    },
                  ),
                  const SizedBox(width: 8),
                ],
              ],
            ),
          ),
        Expanded(
          child: FutureBuilder<List<Map<String, dynamic>>>(
            future: _rows,
            builder: (context, snap) {
              if (snap.hasError) {
                return ErrorState(message: '${snap.error}', onRetry: _reload);
              }
              if (!snap.hasData) return const SkeletonList(rows: 8, height: 64);
              final rows = snap.data!;
              if (rows.isEmpty) {
                return const EmptyState(
                    icon: Icons.receipt_long, title: 'Nothing recorded yet');
              }
              return ListView.separated(
                padding: const EdgeInsets.only(bottom: 24),
                itemCount: rows.length,
                separatorBuilder: (_, __) => const Divider(height: 1),
                itemBuilder: (context, i) {
                  final r = rows[i];
                  final detail = (r['detail'] as Map).isEmpty
                      ? null
                      : (r['detail'] as Map).entries
                          .take(3)
                          .map((e) => '${e.key}: ${e.value}')
                          .join(' · ');
                  return ListTile(
                    dense: true,
                    leading: CircleAvatar(
                      radius: 16,
                      child: Text((r['actor'] as String).substring(0, 1).toUpperCase(),
                          style: const TextStyle(fontSize: 12)),
                    ),
                    title: Text(r['action'] as String,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    subtitle: Text([
                      DateFormat('d MMM, HH:mm')
                          .format(DateTime.parse(r['at'] as String).toLocal()),
                      if (detail != null) detail,
                    ].join('  ·  '), maxLines: 2, overflow: TextOverflow.ellipsis),
                  );
                },
              );
            },
          ),
        ),
      ]),
    );
  }
}
