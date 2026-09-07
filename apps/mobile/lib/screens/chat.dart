import 'dart:async';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:gpt_markdown/gpt_markdown.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:image_picker/image_picker.dart';
import 'package:speech_to_text/speech_to_text.dart';
import 'package:window_manager/window_manager.dart';

import '../api/models.dart';
import '../main.dart' show isDesktop;
import '../state/providers.dart';
import '../theme.dart';
import '../widgets/ambient.dart';
import '../widgets/orb.dart';
import 'life_search.dart';
import 'voice_mode.dart';
import '../live/live_activity.dart';
import '../voice/voice_prefs.dart';

/// Talk to Jarvis. Three ways in: type, hold the mic, or enable the wake word and
/// just say "Jarvis, …". Replies are spoken; state shows in the island up top.
///
/// History lives on the server, so the Mac and the phone share one conversation.
/// Voice runs on-device — nothing is recorded off the machine.
class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

enum _JarvisState { idle, listening, thinking, speaking }

class _Turn {
  _Turn(this.role, this.content, {this.meta = const {}, this.streaming = false});
  final String role;
  String content;
  Map<String, dynamic> meta;
  bool streaming;
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _turns = <_Turn>[];
  final _tts = FlutterTts();
  final _stt = SpeechToText();
  final _player = AudioPlayer();

  bool _busy = false;
  bool _listening = false;
  bool _speaking = false;
  bool _speak = true;
  bool _sttReady = false;
  bool _wakeMode = false;
  bool _awaitingCommand = false;
  bool _pinned = false;
  Size? _unpinnedSize;
  String? _conversationId;
  String _conversationTitle = 'New chat';
  String _provider = 'auto';
  String _persona = 'jarvis';
  StreamSubscription<Map<String, dynamic>>? _stream;
  static const _providers = {
    'auto': 'Auto (fastest free)',
    'gemini': 'Gemini 3.6 Flash',
    'groq': 'GPT-OSS 120B · Groq',
    'openrouter_free': 'OpenRouter free',
    'openrouter_paid': 'Claude Haiku · paid',
    'ollama': 'Ollama on the Mac',
  };

  _JarvisState get _state {
    if (_speaking) return _JarvisState.speaking;
    if (_busy) return _JarvisState.thinking;
    if (_listening) return _JarvisState.listening;
    return _JarvisState.idle;
  }

  /// Mirror Jarvis's live status into an Android live notification (dynamic-island style).
  void _syncLive() {
    switch (_state) {
      case _JarvisState.thinking:
        LiveActivity.show('JARVIS X', 'Working on it…');
      case _JarvisState.speaking:
        LiveActivity.show('JARVIS X', 'Speaking');
      case _JarvisState.listening:
        LiveActivity.show('JARVIS X', 'Listening…');
      case _JarvisState.idle:
        LiveActivity.hide();
    }
  }

  @override
  void initState() {
    super.initState();
    _tts.setStartHandler(() => setState(() => _speaking = true));
    _tts.setCompletionHandler(() => setState(() => _speaking = false));
    _tts.setCancelHandler(() => setState(() => _speaking = false));
    _player.onPlayerComplete.listen((_) {
      if (mounted) setState(() => _speaking = false);
    });
    _stt.initialize(onError: (_) {
      if (mounted) setState(() => _listening = false);
    }).then((ok) => mounted ? setState(() => _sttReady = ok) : null);
    _loadHistory();
  }

  Future<void> _loadHistory([String? conversationId]) async {
    try {
      final rows = await ref.read(clientProvider).conversationHistory(conversationId);
      if (!mounted) return;
      setState(() {
        _turns
          ..clear()
          ..addAll(rows.map((r) => _Turn(
                r['role'] as String,
                r['content'] as String,
                meta: (r['meta'] as Map<String, dynamic>?) ?? const {},
              )));
        if (rows.isNotEmpty) {
          _conversationId = rows.last['conversation_id'] as String?;
        }
      });
      if (_conversationId != null) {
        final all = await ref.read(clientProvider).conversations();
        final mine = all.where((c) => c['id'] == _conversationId);
        if (mine.isNotEmpty && mounted) {
          setState(() {
            _conversationTitle = mine.first['title'] as String;
            _persona = (mine.first['persona'] as String?) ?? 'jarvis';
          });
        }
      }
      _autoScroll();
    } on ProblemException {
      // First run or offline — an empty conversation is fine.
    }
  }

  Future<void> _newChat() async {
    _stream?.cancel();
    try {
      final c = await ref.read(clientProvider).createConversation();
      if (!mounted) return;
      setState(() {
        _turns.clear();
        _conversationId = c['id'] as String;
        _conversationTitle = c['title'] as String;
        _persona = 'jarvis';
        _busy = false;
      });
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _openConversations() async {
    final client = ref.read(clientProvider);
    List<Map<String, dynamic>> list;
    try {
      list = await client.conversations();
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      return;
    }
    if (!mounted) return;
    final search = TextEditingController();
    final picked = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      builder: (context) => StatefulBuilder(
        builder: (context, setSheet) {
          final q = search.text.trim().toLowerCase();
          final shown = q.isEmpty
              ? list
              : list
                  .where((c) => (c['title'] as String).toLowerCase().contains(q))
                  .toList();
          return DraggableScrollableSheet(
            expand: false,
            initialChildSize: 0.7,
            builder: (context, controller) => Column(children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: TextField(
                  controller: search,
                  onChanged: (_) => setSheet(() {}),
                  decoration: InputDecoration(
                    hintText: 'Search chats',
                    prefixIcon: const Icon(Icons.search),
                    isDense: true,
                    suffixIcon: q.isEmpty
                        ? null
                        : IconButton(
                            icon: const Icon(Icons.clear),
                            onPressed: () => setSheet(() => search.clear()),
                          ),
                  ),
                ),
              ),
              Expanded(
                child: ListView(
                  controller: controller,
                  padding: const EdgeInsets.fromLTRB(8, 0, 8, 24),
                  children: [
            ListTile(
              leading: const Icon(Icons.add_comment_outlined),
              title: const Text('New chat'),
              onTap: () => Navigator.pop(context, '__new__'),
            ),
            const Divider(),
            for (final c in shown)
              ListTile(
                leading: Icon(
                  c['pinned'] == true
                      ? Icons.push_pin
                      : (c['id'] == _conversationId
                          ? Icons.chat_bubble
                          : Icons.chat_bubble_outline),
                  size: 20,
                ),
                title: Text(c['title'] as String, maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text(_ago(DateTime.parse(c['last_message_at'] as String).toLocal())),
                trailing: PopupMenuButton<String>(
                  onSelected: (v) async {
                    if (v == 'pin') {
                      await client.pinConversation(
                          c['id'] as String, c['pinned'] != true);
                    } else if (v == 'rename') {
                      final title = await _promptTitle(context, c['title'] as String);
                      if (title != null && title.isNotEmpty) {
                        await client.renameConversation(c['id'] as String, title);
                        if (c['id'] == _conversationId && mounted) {
                          setState(() => _conversationTitle = title);
                        }
                      }
                    } else if (v == 'export') {
                      final r = await client.exportConversation(c['id'] as String);
                      await Clipboard.setData(
                          ClipboardData(text: r['markdown'] as String? ?? ''));
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                            content: Text('Exported — copied, and saved to Documents')));
                      }
                    } else if (v == 'delete') {
                      await client.deleteConversation(c['id'] as String);
                      if (c['id'] == _conversationId && mounted) {
                        setState(() {
                          _turns.clear();
                          _conversationId = null;
                          _conversationTitle = 'New chat';
                        });
                      }
                    }
                    if (context.mounted) Navigator.pop(context);
                  },
                  itemBuilder: (context) => [
                    PopupMenuItem(
                        value: 'pin',
                        child: Text(c['pinned'] == true ? 'Unpin' : 'Pin')),
                    const PopupMenuItem(value: 'rename', child: Text('Rename')),
                    const PopupMenuItem(value: 'export', child: Text('Export')),
                    const PopupMenuItem(value: 'delete', child: Text('Delete')),
                  ],
                ),
                onTap: () => Navigator.pop(context, c['id'] as String),
              ),
            if (shown.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: Text('No chats match')),
              ),
                  ],
                ),
              ),
            ]),
          );
        },
      ),
    );
    if (picked == null || !mounted) return;
    if (picked == '__new__') {
      await _newChat();
    } else {
      _stream?.cancel();
      setState(() {
        _conversationId = picked;
        _busy = false;
      });
      await _loadHistory(picked);
    }
  }

  static String _ago(DateTime t) {
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 1) return 'just now';
    if (d.inHours < 1) return '${d.inMinutes} min ago';
    if (d.inDays < 1) return '${d.inHours} h ago';
    return '${d.inDays} d ago';
  }

  Future<String?> _promptTitle(BuildContext context, String current) {
    final controller = TextEditingController(text: current);
    return showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Rename chat'),
        content: TextField(controller: controller, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, controller.text.trim()),
              child: const Text('Save')),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _stream?.cancel();
    _wakeMode = false;
    _input.dispose();
    _scroll.dispose();
    _tts.stop();
    _player.dispose();
    _stt.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    WidgetsBinding.instance.addPostFrameCallback((_) => _syncLive());
    ref.listen(chatPrefillProvider, (_, next) {
      if (next != null) WidgetsBinding.instance.addPostFrameCallback((_) => _takePrefill());
    });

    return AmbientBackground(
      intensity: _turns.isEmpty ? 1.0 : 0.5,
      child: Stack(children: [
        Column(children: [
          const SizedBox(height: 52),
          _threadBar(context),
          Expanded(child: _messages(context)),
          _composer(context),
        ]),
        Align(alignment: Alignment.topCenter, child: _island(context)),
      ]),
    );
  }

  // ── the thread: title, history, new chat, voice mode ───────────────
  Widget _threadBar(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 4, 8, 0),
      child: Row(children: [
        Expanded(
          child: InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: _openConversations,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Flexible(
                  child: Text(_conversationTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.titleMedium),
                ),
                Icon(Icons.expand_more, size: 18, color: scheme.onSurface.withValues(alpha: 0.6)),
              ]),
            ),
          ),
        ),
        IconButton(
          tooltip: 'Search everything',
          icon: const Icon(Icons.search),
          onPressed: () => LifeSearch.open(context),
        ),
        IconButton(
          tooltip: 'Voice conversation',
          icon: const Icon(Icons.record_voice_over_outlined),
          onPressed: () => Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => VoiceModeScreen(conversationId: _conversationId))),
        ),
        IconButton(
          tooltip: 'New chat',
          icon: const Icon(Icons.add_comment_outlined),
          onPressed: _newChat,
        ),
      ]),
    );
  }

  OrbState get _orbState => switch (_state) {
        _JarvisState.listening => OrbState.listening,
        _JarvisState.thinking => OrbState.thinking,
        _JarvisState.speaking => OrbState.speaking,
        _JarvisState.idle => OrbState.idle,
      };

  // ── the island: Jarvis's state, always visible ─────────────────────
  Widget _island(BuildContext context) {
    final label = switch (_state) {
      _JarvisState.listening => 'Listening…',
      _JarvisState.thinking => 'Thinking…',
      _JarvisState.speaking => 'Speaking',
      _JarvisState.idle => _wakeMode ? 'Say "Jarvis"' : 'Idle',
    };
    final active = _state != _JarvisState.idle;
    final scheme = Theme.of(context).colorScheme;
    return AnimatedContainer(
      duration: const Duration(milliseconds: 260),
      curve: Curves.easeOutCubic,
      margin: const EdgeInsets.only(top: 8),
      padding: EdgeInsets.symmetric(horizontal: active ? 18 : 14, vertical: 7),
      decoration: BoxDecoration(
        color: context.surfaces.surface3.withValues(alpha: 0.92),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: scheme.primary.withValues(alpha: active ? 0.5 : 0.15)),
        boxShadow: [
          if (active)
            BoxShadow(color: scheme.primary.withValues(alpha: 0.25), blurRadius: 18, spreadRadius: 1),
        ],
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        JarvisOrb(state: _orbState, size: 22),
        const SizedBox(width: 10),
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 200),
          child: Text(label,
              key: ValueKey(label),
              style: Theme.of(context).textTheme.labelLarge?.copyWith(color: scheme.onSurface)),
        ),
      ]),
    );
  }

  /// Text handed over from Home's ask-bar is sent as soon as this screen shows it.
  void _takePrefill() {
    final text = ref.read(chatPrefillProvider);
    if (text == null || text.isEmpty || _busy) return;
    ref.read(chatPrefillProvider.notifier).state = null;
    _sendText(text);
  }

  Widget _messages(BuildContext context) {
    if (_turns.isEmpty && !_busy) {
      final scheme = Theme.of(context).colorScheme;
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          JarvisOrb(state: _orbState, size: 132)
              .animate()
              .scale(duration: 700.ms, curve: Curves.easeOutBack),
          const SizedBox(height: 22),
          Text('How can I help?', style: Theme.of(context).textTheme.headlineSmall)
              .animate()
              .fadeIn(delay: 200.ms),
          const SizedBox(height: 8),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 40),
            child: Text(
              'Hold the mic, type below, or turn on the wake word and say\n'
              '"Jarvis, what\'s due today?"',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: scheme.onSurface.withValues(alpha: 0.65)),
            ),
          ).animate().fadeIn(delay: 320.ms),
          const SizedBox(height: 20),
          Wrap(spacing: 8, runSpacing: 8, alignment: WrapAlignment.center, children: [
            for (final s in const [
              "What's due today?",
              'Am I on track?',
              'Screenshot my Mac',
              'Start a focus session',
            ])
              ActionChip(label: Text(s), onPressed: _busy ? null : () => _sendText(s)),
          ]).animate().fadeIn(delay: 420.ms).slideY(begin: 0.15),
        ]),
      );
    }
    final count = _turns.length + (_busy ? 1 : 0);
    return ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.all(12),
      itemCount: count,
      itemBuilder: (context, i) {
        if (_busy && i == count - 1) return const _ThinkingBubble();
        final t = _turns[i];
        final mine = t.role == 'user';
        final scheme = Theme.of(context).colorScheme;
        final action = t.meta['action'] as Map<String, dynamic>?;
        return Column(
          crossAxisAlignment: mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            GestureDetector(
              onLongPress: () => _turnActions(context, i),
              child: mine
                  // You: a calm, subtle bubble, right-aligned (Claude-style).
                  ? Align(
                      alignment: Alignment.centerRight,
                      child: Container(
                        margin: const EdgeInsets.only(top: 10, bottom: 10, left: 44),
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
                        decoration: BoxDecoration(
                          color: context.surfaces.surface3,
                          borderRadius: const BorderRadius.only(
                            topLeft: Radius.circular(20),
                            topRight: Radius.circular(20),
                            bottomLeft: Radius.circular(20),
                            bottomRight: Radius.circular(6),
                          ),
                        ),
                        child: SelectableText(t.content,
                            style: TextStyle(color: scheme.onSurface, height: 1.35)),
                      ),
                    )
                  // Jarvis: full-width text on the page, a small mark above it.
                  : Padding(
                      padding: const EdgeInsets.only(top: 8, bottom: 2),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Row(children: [
                          JarvisOrb(
                              state: t.streaming ? OrbState.thinking : OrbState.idle, size: 18),
                          const SizedBox(width: 8),
                          Text('Jarvis',
                              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                                    color: scheme.onSurface.withValues(alpha: 0.45),
                                    fontWeight: FontWeight.w600,
                                    letterSpacing: 0.3,
                                  )),
                        ]),
                        const SizedBox(height: 8),
                        t.content.isEmpty && t.streaming
                            ? const _ThinkingDots()
                            : GptMarkdown(
                                t.content,
                                style: TextStyle(color: scheme.onSurface, height: 1.55),
                              ),
                      ]),
                    ),
            ),
            if (!mine && action != null && action['run_id'] != null)
              Padding(
                padding: const EdgeInsets.only(left: 2, bottom: 6),
                child: _StepsCard(runId: action['run_id'] as String, status: '${action['status']}'),
              ),
            if (!mine && !t.streaming && t.content.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 2),
                child: _Thumbs(turn: t),
              ),
            if (!mine && (t.meta['searched'] as String?)?.isNotEmpty == true)
              Padding(
                padding: const EdgeInsets.only(left: 2, bottom: 6),
                child: Text('Searched the web: ${t.meta['searched']}',
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: scheme.onSurface.withValues(alpha: 0.55))),
              ),
          ],
        ).animate().fadeIn(duration: 220.ms).slideY(begin: 0.08, curve: Curves.easeOut);
      },
    );
  }

  Widget _composer(BuildContext context) {
    return SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        _suggestionChips(context),
        Padding(
        padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
        child: Row(children: [
          if (isDesktop)
            IconButton(
              tooltip: _pinned
                  ? 'Unpin: back to the full app'
                  : 'Pin Jarvis as a floating assistant on screen',
              icon: Icon(_pinned ? Icons.push_pin : Icons.push_pin_outlined,
                  color: _pinned ? Theme.of(context).colorScheme.primary : null),
              onPressed: _togglePin,
            ),
          IconButton(
            tooltip: _wakeMode
                ? 'Wake word on — say "Jarvis"'
                : 'Enable wake word',
            icon: Icon(_wakeMode ? Icons.hearing : Icons.hearing_disabled,
                color: _wakeMode ? Theme.of(context).colorScheme.primary : null),
            onPressed: _sttReady ? _toggleWake : null,
          ),
          IconButton(
            tooltip: _speak ? 'Replies are spoken' : 'Replies are silent',
            icon: Icon(_speak ? Icons.volume_up : Icons.volume_off),
            onPressed: () => setState(() => _speak = !_speak),
          ),
          Consumer(builder: (context, ref, _) {
            final personas = ref.watch(personasProvider).valueOrNull ?? const [];
            final current = personas.cast<Map<String, dynamic>?>().firstWhere(
                  (p) => p!['key'] == _persona,
                  orElse: () => null,
                );
            return PopupMenuButton<String>(
              tooltip: 'Persona: ${current?['name'] ?? 'Jarvis'}',
              icon: Icon(Icons.face_retouching_natural,
                  color: _persona == 'jarvis' ? null : Theme.of(context).colorScheme.primary),
              initialValue: _persona,
              onSelected: (v) => setState(() => _persona = v),
              itemBuilder: (context) => _groupedPersonaItems(context, personas),
            );
          }),
          PopupMenuButton<String>(
            tooltip: 'Model: ${_providers[_provider]}',
            icon: Icon(Icons.auto_awesome_outlined,
                color: _provider == 'auto' ? null : Theme.of(context).colorScheme.primary),
            initialValue: _provider,
            onSelected: (v) => setState(() => _provider = v),
            itemBuilder: (context) => [
              for (final e in _providers.entries)
                PopupMenuItem(value: e.key, child: Text(e.value)),
            ],
          ),
          IconButton(
            tooltip: 'Attach image',
            icon: const Icon(Icons.add_photo_alternate_outlined),
            onPressed: _busy ? null : _attachImage,
          ),
          Expanded(
            child: TextField(
              controller: _input,
              textInputAction: TextInputAction.send,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                hintText: _listening
                    ? 'Listening…'
                    : 'Ask Jarvis…  (try /brief, /due, /screenshot)',
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
              ),
              onSubmitted: (_) => _send(),
            ),
          ),
          const SizedBox(width: 8),
          GestureDetector(
            onLongPressStart: (_) => _startListening(),
            onLongPressEnd: (_) => _stopListening(),
            child: FloatingActionButton.small(
              heroTag: 'chat-mic',
              tooltip: _busy
                  ? 'Stop'
                  : (_sttReady ? 'Hold to talk' : 'Voice unavailable — type instead'),
              backgroundColor: _busy
                  ? Theme.of(context).colorScheme.errorContainer
                  : (_listening ? Theme.of(context).colorScheme.error : null),
              onPressed: _busy ? _stop : _send,
              child: Icon(_busy
                  ? Icons.stop
                  : _listening
                      ? Icons.mic
                      : (_input.text.isEmpty && _sttReady ? Icons.mic_none : Icons.send)),
            ),
          ),
        ]),
        ),
      ]),
    );
  }

  /// Follow-up chips after the last answer (FEATURES-50 #15). Tapping one sends it.
  Widget _suggestionChips(BuildContext context) {
    if (_busy || _turns.isEmpty || _turns.last.role != 'assistant' || _turns.last.streaming) {
      return const SizedBox.shrink();
    }
    final chips = followUps(_turns.last.content);
    if (chips.isEmpty) return const SizedBox.shrink();
    return SizedBox(
      height: 40,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        children: [
          for (final c in chips)
            Padding(
              padding: const EdgeInsets.only(right: 6),
              child: ActionChip(
                label: Text(c),
                visualDensity: VisualDensity.compact,
                onPressed: () {
                  _input.text = c;
                  _send();
                },
              ),
            ),
        ],
      ),
    );
  }

  // ── pinned assistant (desktop): small, always on top, wake word armed ──
  Future<void> _togglePin() async {
    _pinned = !_pinned;
    if (_pinned) {
      _unpinnedSize = await windowManager.getSize();
      await windowManager.setAlwaysOnTop(true);
      await windowManager.setSize(const Size(400, 300), animate: true);
      if (_sttReady && !_wakeMode) _toggleWake();
    } else {
      await windowManager.setAlwaysOnTop(false);
      await windowManager.setSize(_unpinnedSize ?? const Size(1100, 780),
          animate: true);
    }
    if (mounted) setState(() {});
  }

  // ── wake word ──────────────────────────────────────────────────────
  void _toggleWake() {
    setState(() => _wakeMode = !_wakeMode);
    if (_wakeMode) {
      _wakeLoop();
    } else {
      _stt.stop();
    }
  }

  Future<void> _wakeLoop() async {
    while (_wakeMode && mounted) {
      // Never listen while Jarvis is talking or working — it would hear itself.
      if (_busy || _speaking) {
        await Future.delayed(const Duration(milliseconds: 400));
        continue;
      }
      final heard = Completer<String>();
      setState(() => _listening = true);
      await _stt.listen(
        onResult: (r) {
          if (r.finalResult && !heard.isCompleted) {
            heard.complete(r.recognizedWords);
          }
        },
      );
      String words;
      try {
        words = await heard.future.timeout(const Duration(seconds: 25));
      } on TimeoutException {
        words = '';
      }
      await _stt.stop();
      if (mounted) setState(() => _listening = false);
      if (!_wakeMode || !mounted) break;
      if (words.trim().isNotEmpty) await _handleWakeUtterance(words.trim());
      await Future.delayed(const Duration(milliseconds: 300));
    }
  }

  Future<void> _handleWakeUtterance(String words) async {
    if (_awaitingCommand) {
      _awaitingCommand = false;
      await _sendText(words);
      return;
    }
    final lower = words.toLowerCase();
    final idx = lower.indexOf('jarvis');
    if (idx == -1) return; // ambient speech, not for us
    final rest = words
        .substring(idx + 'jarvis'.length)
        .replaceFirst(RegExp(r'^[\s,.!?]+'), '')
        .trim();
    if (rest.isEmpty) {
      _awaitingCommand = true;
      if (_speak) await _say('Yes?');
    } else {
      await _sendText(rest);
    }
  }

  // ── hold-to-talk ───────────────────────────────────────────────────
  Future<void> _startListening() async {
    if (!_sttReady || _busy || _wakeMode) return;
    setState(() => _listening = true);
    await _stt.listen(
      onResult: (r) => setState(() => _input.text = r.recognizedWords),
    );
  }

  Future<void> _stopListening() async {
    if (_wakeMode) return;
    await _stt.stop();
    setState(() => _listening = false);
    if (_input.text.trim().isNotEmpty) await _send();
  }

  Future<void> _turnActions(BuildContext context, int index) async {
    final t = _turns[index];
    HapticFeedback.selectionClick();
    final choice = await showModalBottomSheet<String>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
              leading: const Icon(Icons.copy),
              title: const Text('Copy'),
              onTap: () => Navigator.pop(context, 'copy')),
          if (t.role == 'assistant')
            ListTile(
                leading: const Icon(Icons.volume_up_outlined),
                title: const Text('Read aloud'),
                onTap: () => Navigator.pop(context, 'speak')),
          if (t.role == 'assistant' && index > 0)
            ListTile(
                leading: const Icon(Icons.refresh),
                title: const Text('Regenerate'),
                onTap: () => Navigator.pop(context, 'regenerate')),
          if (t.role == 'user')
            ListTile(
                leading: const Icon(Icons.edit_outlined),
                title: const Text('Edit and resend'),
                onTap: () => Navigator.pop(context, 'edit')),
        ]),
      ),
    );
    if (!mounted || choice == null) return;
    switch (choice) {
      case 'copy':
        await Clipboard.setData(ClipboardData(text: t.content));
        if (mounted) {
          ScaffoldMessenger.of(this.context)
              .showSnackBar(const SnackBar(content: Text('Copied')));
        }
      case 'speak':
        await _say(t.content);
      case 'regenerate':
        final previous = _turns.sublist(0, index).lastWhere((x) => x.role == 'user');
        setState(() => _turns.removeRange(index, _turns.length));
        await _sendText(previous.content, resend: true);
      case 'edit':
        _input.text = t.content;
    }
  }

  void _stop() {
    _stream?.cancel();
    setState(() {
      _busy = false;
      for (final t in _turns) {
        t.streaming = false;
      }
    });
  }

  // ── sending ────────────────────────────────────────────────────────
  /// Slash-commands: shortcuts that expand to a natural request the agent understands.
  static const _slash = {
    '/brief': 'Give me my briefing now',
    '/due': 'What is due today and this week?',
    '/focus': 'What is the one thing I should do now?',
    '/screenshot': 'Take a screenshot of my Mac',
    '/ring': 'Ring my phone',
    '/locate': 'Where is my phone?',
    '/week': 'Give me my weekly review',
  };

  Future<void> _attachImage() async {
    final XFile? file = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 1600,
      imageQuality: 85,
    );
    if (file == null) return;
    final bytes = await file.readAsBytes();
    final question = _input.text.trim();
    _input.clear();
    setState(() {
      _turns.add(_Turn('user', question.isEmpty ? '📷 (image)' : '📷 $question'));
      _busy = true;
    });
    _autoScroll();
    try {
      final text = await ref
          .read(clientProvider)
          .describeImage(bytes, file.name, question: question.isEmpty ? null : question);
      if (!mounted) return;
      setState(() {
        _turns.add(_Turn('assistant', text));
        _busy = false;
      });
      _autoScroll();
      if (_speak) await _say(text);
    } on ProblemException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }

  Future<void> _send() async {
    var text = _input.text.trim();
    if (text.isEmpty || _busy) return;
    if (text.startsWith('/')) {
      final cmd = text.split(' ').first.toLowerCase();
      if (_slash.containsKey(cmd)) {
        final rest = text.substring(cmd.length).trim();
        text = rest.isEmpty ? _slash[cmd]! : '${_slash[cmd]!} $rest';
      }
    }
    _input.clear();
    await _sendText(text);
  }

  Future<void> _sendText(String text, {bool resend = false}) async {
    _stream?.cancel();
    setState(() {
      _busy = true;
      if (!resend) _turns.add(_Turn('user', text));
      _turns.add(_Turn('assistant', '', streaming: true));
    });
    _autoScroll();
    final reply = _turns.last;

    final recent = _turns.where((t) => !t.streaming).toList();
    final window = recent.length <= 12 ? recent : recent.sublist(recent.length - 12);
    final done = Completer<void>();
    _stream = ref
        .read(clientProvider)
        .chatStream(
          [for (final t in window) {'role': t.role, 'content': t.content}],
          conversationId: _conversationId,
          provider: _provider,
          persona: _persona,
        )
        .listen((event) async {
      if (!mounted) return;
      switch (event['type']) {
        case 'delta':
          setState(() => reply.content += event['text'] as String);
          _autoScroll();
        case 'final':
          setState(() {
            reply.content = (event['text'] as String?) ?? reply.content;
            reply.meta = {
              'provider': event['provider'],
              'searched': event['searched'],
              'action': event['action'],
            };
            reply.streaming = false;
            _conversationId = (event['conversation_id'] as String?) ?? _conversationId;
            _conversationTitle =
                (event['conversation_title'] as String?) ?? _conversationTitle;
          });
          final action = event['action'] as Map<String, dynamic>?;
          if (action != null && action['kind'] != 'agent.run') {
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                content: Text(action['kind'] == 'youtube.generate'
                    ? 'Render started: ${action['topic']}'
                    : 'Started: ${action['kind']}')));
          }
          if (_speak && reply.content.isNotEmpty) await _say(reply.content);
        case 'error':
          setState(() {
            reply.content = reply.content.isEmpty
                ? 'Error: ${event['message']}'
                : reply.content;
            reply.streaming = false;
          });
        case 'done':
          if (!done.isCompleted) done.complete();
      }
    }, onError: (Object e) {
      if (!mounted) return;
      setState(() {
        reply.content = reply.content.isEmpty ? 'Error: $e' : reply.content;
        reply.streaming = false;
      });
      if (!done.isCompleted) done.complete();
    }, onDone: () {
      if (!done.isCompleted) done.complete();
    });

    await done.future;
    if (mounted) {
      setState(() {
        _busy = false;
        reply.streaming = false;
      });
      _autoScroll();
    }
  }

  /// One neural voice on every surface: the server's edge-tts if reachable, the
  /// device's own voice if not. Never silence.
  Future<void> _say(String text) async {
    final bytes = await ref.read(clientProvider).ttsBytes(text);
    if (bytes == null) {
      await VoicePrefs.applyTts(_tts);
      await _tts.speak(text);
      return;
    }
    setState(() => _speaking = true);
    try {
      await _player.setPlaybackRate(await VoicePrefs.rate());
      await _player.play(BytesSource(bytes));
    } catch (_) {
      setState(() => _speaking = false);
      await _tts.speak(text);
    }
  }

  void _autoScroll() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
      }
    });
  }
}

class _ThinkingBubble extends StatelessWidget {
  const _ThinkingBubble();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(top: 8, bottom: 2),
      child: Row(children: [
        const JarvisOrb(state: OrbState.thinking, size: 18),
        const SizedBox(width: 8),
        Text('Jarvis',
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: scheme.onSurface.withValues(alpha: 0.45),
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.3,
                )),
        const SizedBox(width: 10),
        const _ThinkingDots(),
      ]),
    );
  }
}

class _ThinkingDots extends StatelessWidget {
  const _ThinkingDots();

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.5);
    return Row(mainAxisSize: MainAxisSize.min, children: [
      for (var i = 0; i < 3; i++)
        Container(
          width: 7,
          height: 7,
          margin: const EdgeInsets.symmetric(horizontal: 3),
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        )
            .animate(onPlay: (c) => c.repeat())
            .fadeIn(delay: (i * 160).ms, duration: 400.ms)
            .then()
            .fadeOut(duration: 400.ms),
    ]);
  }
}

/// "Show your work": the steps an agent run took, from the rows it produced.
class _StepsCard extends ConsumerStatefulWidget {
  const _StepsCard({required this.runId, required this.status});

  final String runId;
  final String status;

  @override
  ConsumerState<_StepsCard> createState() => _StepsCardState();
}

class _StepsCardState extends ConsumerState<_StepsCard> {
  bool _open = false;
  Future<Map<String, dynamic>>? _run;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final (icon, color) = switch (widget.status) {
      'succeeded' => (Icons.check_circle_outline, JarvisColors.success),
      'awaiting_approval' => (Icons.lock_outline, JarvisColors.warning),
      'stopped' || 'awaiting_user' => (Icons.error_outline, JarvisColors.danger),
      _ => (Icons.bolt, scheme.primary),
    };
    return Container(
      decoration: BoxDecoration(
        color: context.surfaces.surface2,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: scheme.onSurface.withValues(alpha: 0.08)),
      ),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => setState(() {
            _open = !_open;
            _run ??= ref.read(clientProvider).run(widget.runId);
          }),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(icon, size: 16, color: color),
              const SizedBox(width: 6),
              Text('Agent · ${widget.status.replaceAll('_', ' ')}',
                  style: Theme.of(context).textTheme.labelMedium),
              const SizedBox(width: 6),
              Icon(_open ? Icons.expand_less : Icons.expand_more, size: 16),
            ]),
          ),
        ),
        if (_open)
          FutureBuilder<Map<String, dynamic>>(
            future: _run,
            builder: (context, snap) {
              if (!snap.hasData) {
                return const Padding(
                    padding: EdgeInsets.all(12), child: LinearProgressIndicator(minHeight: 2));
              }
              final steps = (snap.data!['steps'] as List<dynamic>).cast<Map<String, dynamic>>();
              if (steps.isEmpty) {
                return const Padding(
                    padding: EdgeInsets.fromLTRB(12, 0, 12, 10),
                    child: Text('No tool calls — answered directly.'));
              }
              return Column(children: [
                for (final st in steps)
                  ListTile(
                    dense: true,
                    leading: Icon(
                      switch (st['verdict'] ?? st['status']) {
                        'verified' => Icons.verified,
                        'failed' => Icons.error_outline,
                        'denied' => Icons.block,
                        'awaiting_approval' => Icons.hourglass_top,
                        _ => Icons.bolt,
                      },
                      size: 18,
                    ),
                    title: Text('${st['tool']}  ·  ${st['risk']}'),
                    subtitle: Text(
                      [
                        if ((st['rationale'] as String?)?.isNotEmpty == true) st['rationale'],
                        '${st['status']}${st['verdict'] != null ? ' · evidence ${st['verdict']}' : ''}',
                      ].join('\n'),
                    ),
                  ),
              ]);
            },
          ),
      ]),
    );
  }
}

/// 👍 / 👎 under a reply. A 👎 asks why — the reason is what teaches.
class _Thumbs extends ConsumerStatefulWidget {
  const _Thumbs({required this.turn});

  final _Turn turn;

  @override
  ConsumerState<_Thumbs> createState() => _ThumbsState();
}

class _ThumbsState extends ConsumerState<_Thumbs> {
  int? _given;

  Future<void> _rate(int score) async {
    String? note;
    if (score < 0) {
      final controller = TextEditingController();
      note = await showDialog<String>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('What was wrong?'),
          content: TextField(
            controller: controller,
            autofocus: true,
            maxLines: 3,
            decoration: const InputDecoration(
                hintText: 'e.g. too long · wrong person · should have asked first'),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context), child: const Text('Skip')),
            FilledButton(
                onPressed: () => Navigator.pop(context, controller.text.trim()),
                child: const Text('Teach')),
          ],
        ),
      );
      if (!mounted) return;
    }
    setState(() => _given = score);
    try {
      await ref.read(clientProvider).sendFeedback(
            score: score,
            note: note,
            excerpt: widget.turn.content.length > 300
                ? widget.turn.content.substring(0, 300)
                : widget.turn.content,
          );
      if (mounted && note != null && note.isNotEmpty) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('Learned — it will remember that')));
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.45);
    return Row(mainAxisSize: MainAxisSize.min, children: [
      IconButton(
        visualDensity: VisualDensity.compact,
        iconSize: 16,
        icon: Icon(_given == 1 ? Icons.thumb_up : Icons.thumb_up_outlined,
            color: _given == 1 ? JarvisColors.success : muted),
        onPressed: _given == null ? () => _rate(1) : null,
      ),
      IconButton(
        visualDensity: VisualDensity.compact,
        iconSize: 16,
        icon: Icon(_given == -1 ? Icons.thumb_down : Icons.thumb_down_outlined,
            color: _given == -1 ? JarvisColors.danger : muted),
        onPressed: _given == null ? () => _rate(-1) : null,
      ),
    ]);
  }
}

/// The persona menu, grouped: presets, the role library (agency-agents-derived), then
/// the user's own. A flat list of 20+ roles is unusable; the sections make it scannable.
List<PopupMenuEntry<String>> _groupedPersonaItems(
    BuildContext context, List<Map<String, dynamic>> personas) {
  const order = ['preset', 'library', 'custom'];
  const titles = {'preset': 'Presets', 'library': 'Role library', 'custom': 'Yours'};
  final items = <PopupMenuEntry<String>>[];
  for (final group in order) {
    final inGroup = personas.where((p) => (p['group'] ?? 'preset') == group).toList();
    if (inGroup.isEmpty) continue;
    if (items.isNotEmpty) items.add(const PopupMenuDivider());
    items.add(PopupMenuItem<String>(
      enabled: false,
      height: 28,
      child: Text(titles[group]!,
          style: Theme.of(context)
              .textTheme
              .labelSmall
              ?.copyWith(fontWeight: FontWeight.bold, letterSpacing: 0.5)),
    ));
    for (final p in inGroup) {
      items.add(PopupMenuItem<String>(
        value: p['key'] as String,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(p['name'] as String),
            if ((p['description'] as String? ?? '').isNotEmpty)
              Text(p['description'] as String,
                  style: Theme.of(context).textTheme.labelSmall),
          ],
        ),
      ));
    }
  }
  return items;
}

/// Up to three follow-up suggestions for the last answer (FEATURES-50 #15). Pure, so it
/// is unit-tested; the chips are generic-but-useful, no extra model call.
List<String> followUps(String reply) {
  final r = reply.toLowerCase();
  final out = <String>[];
  if (r.contains('deadline') || r.contains('due') || r.contains(' task')) {
    out.add('Add to my deadlines');
  }
  if (r.contains('email') || r.contains('message') || r.contains('reply')) {
    out.add('Draft a reply');
  }
  if (reply.length > 400) out.add('Summarise that');
  out.add('Tell me more');
  return out.toSet().take(3).toList();
}
