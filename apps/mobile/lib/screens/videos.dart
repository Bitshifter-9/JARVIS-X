import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../state/providers.dart';

/// The YouTube pipeline, observable: every render and upload job with its status.
///
/// Publishing still goes through Approvals — this screen only starts renders and
/// shows what happened.
class VideosScreen extends ConsumerWidget {
  const VideosScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final runs = ref.watch(videoRunsProvider);

    return Scaffold(
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(videoRunsProvider),
        child: runs.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => ListView(children: [
            const SizedBox(height: 96),
            Center(child: Text('Could not load video runs: $e')),
          ]),
          data: (list) => ListView(children: [
            const _AssetsBanner(),
            const _AnalyticsCard(),
            if (list.isEmpty) ...[
              const SizedBox(height: 64),
              Icon(Icons.movie_outlined,
                  size: 48, color: Theme.of(context).disabledColor),
              const SizedBox(height: 12),
              Text('No videos yet — tap + to render one',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleMedium),
            ] else
              ...list.map((run) => _RunCard(run: run)),
          ]),
        ),
      ),
      floatingActionButton: FloatingActionButton(
        tooltip: 'Render a new Short',
        onPressed: () => _promptTopic(context, ref),
        child: const Icon(Icons.add),
      ),
    );
  }

  Future<void> _promptTopic(BuildContext context, WidgetRef ref) async {
    final controller = TextEditingController();
    final topic = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('New Short'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
            labelText: 'Topic',
            hintText: 'e.g. This week in AI chips',
          ),
          onSubmitted: (v) => Navigator.pop(context, v),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, controller.text),
              child: const Text('Render')),
        ],
      ),
    );
    if (topic == null || topic.trim().length < 3) return;

    try {
      await ref.read(clientProvider).generateVideo(topic.trim());
      ref.invalidate(videoRunsProvider);
    } on ProblemException catch (e) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }
}

/// Whether renders will use the user's cloned voice and lip-synced avatar.
/// Tap to upload the voice sample, hero video, or background music.
class _AssetsBanner extends ConsumerWidget {
  const _AssetsBanner();

  static const _kinds = [
    ('voice', 'Voice sample (~15s clean speech)', Icons.record_voice_over,
        ['wav', 'mp3', 'm4a']),
    ('avatar', 'Face video (silent, facing camera)', Icons.face,
        ['mp4', 'mov']),
    ('music', 'Background music (royalty-free)', Icons.music_note,
        ['mp3', 'wav', 'm4a']),
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final assets = ref.watch(videoAssetsProvider);
    return assets.when(
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (status) {
        final voiceReady = status['voice_clone_ready'] == true;
        final lipsync = status['lipsync_configured'] == true;
        final map = status['assets'] as Map<String, dynamic>? ?? {};
        final hasAvatar = map['avatar'] != null;
        final hasMusic = map['music'] != null;
        return Card(
          margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: InkWell(
            onTap: () => _uploadSheet(context, ref),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Row(children: [
                Icon(voiceReady ? Icons.record_voice_over : Icons.voice_over_off,
                    size: 18,
                    color:
                        voiceReady ? Colors.green : Theme.of(context).disabledColor),
                const SizedBox(width: 6),
                Text(voiceReady ? 'Your voice' : 'Stock voice',
                    style: Theme.of(context).textTheme.bodySmall),
                const SizedBox(width: 12),
                Icon(hasAvatar && lipsync ? Icons.face : Icons.face_retouching_off,
                    size: 18,
                    color: hasAvatar && lipsync
                        ? Colors.green
                        : Theme.of(context).disabledColor),
                const SizedBox(width: 6),
                Text(hasAvatar && lipsync ? 'Your avatar' : 'B-roll visuals',
                    style: Theme.of(context).textTheme.bodySmall),
                const SizedBox(width: 12),
                Icon(Icons.music_note,
                    size: 18,
                    color:
                        hasMusic ? Colors.green : Theme.of(context).disabledColor),
                const Spacer(),
                const Icon(Icons.upload_file, size: 18),
              ]),
            ),
          ),
        );
      },
    );
  }

  Future<void> _uploadSheet(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheet) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const ListTile(
              title: Text('Upload an asset'),
              subtitle: Text('Re-uploading a kind replaces the old file.')),
          for (final (kind, label, icon, extensions) in _kinds)
            ListTile(
              leading: Icon(icon),
              title: Text(label),
              onTap: () {
                Navigator.pop(sheet);
                _pickAndUpload(context, ref, kind, extensions);
              },
            ),
        ]),
      ),
    );
  }

  Future<void> _pickAndUpload(BuildContext context, WidgetRef ref, String kind,
      List<String> extensions) async {
    final file = await openFile(acceptedTypeGroups: [
      XTypeGroup(label: kind, extensions: extensions),
    ]);
    if (file == null) return;
    final bytes = await file.readAsBytes();

    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Uploading ${file.name}…')));
    try {
      await ref.read(clientProvider).uploadAsset(kind, file.name, bytes);
      ref.invalidate(videoAssetsProvider);
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('$kind uploaded — the next render will use it')));
    } on ProblemException catch (e) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }
}

/// 28-day channel numbers from the latest analytics sweep. Hidden until one ran.
class _AnalyticsCard extends ConsumerWidget {
  const _AnalyticsCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final analytics = ref.watch(videoAnalyticsProvider);
    return analytics.when(
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (data) {
        if (data['available'] != true) return const SizedBox.shrink();
        final totals = data['totals'] as Map<String, dynamic>? ?? {};
        final videos = (data['videos'] as List<dynamic>? ?? []).take(3);
        return Card(
          margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  const Icon(Icons.insights_outlined, size: 18),
                  const SizedBox(width: 8),
                  Text('Last ${data['window_days'] ?? 28} days',
                      style: Theme.of(context).textTheme.titleSmall),
                  const Spacer(),
                  IconButton(
                    tooltip: 'Refresh analytics',
                    icon: const Icon(Icons.refresh, size: 18),
                    onPressed: () async {
                      await ref.read(clientProvider).refreshAnalytics();
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                            content: Text('Refresh queued — pull down shortly')));
                      }
                    },
                  ),
                ]),
                Text(
                  '${totals['views'] ?? 0} views · '
                  '${totals['watch_minutes'] ?? 0} watch-min · '
                  '${totals['avg_view_seconds'] ?? 0}s avg view',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                for (final v in videos)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text('• ${v['title']} — ${v['views']} views',
                        style: Theme.of(context).textTheme.bodySmall,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _RunCard extends ConsumerWidget {
  const _RunCard({required this.run});

  final Map<String, dynamic> run;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final kind = run['kind'] as String? ?? '';
    final status = run['status'] as String? ?? '';
    final result = run['result'] as Map<String, dynamic>?;
    final error = run['error'] as String?;
    final isUpload = kind == 'youtube.upload';
    final actionId = result?['action_id'] as String?;

    final labels = {
      'youtube.generate': 'Render',
      'youtube.upload': 'Upload',
      'youtube.reply': 'Comment reply',
      'youtube.analytics': 'Analytics',
      'youtube.comments': 'Comment sweep',
    };
    final subtitle = <String>[
      if (run['topic'] != null) run['topic'] as String,
      if (result?['title'] != null) result!['title'] as String,
      if (result?['url'] != null) result!['url'] as String,
      if (result?['video_path'] != null) 'Saved: ${result!['video_path']}',
      if (result?['skipped'] != null) 'Skipped: ${result!['skipped']}',
      if (status != 'succeeded' && error != null) error,
    ].join('\n');

    final isFinishedRender = kind == 'youtube.generate' && status == 'succeeded';

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: ListTile(
        leading: Icon(isUpload ? Icons.cloud_upload_outlined : Icons.movie_outlined),
        title: Row(children: [
          Expanded(child: Text(labels[kind] ?? kind)),
          _StatusChip(status: status),
        ]),
        subtitle: subtitle.isEmpty ? null : Text(subtitle),
        isThreeLine: subtitle.contains('\n'),
        trailing: isFinishedRender
            ? Row(mainAxisSize: MainAxisSize.min, children: [
                if (actionId != null)
                  IconButton(
                    tooltip: 'Watch the rendered video',
                    icon: const Icon(Icons.play_circle_outline),
                    onPressed: () => _open(context, ref, actionId),
                  ),
                IconButton(
                  tooltip: 'Publish: propose the upload (again)',
                  icon: const Icon(Icons.cloud_upload_outlined),
                  onPressed: () => _publish(context, ref),
                ),
              ])
            : null,
      ),
    );
  }

  Future<void> _open(BuildContext context, WidgetRef ref, String actionId) async {
    try {
      final url = await ref.read(clientProvider).videoPreviewUrl(actionId);
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } on ProblemException catch (e) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _publish(BuildContext context, WidgetRef ref) async {
    try {
      await ref.read(clientProvider).publishVideo(run['id'] as String);
      ref.invalidate(videoRunsProvider);
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Upload proposed — decide it in Approvals')));
    } on ProblemException catch (e) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = switch (status) {
      'succeeded' => Colors.green,
      'running' => scheme.primary,
      'pending' => scheme.outline,
      _ => scheme.error,
    };
    return Chip(
      label: Text(status, style: TextStyle(fontSize: 11, color: color)),
      side: BorderSide(color: color),
      visualDensity: VisualDensity.compact,
      backgroundColor: Colors.transparent,
    );
  }
}
