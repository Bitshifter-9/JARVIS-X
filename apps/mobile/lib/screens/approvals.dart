import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/states.dart';

/// Approvals: the human half of the loop.
///
/// Nothing is decided from a summary. The card shows the action, the risk, whether the
/// Mac must confirm as well, and how long the decision stays valid. Swipe right to
/// approve, left to reject — or use the buttons.
class ApprovalsScreen extends ConsumerWidget {
  const ApprovalsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final approvals = ref.watch(approvalsProvider);

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(approvalsProvider),
      child: approvals.when(
        loading: () => const SkeletonList(rows: 3, height: 170),
        error: (e, _) =>
            ErrorState(message: '$e', onRetry: () => ref.invalidate(approvalsProvider)),
        data: (list) {
          final pending = list.where((a) => a.isPending).toList();
          return ListView(
            padding: const EdgeInsets.only(top: 8, bottom: 24),
            children: [
              if (pending.isEmpty)
                const SizedBox(
                  height: 320,
                  child: EmptyState(
                    icon: Icons.verified_outlined,
                    title: 'Nothing waiting on you',
                    body: 'Anything with an effect on the outside world stops here first.',
                  ),
                ),
              for (final (i, a) in pending.indexed)
                _ApprovalCard(key: ValueKey(a.id), approval: a).enter(i),
              const _StandingPermissions(),
            ],
          );
        },
      ),
    );
  }
}

class _ApprovalCard extends ConsumerStatefulWidget {
  const _ApprovalCard({super.key, required this.approval});

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
    final scheme = Theme.of(context).colorScheme;

    return Dismissible(
      key: ValueKey('swipe-${approval.id}'),
      direction: expired || _busy ? DismissDirection.none : DismissDirection.horizontal,
      background: _swipeHint(context, approve: true),
      secondaryBackground: _swipeHint(context, approve: false),
      confirmDismiss: (direction) async {
        HapticFeedback.mediumImpact();
        await _decide(direction == DismissDirection.startToEnd);
        return false; // the list refreshes from the server; nothing is removed locally
      },
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: JarvisColors.warning.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Icon(Icons.lock_outline, size: 18, color: JarvisColors.warning),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text('Approval required', style: Theme.of(context).textTheme.titleMedium),
                ),
                if (approval.tool != null)
                  Chip(
                    label: Text(approval.tool!, style: const TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                  ),
              ]),
              const SizedBox(height: 10),
              Text(
                approval.summary ?? 'Action ${approval.actionId.substring(0, 8)}…',
                style: Theme.of(context).textTheme.bodyLarge?.copyWith(height: 1.35),
              ),
              if (approval.tool == 'youtube.upload') ...[
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  icon: const Icon(Icons.play_circle_outline, size: 18),
                  label: const Text('Watch before approving'),
                  onPressed: () async {
                    try {
                      final url =
                          await ref.read(clientProvider).videoPreviewUrl(approval.actionId);
                      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
                    } on ProblemException catch (e) {
                      if (!context.mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                    }
                  },
                ),
              ],
              const SizedBox(height: 10),
              if (!expired)
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    minHeight: 3,
                    value: (remaining.inSeconds / 600).clamp(0.0, 1.0),
                    color: remaining.inMinutes < 2 ? scheme.error : JarvisColors.warning,
                    backgroundColor: scheme.onSurface.withValues(alpha: 0.08),
                  ),
                ),
              const SizedBox(height: 8),
              Row(children: [
                Icon(Icons.schedule, size: 14,
                    color: expired ? scheme.error : scheme.onSurface.withValues(alpha: 0.6)),
                const SizedBox(width: 6),
                Text(
                  expired
                      ? 'Expired — a new proposal is needed'
                      : 'Expires in ${remaining.inMinutes} min',
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: expired ? scheme.error : scheme.onSurface.withValues(alpha: 0.6)),
                ),
              ]),
              if (approval.requiresLocalConfirmation) ...[
                const SizedBox(height: 8),
                Row(children: [
                  const Icon(Icons.laptop_mac, size: 16, color: JarvisColors.warning),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      approval.locallyConfirmed
                          ? 'Confirmed on your Mac'
                          : 'Also needs confirmation on your Mac',
                      style: const TextStyle(color: JarvisColors.warning, fontSize: 12),
                    ),
                  ),
                ]),
              ],
              const SizedBox(height: 16),
              if (approval.tool != null && _envelopeTools.contains(approval.tool))
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: _busy || expired ? null : _alwaysAllow,
                    icon: const Icon(Icons.all_inclusive, size: 16),
                    label: const Text('Always allow this for 7 days'),
                  ),
                ),
              Row(children: [
                Expanded(
                  child: OutlinedButton.icon(
                    key: Key('reject-${approval.id}'),
                    onPressed: _busy || expired ? null : () => _decide(false),
                    icon: const Icon(Icons.close, size: 18),
                    label: const Text('Reject'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton.icon(
                    key: Key('approve-${approval.id}'),
                    onPressed: _busy || expired ? null : () => _decide(true),
                    icon: const Icon(Icons.check, size: 18),
                    label: const Text('Approve'),
                  ),
                ),
              ]),
              if (_busy) ...[
                const SizedBox(height: 10),
                const LinearProgressIndicator(minHeight: 2),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _swipeHint(BuildContext context, {required bool approve}) {
    final color = approve ? JarvisColors.success : JarvisColors.danger;
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      padding: const EdgeInsets.symmetric(horizontal: 24),
      alignment: approve ? Alignment.centerLeft : Alignment.centerRight,
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.18),
        borderRadius: BorderRadius.circular(18),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(approve ? Icons.check_circle : Icons.cancel, color: color),
        const SizedBox(width: 8),
        Text(approve ? 'Approve' : 'Reject',
            style: TextStyle(color: color, fontWeight: FontWeight.w700)),
      ]),
    );
  }

  /// R2 tools the server lets an owner pre-approve inside an envelope (10.9.2).
  static const _envelopeTools = {
    'youtube.upload', 'calendar.create_event', 'mac.capture_screen', 'mac.describe_screen',
  };

  Future<void> _alwaysAllow() async {
    setState(() => _busy = true);
    try {
      await ref.read(clientProvider).grantPermission(widget.approval.tool!, days: 7);
      await ref.read(clientProvider).decide(widget.approval.id, approved: true);
      ref.invalidate(approvalsProvider);
      ref.invalidate(permissionsProvider);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Approved, and allowed for 7 days — revoke below any time')));
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _decide(bool approved) async {
    setState(() => _busy = true);
    try {
      await ref.read(clientProvider).decide(widget.approval.id, approved: approved);
      HapticFeedback.selectionClick();
      ref.invalidate(approvalsProvider);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(approved ? 'Approved — running now' : 'Rejected')));
    } on ProblemException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}


/// What runs without asking, and until when. One tap revokes.
class _StandingPermissions extends ConsumerWidget {
  const _StandingPermissions();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final grants = ref.watch(permissionsProvider).valueOrNull ?? const [];
    if (grants.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Runs without asking', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 4),
        Text('Standing permissions you granted. Each expires on its own; revoke any time.',
            style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 8),
        for (final g in grants)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              leading: const Icon(Icons.all_inclusive),
              title: Text(g['tool'] as String),
              subtitle: Text([
                if ((g['conditions'] as Map).isNotEmpty) 'only ${g['conditions']}',
                if (g['max_per_day'] != null) '${g['max_per_day']}/day',
                if (g['expires_at'] != null)
                  'until ${DateTime.parse(g['expires_at'] as String).toLocal().toString().substring(0, 16)}',
              ].join(' · ')),
              trailing: IconButton(
                tooltip: 'Revoke',
                icon: const Icon(Icons.block),
                onPressed: () async {
                  try {
                    await ref.read(clientProvider).revokePermission(g['id'] as String);
                    ref.invalidate(permissionsProvider);
                  } on ProblemException catch (e) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                    }
                  }
                },
              ),
            ),
          ),
      ]),
    );
  }
}
