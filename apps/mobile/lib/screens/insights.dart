import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:health/health.dart';
import 'package:image_picker/image_picker.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../node/platform_hooks.dart';
import '../state/providers.dart';
import '../theme.dart';

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
  List<Map<String, dynamic>> _upcoming = const [];
  List<Map<String, dynamic>> _owed = const [];
  List<Map<String, dynamic>> _resurface = const [];
  List<Map<String, dynamic>> _phrasebook = const [];
  List<Map<String, dynamic>> _people = const [];
  Map<String, dynamic> _interests = const {};
  Map<String, dynamic> _rhythm = const {};
  Map<String, dynamic> _mood = const {};
  Map<String, dynamic> _speech = const {};
  List<Map<String, dynamic>> _gaps = const [];
  List<Map<String, dynamic>> _decisionsDue = const [];
  Map<String, dynamic> _focus = const {};
  List<Map<String, dynamic>> _places = const [];
  List<Map<String, dynamic>> _media = const [];
  Map<String, dynamic> _commCoach = const {};
  Map<String, dynamic> _health = const {};
  Map<String, dynamic> _peak = const {};
  Map<String, dynamic> _rediscover = const {};
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
        client.coach(),
        client.meetingPrep(),
        client.groupedActivity(),
        client.anomalies(),
        client.commitments(),
        client.upcoming(),
        client.owedReplies(),
        client.resurfacedMemories(),
        client.phrasebook(),
        client.relationships(),
        client.interests(),
        client.rhythm(),
        client.mood(),
        client.speechProfile(),
        client.knowledgeGaps(),
        client.decisionsDue(),
        client.focusAnalytics(),
        client.rediscover(),
        client.places(),
        client.mediaDiary(),
        client.commCoach(),
        client.healthCorrelation(),
        client.peak(),
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
        _upcoming = results[9] as List<Map<String, dynamic>>;
        _owed = results[10] as List<Map<String, dynamic>>;
        _resurface = results[11] as List<Map<String, dynamic>>;
        _phrasebook = results[12] as List<Map<String, dynamic>>;
        _people = results[13] as List<Map<String, dynamic>>;
        _interests = results[14] as Map<String, dynamic>;
        _rhythm = results[15] as Map<String, dynamic>;
        _mood = results[16] as Map<String, dynamic>;
        _speech = results[17] as Map<String, dynamic>;
        _gaps = results[18] as List<Map<String, dynamic>>;
        _decisionsDue = results[19] as List<Map<String, dynamic>>;
        _focus = results[20] as Map<String, dynamic>;
        _rediscover = results[21] as Map<String, dynamic>;
        _places = results[22] as List<Map<String, dynamic>>;
        _media = results[23] as List<Map<String, dynamic>>;
        _commCoach = results[24] as Map<String, dynamic>;
        _health = results[25] as Map<String, dynamic>;
        _peak = results[26] as Map<String, dynamic>;
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

  Future<void> _connectHealth() async {
    final deviceId =
        await ref.read(secureStorageProvider).read(key: 'phone_node_device_id');
    if (deviceId == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Pair this phone in Devices first.')));
      }
      return;
    }
    try {
      final health = Health();
      await health.configure();
      const types = [HealthDataType.STEPS, HealthDataType.SLEEP_ASLEEP];
      final ok = await health.requestAuthorization(types);
      if (!ok) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Health access not granted.')));
        }
        return;
      }
      final now = DateTime.now();
      final samples = <Map<String, dynamic>>[];
      for (var d = 0; d < 21; d++) {
        final end = DateTime(now.year, now.month, now.day).subtract(Duration(days: d));
        final start = end.subtract(const Duration(days: 1));
        final steps = await health.getTotalStepsInInterval(start, end) ?? 0;
        final sleep = await health.getHealthDataFromTypes(
            startTime: start, endTime: end, types: [HealthDataType.SLEEP_ASLEEP]);
        var sleepMin = 0;
        for (final p in sleep) {
          sleepMin += p.dateTo.difference(p.dateFrom).inMinutes;
        }
        samples.add({
          'day': start.toUtc().toIso8601String(),
          'steps': steps,
          'sleep_minutes': sleepMin,
        });
      }
      await ref.read(clientProvider).postHealth(deviceId, samples);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Health synced. Correlations will appear as data builds.')));
      }
      await _load();
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Health: $e')));
      }
    }
  }

  Future<void> _indexPhoto() async {
    final deviceId =
        await ref.read(secureStorageProvider).read(key: 'phone_node_device_id');
    if (deviceId == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Pair this phone in Devices first.')));
      }
      return;
    }
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery);
    if (picked == null || !mounted) return;
    final controller = TextEditingController();
    final caption = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('What is this photo?'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
              hintText: 'Receipt from Goa · whiteboard · a book cover'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('Index'),
          ),
        ],
      ),
    );
    if (caption == null || caption.isEmpty) return;
    try {
      await ref.read(clientProvider).postPhoto(deviceId, [
        {'caption': caption, 'uri': picked.path,
         'at': DateTime.now().toUtc().toIso8601String()}
      ]);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Indexed — find it later in search.')));
      }
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _addLocation() async {
    final deviceId =
        await ref.read(secureStorageProvider).read(key: 'phone_node_device_id');
    if (deviceId == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Pair this phone in Devices first, then place learning can start.')));
      }
      return;
    }
    final fix = await platformLocate();
    if (fix == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Location unavailable — grant location permission.')));
      }
      return;
    }
    try {
      await ref.read(clientProvider).postLocation(deviceId, [
        {'lat': fix['lat'], 'lng': fix['lng'], 'at': DateTime.now().toUtc().toIso8601String()}
      ]);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Location added (rounded to ~500 m). Places emerge over time.')));
      }
      await _load();
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _addMediaNote(String sourceId, String? existing) async {
    final controller = TextEditingController(text: existing ?? '');
    final note = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Why it mattered'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 3,
          decoration: const InputDecoration(hintText: 'One line — what you took from it'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (note == null || note.isEmpty) return;
    try {
      await ref.read(clientProvider).setMediaNote(sourceId, note);
      await _load();
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _timeTravel() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: DateTime.now().subtract(const Duration(days: 1)),
      firstDate: DateTime.now().subtract(const Duration(days: 365)),
      lastDate: DateTime.now(),
      helpText: 'Look back at a day',
    );
    if (picked == null || !mounted) return;
    final iso = '${picked.year.toString().padLeft(4, '0')}-'
        '${picked.month.toString().padLeft(2, '0')}-'
        '${picked.day.toString().padLeft(2, '0')}';
    Map<String, dynamic> recon = const {};
    try {
      recon = await ref.read(clientProvider).timetravel(iso);
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      return;
    }
    if (!mounted) return;
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (_) => _DayReconstruction(recon: recon),
    );
  }

  Future<void> _showLesson(String topic) async {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (ctx) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.7,
        builder: (ctx, controller) => FutureBuilder<Map<String, dynamic>>(
          future: ref.read(clientProvider).microLesson(topic),
          builder: (ctx, snap) {
            if (!snap.hasData) {
              return const Center(child: Padding(
                  padding: EdgeInsets.all(32), child: CircularProgressIndicator()));
            }
            final lesson = snap.data!['lesson'] as String?;
            final reason = snap.data!['reason'] as String?;
            return ListView(
              controller: controller,
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
              children: [
                Row(children: [
                  const Icon(Icons.school_outlined, size: 20),
                  const SizedBox(width: 8),
                  Expanded(child: Text(topic, style: Theme.of(ctx).textTheme.titleMedium)),
                ]),
                const SizedBox(height: 12),
                Text(lesson ?? reason ?? "Couldn't make a lesson right now.",
                    style: Theme.of(ctx).textTheme.bodyMedium),
              ],
            );
          },
        ),
      ),
    );
  }

  Future<void> _showLogDecision(BuildContext context) async {
    final logged = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => const _LogDecisionSheet(),
    );
    if (logged == true) _load();
  }

  bool get _hasDrift =>
      (_interests['rising'] as List?)?.isNotEmpty == true ||
      (_interests['fading'] as List?)?.isNotEmpty == true;

  bool get _hasAboutYou =>
      _mood['enough_data'] == true ||
      _speech['enough_data'] == true ||
      _gaps.isNotEmpty;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Insights'),
        actions: [
          IconButton(
            tooltip: 'Index a photo',
            icon: const Icon(Icons.add_photo_alternate_outlined),
            onPressed: _indexPhoto,
          ),
          IconButton(
            tooltip: 'Look back at a day',
            icon: const Icon(Icons.history),
            onPressed: _timeTravel,
          ),
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
                      if (_upcoming.isNotEmpty) _ComingUpCard(items: _upcoming),
                      if (_upcoming.isNotEmpty) const SizedBox(height: 8),
                      if (_owed.isNotEmpty) _OwedRepliesCard(items: _owed),
                      if (_owed.isNotEmpty) const SizedBox(height: 8),
                      if (_resurface.isNotEmpty) _ResurfaceCard(items: _resurface),
                      if (_resurface.isNotEmpty) const SizedBox(height: 8),
                      if (_rediscover['content'] != null) _RediscoverCard(item: _rediscover),
                      if (_rediscover['content'] != null) const SizedBox(height: 8),
                      if (_people.any((p) => p['quiet'] == true))
                        _PeopleCard(people: _people),
                      if (_people.any((p) => p['quiet'] == true))
                        const SizedBox(height: 8),
                      if ((_commCoach['observations'] as List?)?.isNotEmpty == true)
                        _CommCoachCard(coach: _commCoach),
                      if ((_commCoach['observations'] as List?)?.isNotEmpty == true)
                        const SizedBox(height: 8),
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
                      _DecisionsCard(
                        due: _decisionsDue,
                        onLog: () => _showLogDecision(context),
                        onReview: (id, outcome) async {
                          await ref.read(clientProvider).reviewDecision(id, outcome);
                          _load();
                        },
                      ),
                      const SizedBox(height: 8),
                      if (_anomalies.isNotEmpty) _AnomalyCard(nudges: _anomalies),
                      if (_anomalies.isNotEmpty) const SizedBox(height: 8),
                      if (_meeting != null) _MeetingCard(meeting: _meeting!),
                      if (_meeting != null) const SizedBox(height: 8),
                      _StreaksCard(streaks: _streaks),
                      const SizedBox(height: 8),
                      _HealthCard(health: _health, onConnect: _connectHealth),
                      const SizedBox(height: 8),
                      if (_grouped.isNotEmpty) _GroupedCard(groups: _grouped),
                      if (_grouped.isNotEmpty) const SizedBox(height: 8),
                      _AwayCard(away: _away),
                      const SizedBox(height: 8),
                      _PlacesCard(places: _places, onAdd: _addLocation),
                      const SizedBox(height: 8),
                      if (_media.isNotEmpty)
                        _MediaDiaryCard(items: _media, onNote: _addMediaNote),
                      if (_media.isNotEmpty) const SizedBox(height: 8),
                      _SpendingCard(spending: _spending),
                      const SizedBox(height: 8),
                      if (_travel.isNotEmpty) _TravelCard(trips: _travel),
                      if (_travel.isNotEmpty) const SizedBox(height: 8),
                      _LabelsCard(labels: _labels),
                      if (_hasDrift) const SizedBox(height: 8),
                      if (_hasDrift) _InterestsCard(drift: _interests),
                      if (_phrasebook.isNotEmpty) const SizedBox(height: 8),
                      if (_phrasebook.isNotEmpty) _PhrasebookCard(terms: _phrasebook),
                      if (_rhythm['enough_data'] == true) const SizedBox(height: 8),
                      if (_rhythm['enough_data'] == true) _RhythmCard(rhythm: _rhythm),
                      if (_peak['enough_data'] == true) const SizedBox(height: 8),
                      if (_peak['enough_data'] == true) _PeakCard(peak: _peak),
                      if (_focus['enough_data'] == true) const SizedBox(height: 8),
                      if (_focus['enough_data'] == true) _FocusCard(focus: _focus),
                      if (_hasAboutYou) const SizedBox(height: 8),
                      if (_hasAboutYou)
                        _AboutYouCard(
                            mood: _mood, speech: _speech, gaps: _gaps, onLesson: _showLesson),
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
          for (final m in [focus['message'], done['message']])
            if (m != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text('$m', style: Theme.of(context).textTheme.bodySmall),
              ),
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

/// About-to-forget: what's coming due, surfaced just before you need it (second-brain #24).
class _ComingUpCard extends StatelessWidget {
  const _ComingUpCard({required this.items});
  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: scheme.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.upcoming_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Coming up', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 4),
          for (final it in items)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(
                it['type'] == 'commitment' ? Icons.handshake_outlined : Icons.event_outlined,
                size: 20,
                color: it['overdue'] == true ? scheme.error : scheme.onSecondaryContainer,
              ),
              title: Text(it['text'] as String? ?? '',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              trailing: Text(_when(it['when'] as String?, it['overdue'] == true),
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: it['overdue'] == true ? scheme.error : null)),
            ),
        ]),
      ),
    );
  }

  static String _when(String? iso, bool overdue) {
    final d = iso != null ? DateTime.tryParse(iso)?.toLocal() : null;
    if (d == null) return '';
    if (overdue) return 'overdue';
    final left = d.difference(DateTime.now());
    if (left.inHours < 1) return '${left.inMinutes}m';
    if (left.inHours < 24) return '${left.inHours}h';
    return '${left.inDays}d';
  }
}


/// Dropped-thread finder: people who asked something you may owe a reply (second-brain
/// #28). Read off the triage 'needs_reply' classifications; muted senders are excluded.
class _OwedRepliesCard extends StatelessWidget {
  const _OwedRepliesCard({required this.items});
  final List<Map<String, dynamic>> items;

  static String _name(String raw) =>
      raw.replaceAll(RegExp(r'<[^>]*>'), '').trim().replaceAll(RegExp(r'^"|"$'), '');

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: scheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.reply_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Owe a reply?', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 2),
          Text('They asked you something and may still be waiting.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 4),
          for (final it in items)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(
                it['provider'] == 'gmail' ? Icons.mail_outline : Icons.chat_bubble_outline,
                size: 20, color: scheme.onTertiaryContainer,
              ),
              title: Text(_name(it['sender'] as String? ?? 'someone'),
                  maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              subtitle: Text(it['subject'] as String? ?? '',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              trailing: it['url'] == null
                  ? null
                  : const Icon(Icons.open_in_new, size: 16),
              onTap: it['url'] == null
                  ? null
                  : () => launchUrl(Uri.parse(it['url'] as String),
                      mode: LaunchMode.externalApplication),
            ),
        ]),
      ),
    );
  }
}


/// Spaced-repetition resurfacing: important things you'd forget, brought back on a
/// forgetting curve (second-brain #21). Read-only card; the curve advances server-side.
class _ResurfaceCard extends StatelessWidget {
  const _ResurfaceCard({required this.items});
  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: scheme.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.lightbulb_outline, size: 20),
            const SizedBox(width: 8),
            Text('Worth remembering', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 2),
          Text("Things you learned once, so they don't slip away.",
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 4),
          for (final it in items)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(Icons.bookmark_border, size: 18, color: scheme.onPrimaryContainer),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(it['content'] as String? ?? '',
                      style: Theme.of(context).textTheme.bodyMedium),
                ),
              ]),
            ),
        ]),
      ),
    );
  }
}


/// Your personal phrasebook: the recurring names, jargon and acronyms JARVIS learned from
/// your own words (second-brain #13) — what it should never mishear.
class _PhrasebookCard extends StatelessWidget {
  const _PhrasebookCard({required this.terms});
  final List<Map<String, dynamic>> terms;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.menu_book_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Your words', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 2),
          Text('Names and terms you use often — so JARVIS gets them right.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 10),
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final t in terms)
              Chip(
                visualDensity: VisualDensity.compact,
                label: Text('${t['term']}'),
              ),
          ]),
        ]),
      ),
    );
  }
}


/// Relationship cadence: people you usually keep up with but have gone quiet on — the
/// reconnect nudge (second-brain #16). Only the quiet ones surface; staying in touch needs
/// no reminder.
class _PeopleCard extends StatelessWidget {
  const _PeopleCard({required this.people});
  final List<Map<String, dynamic>> people;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final quiet = people.where((p) => p['quiet'] == true).toList();
    return Card(
      color: scheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.diversity_3_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Reconnect?', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 2),
          Text("People you usually keep up with, but haven't lately.",
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 4),
          for (final p in quiet)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: CircleAvatar(
                radius: 16,
                backgroundColor: scheme.surface,
                child: Text(
                  ((p['name'] as String?)?.trim().isNotEmpty ?? false)
                      ? (p['name'] as String).trim()[0].toUpperCase()
                      : '?',
                  style: TextStyle(color: scheme.onSurface),
                ),
              ),
              title: Text('${p['name']}',
                  maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              subtitle: Text([
                if (p['relation'] != null) '${p['relation']}',
                'usually every ${p['cadence_days']}d',
              ].join(' · ')),
              trailing: Text('${p['days_since']}d ago',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.error)),
            ),
        ]),
      ),
    );
  }
}


/// Interest drift: topics rising and fading in your own words (second-brain #17) — so
/// briefings track the current you, not a stale profile.
class _InterestsCard extends StatelessWidget {
  const _InterestsCard({required this.drift});
  final Map<String, dynamic> drift;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final rising = (drift['rising'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    final fading = (drift['fading'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.trending_up, size: 20),
            const SizedBox(width: 8),
            Text('Where your head is', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 2),
          Text('Topics moving up and down in what you talk about.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 10),
          if (rising.isNotEmpty) ...[
            Text('Rising', style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 4),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final t in rising)
                Chip(
                  visualDensity: VisualDensity.compact,
                  avatar: Icon(Icons.arrow_upward, size: 14, color: scheme.primary),
                  label: Text('${t['term']}'),
                ),
            ]),
          ],
          if (fading.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('Fading', style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 4),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final t in fading)
                Chip(
                  visualDensity: VisualDensity.compact,
                  avatar: Icon(Icons.arrow_downward, size: 14,
                      color: scheme.onSurface.withValues(alpha: 0.5)),
                  label: Text('${t['term']}'),
                ),
            ]),
          ],
        ]),
      ),
    );
  }
}


/// Your rhythm (#15): the energy curve by hour — when you focus and when you slump — as a
/// one-line takeaway plus a 24-bar day sparkline. Deterministic from your focus/done history.
class _RhythmCard extends StatelessWidget {
  const _RhythmCard({required this.rhythm});
  final Map<String, dynamic> rhythm;

  static String _hour(int h) {
    final ampm = h < 12 ? 'am' : 'pm';
    final h12 = h % 12 == 0 ? 12 : h % 12;
    return '$h12$ampm';
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final byHour = (rhythm['by_hour'] as List<dynamic>? ?? const []).cast<int>();
    final peak = byHour.isEmpty ? 1 : byHour.reduce((a, b) => a > b ? a : b);
    final w = rhythm['best_window'] as Map<String, dynamic>?;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.bolt_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Your rhythm', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 4),
          Text(
            w == null
                ? 'When you tend to get things done.'
                : "You're sharpest around ${_hour(w['start'] as int)}–${_hour(w['end'] as int)}.",
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 40,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                for (var h = 0; h < byHour.length; h++)
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 1),
                      child: Container(
                        height: 4 + 34 * (peak == 0 ? 0 : byHour[h] / peak),
                        decoration: BoxDecoration(
                          color: (w != null &&
                                  _inWindow(h, w['start'] as int, w['end'] as int))
                              ? scheme.primary
                              : scheme.primary.withValues(alpha: 0.25),
                          borderRadius: BorderRadius.circular(2),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            Text('12am', style: Theme.of(context).textTheme.labelSmall),
            Text('12pm', style: Theme.of(context).textTheme.labelSmall),
            Text('11pm', style: Theme.of(context).textTheme.labelSmall),
          ]),
        ]),
      ),
    );
  }

  static bool _inWindow(int h, int start, int end) =>
      start <= end ? (h >= start && h < end) : (h >= start || h < end);
}


/// "About you": a consolidated glance at what JARVIS has learned about how you tick — a
/// private mood trend (#18), how you phrase things (#12), and topics you keep asking about
/// (#29). One card, so it informs without crowding.
class _AboutYouCard extends StatelessWidget {
  const _AboutYouCard(
      {required this.mood, required this.speech, required this.gaps, required this.onLesson});
  final Map<String, dynamic> mood;
  final Map<String, dynamic> speech;
  final List<Map<String, dynamic>> gaps;
  final void Function(String topic) onLesson;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final moodOk = mood['enough_data'] == true;
    final speechOk = speech['enough_data'] == true;
    final fillers = (speech['fillers'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    final moodWord = mood['mood'] as String?;
    final moodColor = {
      'up': JarvisColors.success,
      'down': JarvisColors.warning,
      'steady': scheme.onSurface,
    }[moodWord];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.self_improvement_outlined, size: 20),
            const SizedBox(width: 8),
            Text('About you', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 2),
          Text('What JARVIS is learning about how you tick — private to you.',
              style: Theme.of(context).textTheme.bodySmall),
          if (moodOk) ...[
            const SizedBox(height: 12),
            Row(children: [
              Icon(Icons.mood, size: 16, color: moodColor),
              const SizedBox(width: 8),
              Text('Mood lately: ',
                  style: Theme.of(context).textTheme.bodyMedium),
              Text('$moodWord',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      fontWeight: FontWeight.w600, color: moodColor)),
            ]),
          ],
          if (speechOk) ...[
            const SizedBox(height: 10),
            Text('You write ~${speech['avg_sentence_words']} words a sentence'
                '${fillers.isEmpty ? '' : '. You lean on:'}',
                style: Theme.of(context).textTheme.bodyMedium),
            if (fillers.isNotEmpty) ...[
              const SizedBox(height: 6),
              Wrap(spacing: 6, runSpacing: 6, children: [
                for (final f in fillers.take(5))
                  Chip(visualDensity: VisualDensity.compact, label: Text('"${f['term']}"')),
              ]),
            ],
          ],
          if (gaps.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('You keep asking about (tap for a 3-min lesson):',
                style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final g in gaps.take(6))
                ActionChip(
                  visualDensity: VisualDensity.compact,
                  avatar: const Icon(Icons.school_outlined, size: 14),
                  label: Text('${g['topic']}'),
                  onPressed: () => onLesson('${g['topic']}'),
                ),
            ]),
          ],
        ]),
      ),
    );
  }
}


/// Decision journal (#39): log a decision and your reasoning; when its review date comes,
/// answer "did it work?" — so you learn to decide better. Always visible so you can log one;
/// due-for-review decisions surface here with worked/mixed/didn't.
class _DecisionsCard extends StatelessWidget {
  const _DecisionsCard({required this.due, required this.onLog, required this.onReview});
  final List<Map<String, dynamic>> due;
  final VoidCallback onLog;
  final void Function(String id, String outcome) onReview;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.account_tree_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Decisions', style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            TextButton.icon(
              onPressed: onLog,
              icon: const Icon(Icons.add, size: 18),
              label: const Text('Log'),
            ),
          ]),
          if (due.isEmpty)
            Text('Log a decision and your reasoning. Weeks later, JARVIS asks how it went.',
                style: Theme.of(context).textTheme.bodySmall)
          else ...[
            Text('Time to review — did it work out?',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 4),
            for (final d in due)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${d['text']}',
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                  if ((d['expected'] as String?)?.isNotEmpty ?? false)
                    Text('You expected: ${d['expected']}',
                        style: Theme.of(context).textTheme.labelSmall),
                  const SizedBox(height: 6),
                  Wrap(spacing: 8, children: [
                    for (final o in const [
                      ('worked', 'Worked'),
                      ('mixed', 'Mixed'),
                      ('didnt', "Didn't")
                    ])
                      OutlinedButton(
                        onPressed: () => onReview(d['id'] as String, o.$1),
                        child: Text(o.$2),
                      ),
                  ]),
                ]),
              ),
          ],
        ]),
      ),
    );
  }
}

class _LogDecisionSheet extends ConsumerStatefulWidget {
  const _LogDecisionSheet();
  @override
  ConsumerState<_LogDecisionSheet> createState() => _LogDecisionSheetState();
}

class _LogDecisionSheetState extends ConsumerState<_LogDecisionSheet> {
  final _text = TextEditingController();
  final _reasoning = TextEditingController();
  final _expected = TextEditingController();
  int _days = 30;
  bool _saving = false;

  @override
  void dispose() {
    _text.dispose();
    _reasoning.dispose();
    _expected.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_text.text.trim().isEmpty) return;
    setState(() => _saving = true);
    try {
      await ref.read(clientProvider).logDecision(_text.text.trim(),
          reasoning: _reasoning.text.trim(), expected: _expected.text.trim(), reviewInDays: _days);
      if (mounted) Navigator.pop(context, true);
    } on ProblemException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
          20, 4, 20, MediaQuery.viewInsetsOf(context).bottom + 20),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Log a decision', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 12),
        TextField(
          controller: _text,
          autofocus: true,
          decoration: const InputDecoration(
              labelText: 'The decision', hintText: 'Take the job · Move to X · Ship the beta'),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _reasoning,
          decoration: const InputDecoration(labelText: 'Why (your reasoning)'),
          maxLines: 2,
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _expected,
          decoration: const InputDecoration(labelText: 'What you expect to happen'),
        ),
        const SizedBox(height: 12),
        Row(children: [
          const Text('Review in'),
          const SizedBox(width: 12),
          DropdownButton<int>(
            value: _days,
            items: const [
              DropdownMenuItem(value: 7, child: Text('1 week')),
              DropdownMenuItem(value: 30, child: Text('1 month')),
              DropdownMenuItem(value: 90, child: Text('3 months')),
              DropdownMenuItem(value: 180, child: Text('6 months')),
            ],
            onChanged: (v) => setState(() => _days = v ?? 30),
          ),
        ]),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: _saving ? null : _save,
          icon: _saving
              ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.check),
          label: Text(_saving ? 'Saving…' : 'Log decision'),
        ),
      ]),
    );
  }
}


/// Focus analytics (#36): deep-work vs distraction minutes, the apps that pull you away, and
/// your best focus window — over the activity samples a device collected. Only shown when a
/// device is sampling.
class _FocusCard extends StatelessWidget {
  const _FocusCard({required this.focus});
  final Map<String, dynamic> focus;

  static String _mins(num m) => m >= 60 ? '${(m / 60).toStringAsFixed(1)}h' : '${m.round()}m';

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final distractions =
        (focus['distractions'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    final w = focus['best_window'] as Map<String, dynamic>?;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.center_focus_strong_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Focus (${focus['days']}d)', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(_mins(focus['deep_minutes'] as num? ?? 0),
                    style: Theme.of(context).textTheme.headlineSmall),
                Text('deep work', style: Theme.of(context).textTheme.labelSmall),
              ]),
            ),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(_mins(focus['distraction_minutes'] as num? ?? 0),
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(color: scheme.error)),
                Text('distracted', style: Theme.of(context).textTheme.labelSmall),
              ]),
            ),
          ]),
          if (distractions.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('Pulls you away: ${distractions.map((d) => d['app']).take(3).join(', ')}',
                style: Theme.of(context).textTheme.bodySmall),
          ],
          if (w != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('Best focus window earlier today shows in Your rhythm.',
                  style: Theme.of(context).textTheme.labelSmall),
            ),
        ]),
      ),
    );
  }
}

/// Time-travel (#30): a past day rebuilt from what touched it — tasks done, deadlines, mail,
/// notes, and the apps you spent time in.
class _DayReconstruction extends StatelessWidget {
  const _DayReconstruction({required this.recon});
  final Map<String, dynamic> recon;

  @override
  Widget build(BuildContext context) {
    final done = (recon['done'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    final deadlines =
        (recon['deadlines'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    final messages = (recon['messages'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    final notes = (recon['notes'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    final apps = (recon['apps'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();

    Widget section(String title, IconData icon, List<Widget> rows) => rows.isEmpty
        ? const SizedBox.shrink()
        : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const SizedBox(height: 12),
            Row(children: [
              Icon(icon, size: 16),
              const SizedBox(width: 8),
              Text(title, style: Theme.of(context).textTheme.labelLarge),
            ]),
            const SizedBox(height: 4),
            ...rows,
          ]);

    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.75,
      builder: (context, controller) => ListView(
        controller: controller,
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
        children: [
          Text(recon['date'] as String? ?? 'That day',
              style: Theme.of(context).textTheme.titleLarge),
          if (recon['empty'] == true)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text("Nothing captured that day.",
                  style: Theme.of(context).textTheme.bodySmall),
            ),
          section('Finished', Icons.check_circle_outline,
              [for (final t in done) Text('• ${t['title']}')]),
          section('Due', Icons.event_outlined,
              [for (final t in deadlines) Text('• ${t['title']}')]),
          section('Messages', Icons.mail_outline, [
            for (final m in messages)
              Text('• ${m['from'] ?? ''}: ${m['subject'] ?? ''}',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
          ]),
          section('Notes', Icons.sticky_note_2_outlined, [
            for (final n in notes)
              Text('• ${n['content']}', maxLines: 2, overflow: TextOverflow.ellipsis),
          ]),
          section('Time in apps', Icons.apps, [
            for (final a in apps) Text('• ${a['app']} — ${a['minutes']}m'),
          ]),
        ],
      ),
    );
  }
}


/// Rediscover (#27): an old idea or note relevant to what you're doing right now —
/// serendipity on purpose.
class _RediscoverCard extends StatelessWidget {
  const _RediscoverCard({required this.item});
  final Map<String, dynamic> item;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: scheme.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.travel_explore_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Rediscover', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 6),
          Text('${item['content']}', style: Theme.of(context).textTheme.bodyMedium),
          const SizedBox(height: 6),
          Text('From ${item['age_days']} days ago · because you mentioned "${item['because']}"',
              style: Theme.of(context).textTheme.labelSmall),
        ]),
      ),
    );
  }
}


/// Significant places, learned from coarse location (#5) — home, work, the places you
/// frequent. Coordinates are rounded to ~500 m server-side: context, not a map. "Add current
/// location" contributes one coarse fix; places emerge from the pattern over time.
class _PlacesCard extends StatelessWidget {
  const _PlacesCard({required this.places, required this.onAdd});
  final List<Map<String, dynamic>> places;
  final Future<void> Function() onAdd;

  static const _icon = {
    'home': Icons.home_outlined,
    'work': Icons.work_outline,
    'frequent place': Icons.place_outlined,
  };

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.map_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Places', style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            TextButton.icon(
              onPressed: onAdd,
              icon: const Icon(Icons.add_location_alt_outlined, size: 18),
              label: const Text('Add'),
            ),
          ]),
          if (places.isEmpty)
            Text('Where you spend time — learned from coarse location (rounded to ~500 m, '
                'never a map). Tap Add to contribute a fix.',
                style: Theme.of(context).textTheme.bodySmall)
          else
            for (final p in places)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: Icon(_icon[p['label']] ?? Icons.place_outlined, size: 20),
                title: Text('${p['label']}',
                    style: const TextStyle(fontWeight: FontWeight.w600)),
                trailing: Text('${((p['share'] as num) * 100).round()}% of the time',
                    style: Theme.of(context).textTheme.labelSmall),
              ),
        ]),
      ),
    );
  }
}


/// Media diary (#6): what you watched and read (from the reading log), with a one-line "why
/// it mattered" you attach — so consumption becomes recall-able knowledge. Tap to annotate.
class _MediaDiaryCard extends StatelessWidget {
  const _MediaDiaryCard({required this.items, required this.onNote});
  final List<Map<String, dynamic>> items;
  final void Function(String sourceId, String? existing) onNote;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.subscriptions_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Media diary', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 2),
          Text('What you watched and read — add a takeaway to keep it.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 4),
          for (final m in items.take(8))
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(m['kind'] == 'video' ? Icons.play_circle_outline
                  : m['kind'] == 'pdf' ? Icons.picture_as_pdf_outlined
                  : Icons.article_outlined, size: 20),
              title: Text('${m['title'] ?? m['url'] ?? ''}',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: m['note'] != null
                  ? Text('“${m['note']}”', maxLines: 2, overflow: TextOverflow.ellipsis)
                  : null,
              trailing: IconButton(
                tooltip: m['note'] != null ? 'Edit takeaway' : 'Add takeaway',
                icon: Icon(m['note'] != null ? Icons.edit_note : Icons.add_comment_outlined,
                    size: 20),
                onPressed: () => onNote(m['id'] as String, m['note'] as String?),
              ),
            ),
        ]),
      ),
    );
  }
}


/// Communication coach (#37): who's waiting on you, who you've gone quiet on, and how your
/// writing lands — each with a concrete fix. Composes the comm-specific signals.
class _CommCoachCard extends StatelessWidget {
  const _CommCoachCard({required this.coach});
  final Map<String, dynamic> coach;

  static const _icon = {
    'waiting': Icons.hourglass_bottom,
    'quiet': Icons.notifications_paused_outlined,
    'tone': Icons.record_voice_over_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final obs = (coach['observations'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.forum_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Communication coach', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 4),
          for (final o in obs)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(_icon[o['kind']] ?? Icons.chat_outlined, size: 18),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('${o['text']}', style: Theme.of(context).textTheme.bodyMedium),
                    if (o['fix'] != null)
                      Text('${o['fix']}',
                          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                              color: Theme.of(context).colorScheme.primary)),
                  ]),
                ),
              ]),
            ),
        ]),
      ),
    );
  }
}


/// Energy/health correlation (#38): once you connect sleep/steps, this shows what actually
/// moves your day — the correlation with your productivity. Deterministic, private.
class _HealthCard extends StatelessWidget {
  const _HealthCard({required this.health, required this.onConnect});
  final Map<String, dynamic> health;
  final Future<void> Function() onConnect;

  @override
  Widget build(BuildContext context) {
    final corrs =
        (health['correlations'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.monitor_heart_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Energy & health', style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            TextButton.icon(
              onPressed: onConnect,
              icon: const Icon(Icons.sync, size: 18),
              label: const Text('Sync'),
            ),
          ]),
          if (corrs.isEmpty)
            Text('Connect sleep & steps (Health Connect) to learn what moves your day.',
                style: Theme.of(context).textTheme.bodySmall)
          else
            for (final c in corrs)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Text('${c['insight']}', style: Theme.of(context).textTheme.bodyMedium),
              ),
        ]),
      ),
    );
  }
}


/// Peak-performance coach: the switch tax you are paying, the breaks you owe yourself, and
/// where your biological peak actually sits. Measured, not self-reported.
class _PeakCard extends StatelessWidget {
  const _PeakCard({required this.peak});
  final Map<String, dynamic> peak;

  static String _hour(int h) {
    final ampm = h < 12 ? 'am' : 'pm';
    return '${h % 12 == 0 ? 12 : h % 12}$ampm';
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final breaks = (peak['breaks'] as Map<String, dynamic>? ?? const {});
    final w = peak['peak_window'] as Map<String, dynamic>?;
    final tax = (peak['switches_per_hour'] as num?) ?? 0;
    final owed = [
      if (breaks['micro_due'] == true) 'a 10-minute step away today',
      if (breaks['meso_due'] == true) 'a couple of hours this week',
      if (breaks['macro_due'] == true) 'a half-day this month',
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.psychology_alt_outlined, size: 20),
            const SizedBox(width: 8),
            Text('Peak performance', style: Theme.of(context).textTheme.titleMedium),
          ]),
          const SizedBox(height: 10),
          Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            Text('$tax', style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                color: tax >= 12 ? scheme.error : null)),
            const SizedBox(width: 6),
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text('app switches / hour',
                  style: Theme.of(context).textTheme.labelSmall),
            ),
          ]),
          Text('${peak['switch_note']}', style: Theme.of(context).textTheme.bodySmall),
          if (w != null) ...[
            const SizedBox(height: 10),
            Row(children: [
              Icon(Icons.bolt_outlined, size: 16, color: scheme.primary),
              const SizedBox(width: 8),
              Expanded(
                child: Text('Hardest work between ${_hour(w['start'] as int)} and '
                    '${_hour(w['end'] as int)}.',
                    style: Theme.of(context).textTheme.bodyMedium),
              ),
            ]),
          ],
          if (owed.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('You owe yourself ${owed.join(', ')}.',
                style: Theme.of(context).textTheme.bodyMedium),
            Text('A stress cycle that never closes is what burns out.',
                style: Theme.of(context).textTheme.labelSmall),
          ],
        ]),
      ),
    );
  }
}
