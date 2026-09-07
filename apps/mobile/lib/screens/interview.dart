import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/models.dart';
import '../state/providers.dart';
import '../widgets/orb.dart';
import '../widgets/states.dart';

/// "Set up in 3 minutes": six questions, one at a time, in Jarvis's voice. The answers
/// become the profile — distilled by the model when one is reachable.
class InterviewScreen extends ConsumerStatefulWidget {
  const InterviewScreen({super.key});

  @override
  ConsumerState<InterviewScreen> createState() => _InterviewScreenState();
}

class _InterviewScreenState extends ConsumerState<InterviewScreen> {
  List<Map<String, dynamic>>? _questions;
  final Map<String, String> _answers = {};
  final _input = TextEditingController();
  int _index = 0;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    ref.read(clientProvider).interviewQuestions().then((q) {
      if (mounted) setState(() => _questions = q);
    }).catchError((e) {
      if (mounted) setState(() => _error = '$e');
    });
  }

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  void _next() {
    final q = _questions![_index];
    _answers[q['key'] as String] = _input.text.trim();
    _input.clear();
    if (_index + 1 < _questions!.length) {
      setState(() => _index++);
    } else {
      _submit();
    }
  }

  Future<void> _submit() async {
    setState(() => _busy = true);
    try {
      final r = await ref.read(clientProvider).submitInterview(_answers);
      if (!mounted) return;
      Navigator.pop(context, r);
    } on ProblemException catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final questions = _questions;
    return Scaffold(
      appBar: AppBar(title: const Text('Set up in 3 minutes')),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: () => Navigator.pop(context))
          : questions == null
              ? const Center(child: CircularProgressIndicator())
              : Padding(
                  padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    LinearProgressIndicator(
                      value: (_index + (_busy ? 1 : 0)) / questions.length,
                      minHeight: 3,
                    ),
                    const SizedBox(height: 24),
                    Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      JarvisOrb(state: _busy ? OrbState.thinking : OrbState.speaking, size: 48),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Text(
                          questions[_index]['question'] as String,
                          key: ValueKey(_index),
                          style: Theme.of(context).textTheme.titleLarge?.copyWith(height: 1.3),
                        ).animate().fadeIn(duration: 300.ms).slideY(begin: 0.05),
                      ),
                    ]),
                    const SizedBox(height: 20),
                    Expanded(
                      child: TextField(
                        controller: _input,
                        autofocus: true,
                        maxLines: null,
                        expands: true,
                        textAlignVertical: TextAlignVertical.top,
                        decoration: const InputDecoration(
                          hintText: 'Type it the way you would say it.',
                          alignLabelWithHint: true,
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                    Row(children: [
                      Text('${_index + 1} of ${questions.length}',
                          style: Theme.of(context).textTheme.labelMedium),
                      const Spacer(),
                      TextButton(
                        onPressed: _busy ? null : _next,
                        child: const Text('Skip'),
                      ),
                      const SizedBox(width: 8),
                      FilledButton.icon(
                        onPressed: _busy ? null : _next,
                        icon: Icon(_index + 1 < questions.length ? Icons.arrow_forward : Icons.check),
                        label: Text(_busy
                            ? 'Writing your profile…'
                            : _index + 1 < questions.length
                                ? 'Next'
                                : 'Finish'),
                      ),
                    ]),
                  ]),
                ),
    );
  }
}
