import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';

/// Search everything JARVIS has captured — mail, deadlines, chat history, memories — in
/// one box (second-brain #26). Tap a result to open it.
class LifeSearch {
  static void open(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (_) => const _LifeSearchSheet(),
    );
  }
}

class _LifeSearchSheet extends ConsumerStatefulWidget {
  const _LifeSearchSheet();

  @override
  ConsumerState<_LifeSearchSheet> createState() => _LifeSearchSheetState();
}

class _LifeSearchSheetState extends ConsumerState<_LifeSearchSheet> {
  final _controller = TextEditingController();
  Timer? _debounce;
  List<Map<String, dynamic>> _results = const [];
  bool _busy = false;
  bool _asking = false;
  Map<String, dynamic>? _answer; // {answer, sources, grounded} from #23
  String _q = '';

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onChanged(String v) {
    _q = v.trim();
    _debounce?.cancel();
    if (_q.length < 2) {
      setState(() {
        _results = const [];
        _answer = null;
      });
      return;
    }
    setState(() => _answer = null); // a new query invalidates the old answer
    _debounce = Timer(const Duration(milliseconds: 280), _run);
  }

  Future<void> _ask() async {
    final q = _q;
    if (q.length < 3) return;
    setState(() => _asking = true);
    try {
      final r = await ref.read(clientProvider).askLife(q);
      if (mounted && q == _q) setState(() => _answer = r);
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _asking = false);
    }
  }

  Future<void> _run() async {
    final q = _q;
    setState(() => _busy = true);
    try {
      final r = await ref.read(clientProvider).lifeSearch(q);
      if (mounted && q == _q) setState(() => _results = r);
    } on ProblemException {
      // leave the last results
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  static const _icon = {
    'mail': Icons.mail_outline,
    'deadline': Icons.event_outlined,
    'chat': Icons.chat_bubble_outline,
    'memory': Icons.auto_awesome_outlined,
    'calendar_event': Icons.event_note_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.85,
      builder: (context, controller) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: TextField(
              controller: _controller,
              autofocus: true,
              onChanged: _onChanged,
              decoration: InputDecoration(
                hintText: 'Search everything — mail, deadlines, chats, memory',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: _busy
                    ? const Padding(
                        padding: EdgeInsets.all(12),
                        child: SizedBox(
                            width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)))
                    : null,
              ),
            ),
          ),
          if (_q.length >= 3)
            Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(12, 0, 12, 4),
                child: TextButton.icon(
                  onPressed: _asking ? null : _ask,
                  icon: _asking
                      ? const SizedBox(
                          width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.auto_awesome, size: 18),
                  label: Text(_asking ? 'Thinking…' : 'Ask JARVIS about "$_q"'),
                ),
              ),
            ),
          if (_answer != null) _AnswerCard(answer: _answer!),
          Expanded(
            child: _q.length < 2
                ? Center(
                    child: Text('Type to search your whole world',
                        style: Theme.of(context).textTheme.bodySmall))
                : _results.isEmpty && !_busy
                    ? Center(
                        child: Text('Nothing found for "$_q"',
                            style: Theme.of(context).textTheme.bodySmall))
                    : ListView.builder(
                        controller: controller,
                        itemCount: _results.length,
                        itemBuilder: (context, i) {
                          final r = _results[i];
                          final when = r['when'] != null
                              ? DateTime.tryParse(r['when'] as String)?.toLocal()
                              : null;
                          return ListTile(
                            leading: Icon(_icon[r['type']] ?? Icons.circle_outlined,
                                color: scheme.primary),
                            title: Text(r['title'] as String? ?? '',
                                maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text(
                              [
                                if ((r['snippet'] as String? ?? '').isNotEmpty) r['snippet'],
                                if (when != null) _ago(when),
                              ].join('  ·  '),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            onTap: () {
                              final tab = _routeToTab[r['route']];
                              Navigator.pop(context);
                              if (tab != null) {
                                ref.read(homeTabProvider.notifier).state = tab;
                              }
                            },
                          );
                        },
                      ),
          ),
        ]),
      ),
    );
  }

  static const _routeToTab = {
    'insights': 10,
    'goals': 3,
    'jarvis': 0,
    'timeline': 5,
    'settings': 11,
  };

  static String _ago(DateTime t) {
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 60) return '${d.inMinutes}m ago';
    if (d.inHours < 24) return '${d.inHours}h ago';
    if (d.inDays < 30) return '${d.inDays}d ago';
    return '${(d.inDays / 30).floor()}mo ago';
  }
}

/// The sourced answer for #23 (instant contextual recall): a short synthesised answer over
/// everything captured, with the sources it drew from. If no model answered, we show the
/// sources alone — still a useful result.
class _AnswerCard extends StatelessWidget {
  const _AnswerCard({required this.answer});
  final Map<String, dynamic> answer;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = answer['answer'] as String?;
    final sources = (answer['sources'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      color: scheme.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.auto_awesome, size: 18, color: scheme.onSecondaryContainer),
            const SizedBox(width: 8),
            Text('Answer', style: Theme.of(context).textTheme.titleSmall),
          ]),
          const SizedBox(height: 6),
          Text(
            text ?? "No synthesised answer right now — here's what I found:",
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          if (sources.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('Sources', style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 4),
            for (final s in sources)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Text(
                  '[${s['n']}] ${s['title'] ?? ''}'
                  '${s['who'] != null ? ' · ${s['who']}' : ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.labelSmall,
                ),
              ),
          ],
        ]),
      ),
    );
  }
}
