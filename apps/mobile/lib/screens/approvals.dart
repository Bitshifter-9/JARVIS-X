import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';

/// Approvals: the human half of the loop.
///
/// Nothing is decided from a summary. The card shows the action, the risk, whether the
/// Mac must confirm as well, and how long the decision stays valid.
class ApprovalsScreen extends ConsumerWidget {
  const ApprovalsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final approvals = ref.watch(approvalsProvider);

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(approvalsProvider),
      child: approvals.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => ListView(children: [
          const SizedBox(height: 96),
          Center(child: Text('Could not load approvals: $e')),
        ]),
        data: (list) {
          final pending = list.where((a) => a.isPending).toList();
          if (pending.isEmpty) {
            return ListView(children: [
              const SizedBox(height: 96),
              Icon(Icons.verified_outlined,
                  size: 48, color: Theme.of(context).disabledColor),
              const SizedBox(height: 12),
              Text('Nothing waiting on you',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleMedium),
            ]);
          }
          return ListView.builder(
            itemCount: pending.length,
            itemBuilder: (context, i) => _ApprovalCard(approval: pending[i]),
          );
        },
      ),
    );
  }
}

class _ApprovalCard extends ConsumerStatefulWidget {
  const _ApprovalCard({required this.approval});

  final Approval approval;

  @override
  ConsumerState<_ApprovalCard> createState() => _ApprovalCardState();
}

class _ApprovalCardState extends ConsumerState<_ApprovalCard> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final approval = widget.approval;
    final remaining = approval.expiresAt.difference(DateTime.now());
    final expired = remaining.isNegative;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.lock_outline, size: 18),
                const SizedBox(width: 8),
                Expanded(
                  child: Text('Approval required',
                      style: Theme.of(context).textTheme.titleMedium),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text('Action ${approval.actionId.substring(0, 8)}…',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            Text(
              expired
                  ? 'Expired — a new proposal is needed'
                  : 'Expires in ${remaining.inMinutes} min',
              style: TextStyle(
                color: expired ? Theme.of(context).colorScheme.error : null,
              ),
            ),
            if (approval.requiresLocalConfirmation) ...[
              const SizedBox(height: 8),
              Row(children: [
                Icon(Icons.warning_amber_outlined,
                    size: 16, color: Colors.orange.shade700),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    approval.locallyConfirmed
                        ? 'Confirmed on your Mac'
                        : 'Also needs confirmation on your Mac',
                    style: TextStyle(color: Colors.orange.shade700, fontSize: 12),
                  ),
                ),
              ]),
            ],
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    key: Key('reject-${approval.id}'),
                    onPressed: _busy || expired ? null : () => _decide(false),
                    child: const Text('Reject'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    key: Key('approve-${approval.id}'),
                    onPressed: _busy || expired ? null : () => _decide(true),
                    child: const Text('Approve'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _decide(bool approved) async {
    setState(() => _busy = true);
    try {
      await ref.read(clientProvider).decide(widget.approval.id, approved: approved);
      ref.invalidate(approvalsProvider);
    } on ProblemException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}
