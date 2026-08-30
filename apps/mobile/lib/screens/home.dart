import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/providers.dart';
import 'approvals.dart';
import 'devices.dart';
import 'goals.dart';
import 'today.dart';

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  int _index = 0;

  static const _screens = [TodayScreen(), GoalsScreen(), ApprovalsScreen(), DevicesScreen()];
  static const _titles = ['Today', 'Goals', 'Approvals', 'Devices'];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_index]),
        actions: [
          IconButton(
            key: const Key('killSwitch'),
            tooltip: 'Pause JARVIS',
            icon: const Icon(Icons.stop_circle_outlined),
            onPressed: _confirmPause,
          ),
          IconButton(
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(authProvider.notifier).signOut(),
          ),
        ],
      ),
      body: _screens[_index],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.today_outlined), label: 'Today'),
          NavigationDestination(icon: Icon(Icons.flag_outlined), label: 'Goals'),
          NavigationDestination(icon: Icon(Icons.verified_user_outlined), label: 'Approvals'),
          NavigationDestination(icon: Icon(Icons.devices_outlined), label: 'Devices'),
        ],
      ),
    );
  }

  Future<void> _confirmPause() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Pause JARVIS?'),
        content: const Text(
          'Queued work is cancelled and every device session is revoked. '
          'Evidence is never deleted.',
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true), child: const Text('Pause')),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final result = await ref.read(clientProvider).pause();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          'Paused. ${result['jobs_cancelled']} job(s) cancelled, '
          '${result['sessions_revoked']} session(s) revoked.',
        ),
      ),
    );
  }
}
