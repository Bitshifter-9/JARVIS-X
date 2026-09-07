import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../widgets/orb.dart';
import 'interview.dart';

/// Train Jarvis: what it knows about you, in your words; how you write, from your
/// own mail; what it got wrong, from your thumbs. All of it is shown to the model on
/// every turn — this is the difference between an assistant and *your* assistant.
class TrainScreen extends ConsumerStatefulWidget {
  const TrainScreen({super.key});

  @override
  ConsumerState<TrainScreen> createState() => _TrainScreenState();
}

class _TrainScreenState extends ConsumerState<TrainScreen> {
  final _fields = {
    'about': TextEditingController(),
    'priorities': TextEditingController(),
    'people': TextEditingController(),
    'style': TextEditingController(),
    'decisions': TextEditingController(),
  };
  static const _labels = {
    'about': ('About you', 'Who you are, what you are working on, where, when your day runs.'),
    'priorities': ('What matters right now', 'The two or three things everything else bends around.'),
    'people': ('People', 'Names, roles, how to refer to them. "Amma = my mother, Telugu."'),
    'style': ('How you want replies', 'Length, tone, language, what to never do.'),
    'decisions': ('How you decide', 'What you say yes to, what you avoid, what you need before acting.'),
  };
  String _learnedStyle = '';
  Map<String, dynamic>? _stats;
  bool _loading = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final client = ref.read(clientProvider);
      final p = await client.profile();
      final stats = await client.feedbackStats();
      if (!mounted) return;
      setState(() {
        for (final e in _fields.entries) {
          e.value.text = (p[e.key] as String?) ?? '';
        }
        _learnedStyle = (p['learned_style'] as String?) ?? '';
        _stats = stats;
        _loading = false;
      });
    } on ProblemException catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }

  @override
  void dispose() {
    for (final c in _fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _busy = true);
    try {
      await ref.read(clientProvider).updateProfile({
        for (final e in _fields.entries) e.key: e.value.text.trim(),
      });
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('Saved — Jarvis sees this on every turn')));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _learn() async {
    setState(() => _busy = true);
    try {
      final r = await ref.read(clientProvider).learnStyle();
      if (!mounted) return;
      setState(() => _learnedStyle = (r['learned_style'] as String?) ?? '');
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Learned from ${r['samples']} of your sent emails')));
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resolve(String id, {required bool accept}) async {
    try {
      final result = await ref.read(clientProvider).resolveSuggestion(id, accept: accept);
      if (!mounted) return;
      setState(() {
        for (final e in _fields.entries) {
          e.value.text = (result[e.key] as String?) ?? e.value.text;
        }
      });
      ref.invalidate(suggestionsProvider);
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _interview() async {
    final result = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(builder: (_) => const InterviewScreen()),
    );
    if (result == null || !mounted) return;
    setState(() {
      for (final e in _fields.entries) {
        e.value.text = (result[e.key] as String?) ?? e.value.text;
      }
      _learnedStyle = (result['learned_style'] as String?) ?? _learnedStyle;
    });
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(result['distilled'] == true
            ? 'Profile written from your answers'
            : 'Saved your answers as they are — the model was unreachable')));
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    final scheme = Theme.of(context).colorScheme;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 40),
      children: [
        Row(children: [
          const JarvisOrb(state: OrbState.thinking, size: 44),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              'Everything here is shown to Jarvis on every turn. Facts it learns on its own '
              'live under Settings → What Jarvis remembers.',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: scheme.onSurface.withValues(alpha: 0.7)),
            ),
          ),
        ]).animate().fadeIn(),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: _busy ? null : _interview,
          icon: const Icon(Icons.timer_outlined),
          label: const Text('Set up in 3 minutes — six questions, Jarvis writes the rest'),
        ),
        const SizedBox(height: 16),
        for (final (i, e) in _fields.entries.indexed)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TextField(
              controller: e.value,
              maxLines: null,
              minLines: 2,
              decoration: InputDecoration(
                labelText: _labels[e.key]!.$1,
                helperText: _labels[e.key]!.$2,
                helperMaxLines: 2,
                alignLabelWithHint: true,
              ),
            ),
          ).animate(delay: (60 * i).ms).fadeIn().slideY(begin: 0.05),
        FilledButton.icon(
          onPressed: _busy ? null : _save,
          icon: const Icon(Icons.save_outlined),
          label: Text(_busy ? 'Saving…' : 'Save'),
        ),
        const SizedBox(height: 28),
        Consumer(builder: (context, ref, _) {
          final pending = ref.watch(suggestionsProvider).valueOrNull ?? const [];
          if (pending.isEmpty) return const SizedBox.shrink();
          return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('What Jarvis noticed', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 6),
            Text(
              'Distilled each night from your day. Nothing changes until you accept it.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            for (final s in pending)
              Card(
                margin: const EdgeInsets.only(bottom: 8),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(14, 12, 8, 6),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(s['text'] as String),
                    if ((s['because'] as String? ?? '').isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text('Because: ${s['because']} · goes under ${s['section']}',
                            style: Theme.of(context).textTheme.labelSmall),
                      ),
                    Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                      TextButton(
                        onPressed: () => _resolve(s['id'] as String, accept: false),
                        child: const Text('Dismiss'),
                      ),
                      FilledButton.tonal(
                        onPressed: () => _resolve(s['id'] as String, accept: true),
                        child: const Text('Accept'),
                      ),
                    ]),
                  ]),
                ),
              ),
            const SizedBox(height: 20),
          ]);
        }),
        Text('How you write', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 6),
        Text(
          'With your click, Jarvis reads your recent sent mail once and writes a style '
          'card — tone, length, sign-off, the phrases you use — so drafts read like you.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 8),
        if (_learnedStyle.isNotEmpty)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Text(_learnedStyle),
            ),
          ),
        OutlinedButton.icon(
          onPressed: _busy ? null : _learn,
          icon: const Icon(Icons.auto_fix_high),
          label: Text(_learnedStyle.isEmpty ? 'Learn my writing style' : 'Learn again'),
        ),
        const SizedBox(height: 28),
        Text('What it has learned from your feedback', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 6),
        Text(
          'Long-press a reply → 👍 / 👎. A 👎 with a reason becomes a rule it is reminded of '
          'next time. Or just tell it in chat: "From now on…", "Never…", "Remember that…".',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 8),
        if (_stats != null)
          Card(
            margin: EdgeInsets.zero,
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('👍 ${_stats!['up']}   👎 ${_stats!['down']}',
                    style: Theme.of(context).textTheme.titleMedium),
                for (final l in (_stats!['lessons'] as List<dynamic>).cast<Map<String, dynamic>>())
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text('${l['score'] > 0 ? '👍' : '👎'} ${l['note']}'),
                  ),
                if ((_stats!['lessons'] as List).isEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text('No lessons yet.',
                        style: TextStyle(color: scheme.onSurface.withValues(alpha: 0.6))),
                  ),
              ]),
            ),
          ),
      ],
    );
  }
}
