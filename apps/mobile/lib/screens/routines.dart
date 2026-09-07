import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/states.dart';

/// Routines: "every morning at 7, brief me", "when my professor mails, summarise it".
/// A switch per routine, the next time it fires, what it said last time, and Run now.
class RoutinesScreen extends ConsumerWidget {
  const RoutinesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final routines = ref.watch(routinesProvider);
    return Scaffold(
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(routinesProvider),
        child: routines.when(
          loading: () => const SkeletonList(rows: 4, height: 120),
          error: (e, _) =>
              ErrorState(message: '$e', onRetry: () => ref.invalidate(routinesProvider)),
          data: (list) => ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 96),
            children: [
              for (final (i, r) in list.indexed) _RoutineCard(routine: r).enter(i),
            ],
          ),
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _edit(context, ref, null),
        icon: const Icon(Icons.add),
        label: const Text('Routine'),
      ),
    );
  }

  static Future<void> _edit(BuildContext context, WidgetRef ref, Map<String, dynamic>? existing) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (context) => _Editor(existing: existing),
    );
    if (saved == true) ref.invalidate(routinesProvider);
  }
}

class _RoutineCard extends ConsumerStatefulWidget {
  const _RoutineCard({required this.routine});
  final Map<String, dynamic> routine;

  @override
  ConsumerState<_RoutineCard> createState() => _RoutineCardState();
}

class _RoutineCardState extends ConsumerState<_RoutineCard> {
  bool _busy = false;

  Future<void> _guard(Future<void> Function() f, {String? done}) async {
    setState(() => _busy = true);
    try {
      await f();
      ref.invalidate(routinesProvider);
      if (done != null && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(done)));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = widget.routine;
    final client = ref.read(clientProvider);
    final enabled = r['enabled'] == true;
    final next = r['next_run_at'] as String?;
    final last = r['last_run_at'] as String?;
    final trigger = r['trigger'] as Map<String, dynamic>?;
    final when = r['cron'] != null
        ? _describeCron(r['cron'] as String)
        : 'When a ${trigger?['provider'] ?? 'message'} arrives'
            '${trigger?['from'] != null ? ' from "${trigger!['from']}"' : ''}'
            '${trigger?['subject'] != null ? ' about "${trigger!['subject']}"' : ''}';
    final channelIcon = switch (r['channel']) {
      'call' => Icons.call,
      'telegram' => Icons.send,
      _ => Icons.notifications_active_outlined,
    };

    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: () => RoutinesScreen._edit(context, ref, r),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(channelIcon, size: 18, color: enabled ? JarvisColors.cyan : null),
              const SizedBox(width: 10),
              Expanded(
                child: Text(r['name'] as String,
                    style: Theme.of(context).textTheme.titleMedium),
              ),
              Switch.adaptive(
                value: enabled,
                onChanged: _busy
                    ? null
                    : (v) => _guard(() async {
                          await client.updateRoutine(r['id'] as String, {'enabled': v});
                        }),
              ),
            ]),
            const SizedBox(height: 4),
            Text('$when · ${r['mode'] == 'brief' ? 'tells you' : 'does things'}',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.7))),
            const SizedBox(height: 6),
            Text(
              [
                if (enabled && next != null)
                  'Next ${DateFormat('EEE d MMM, HH:mm').format(DateTime.parse(next).toLocal())} '
                      '(${_countdown(DateTime.parse(next).toLocal())})',
                if (last != null)
                  'Last ${DateFormat('d MMM, HH:mm').format(DateTime.parse(last).toLocal())}',
                if (!enabled) 'Off',
              ].join(' · '),
              style: Theme.of(context).textTheme.labelSmall,
            ),
            if (r['last_result'] != null) ...[
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: context.surfaces.surface3,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(r['last_result'] as String,
                    maxLines: 4, overflow: TextOverflow.ellipsis),
              ),
            ],
            const SizedBox(height: 6),
            Row(children: [
              TextButton.icon(
                onPressed: _busy
                    ? null
                    : () => _guard(() async {
                          await client.runRoutine(r['id'] as String);
                        }, done: 'Running — the result lands in its thread and on your channel'),
                icon: const Icon(Icons.play_arrow, size: 18),
                label: const Text('Run now'),
              ),
              const Spacer(),
              IconButton(
                tooltip: 'Delete',
                onPressed: _busy
                    ? null
                    : () => _guard(() => client.deleteRoutine(r['id'] as String), done: 'Deleted'),
                icon: const Icon(Icons.delete_outline, size: 20),
              ),
            ]),
          ]),
        ),
      ),
    );
  }
}

String _countdown(DateTime at) {
  final d = at.difference(DateTime.now());
  if (d.isNegative) return 'now';
  if (d.inHours >= 24) return 'in ${d.inDays}d ${d.inHours % 24}h';
  if (d.inHours >= 1) return 'in ${d.inHours}h ${d.inMinutes % 60}m';
  return 'in ${d.inMinutes}m';
}

const _presets = <String, String>{
  'Every morning 07:00': '0 7 * * *',
  'Weekday mornings 08:00': '0 8 * * 1-5',
  'Every evening 21:00': '0 21 * * *',
  'Sunday 18:00': '0 18 * * 0',
  'Every hour': '0 * * * *',
};

String _describeCron(String cron) {
  for (final e in _presets.entries) {
    if (e.value == cron) return e.key;
  }
  return 'On schedule $cron';
}

class _Editor extends ConsumerStatefulWidget {
  const _Editor({this.existing});
  final Map<String, dynamic>? existing;

  @override
  ConsumerState<_Editor> createState() => _EditorState();
}

class _EditorState extends ConsumerState<_Editor> {
  late final _name = TextEditingController(text: widget.existing?['name'] as String? ?? '');
  late final _prompt = TextEditingController(text: widget.existing?['prompt'] as String? ?? '');
  late final _cron = TextEditingController(text: widget.existing?['cron'] as String? ?? '0 7 * * *');
  late final _from = TextEditingController(
      text: (widget.existing?['trigger'] as Map<String, dynamic>?)?['from'] as String? ?? '');
  late final _subject = TextEditingController(
      text: (widget.existing?['trigger'] as Map<String, dynamic>?)?['subject'] as String? ?? '');
  late bool _onSchedule = widget.existing == null || widget.existing!['cron'] != null;
  late String _provider =
      (widget.existing?['trigger'] as Map<String, dynamic>?)?['provider'] as String? ?? 'gmail';
  late String _channel = widget.existing?['channel'] as String? ?? 'app';
  late String _mode = widget.existing?['mode'] as String? ?? 'brief';
  String? _error;
  bool _saving = false;

  @override
  Widget build(BuildContext context) {
    final preset = _presets.entries
        .cast<MapEntry<String, String>?>()
        .firstWhere((e) => e!.value == _cron.text, orElse: () => null)
        ?.key;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 16, 20, MediaQuery.viewInsetsOf(context).bottom + 20),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(widget.existing == null ? 'New routine' : 'Edit routine',
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 14),
          TextField(controller: _name, decoration: const InputDecoration(labelText: 'Name')),
          const SizedBox(height: 14),
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: true, label: Text('On a schedule'), icon: Icon(Icons.schedule)),
              ButtonSegment(value: false, label: Text('When a message arrives'), icon: Icon(Icons.mail_outline)),
            ],
            selected: {_onSchedule},
            onSelectionChanged: (s) => setState(() => _onSchedule = s.first),
          ),
          const SizedBox(height: 14),
          if (_onSchedule) ...[
            DropdownButtonFormField<String>(
              initialValue: preset,
              decoration: const InputDecoration(labelText: 'When'),
              items: [
                for (final e in _presets.entries) DropdownMenuItem(value: e.key, child: Text(e.key)),
                const DropdownMenuItem(value: null, child: Text('Custom (cron)')),
              ],
              onChanged: (k) => setState(() => _cron.text = k == null ? _cron.text : _presets[k]!),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _cron,
              decoration: const InputDecoration(
                  labelText: 'Cron', helperText: 'minute hour day month weekday'),
              onChanged: (_) => setState(() {}),
            ),
          ] else ...[
            DropdownButtonFormField<String>(
              initialValue: _provider,
              decoration: const InputDecoration(labelText: 'From'),
              items: const [
                DropdownMenuItem(value: 'gmail', child: Text('Gmail')),
                DropdownMenuItem(value: 'slack', child: Text('Slack')),
                DropdownMenuItem(value: '', child: Text('Any connection')),
              ],
              onChanged: (v) => setState(() => _provider = v ?? ''),
            ),
            const SizedBox(height: 10),
            TextField(
                controller: _from,
                decoration: const InputDecoration(labelText: 'Sender contains (optional)')),
            const SizedBox(height: 10),
            TextField(
                controller: _subject,
                decoration: const InputDecoration(labelText: 'Subject contains (optional)')),
          ],
          const SizedBox(height: 14),
          TextField(
            controller: _prompt,
            minLines: 2,
            maxLines: 6,
            decoration: const InputDecoration(
                labelText: 'What should Jarvis do?',
                hintText: 'Brief me on today: calendar, due, at risk, one first task.'),
          ),
          const SizedBox(height: 14),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'brief', label: Text('Just tell me'), icon: Icon(Icons.record_voice_over_outlined)),
              ButtonSegment(value: 'agent', label: Text('Do things'), icon: Icon(Icons.bolt)),
            ],
            selected: {_mode},
            onSelectionChanged: (s) => setState(() => _mode = s.first),
          ),
          const SizedBox(height: 14),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'app', label: Text('App'), icon: Icon(Icons.notifications_active_outlined)),
              ButtonSegment(value: 'telegram', label: Text('Telegram'), icon: Icon(Icons.send)),
              ButtonSegment(value: 'call', label: Text('Call me'), icon: Icon(Icons.call)),
            ],
            selected: {_channel},
            onSelectionChanged: (s) => setState(() => _channel = s.first),
          ),
          if (_channel == 'call')
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('Rings your phone and reads the answer aloud. Needs Twilio and a call '
                  'endpoint in Settings.', style: TextStyle(fontSize: 12)),
            ),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(_error!, style: const TextStyle(color: JarvisColors.danger)),
          ],
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _saving ? null : _save,
            child: Text(_saving ? 'Saving…' : 'Save'),
          ),
        ]),
      ),
    );
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'name': _name.text.trim().isEmpty ? 'Routine' : _name.text.trim(),
      'prompt': _prompt.text.trim(),
      'channel': _channel,
      'mode': _mode,
      'cron': _onSchedule ? _cron.text.trim() : null,
      'trigger': _onSchedule
          ? null
          : {
              if (_provider.isNotEmpty) 'provider': _provider,
              if (_from.text.trim().isNotEmpty) 'from': _from.text.trim(),
              if (_subject.text.trim().isNotEmpty) 'subject': _subject.text.trim(),
            },
    };
    try {
      final client = ref.read(clientProvider);
      if (widget.existing == null) {
        await client.createRoutine({...body, 'enabled': true});
      } else {
        await client.updateRoutine(widget.existing!['id'] as String, body);
      }
      if (mounted) Navigator.pop(context, true);
    } on ProblemException catch (e) {
      setState(() {
        _error = '$e';
        _saving = false;
      });
    }
  }
}
