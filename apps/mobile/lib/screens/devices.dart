import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../state/providers.dart';

/// Devices: what can act on your behalf, and how to stop it.
class DevicesScreen extends ConsumerWidget {
  const DevicesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final devices = ref.watch(devicesProvider);
    final formatter = DateFormat('d MMM, HH:mm');

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(devicesProvider),
      child: devices.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => ListView(children: [
          const SizedBox(height: 96),
          Center(child: Text('Could not load devices: $e')),
        ]),
        data: (list) => list.isEmpty
            ? ListView(children: [
                const SizedBox(height: 96),
                Icon(Icons.laptop_mac,
                    size: 48, color: Theme.of(context).disabledColor),
                const SizedBox(height: 12),
                Text('No paired Mac',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 4),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 48),
                  child: Text(
                    'Everything except Mac-local actions works without one.',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              ])
            : ListView.builder(
                itemCount: list.length,
                itemBuilder: (context, i) {
                  final device = list[i];
                  return ListTile(
                    leading: Icon(
                      Icons.laptop_mac,
                      color: device.revoked
                          ? Theme.of(context).disabledColor
                          : device.online
                              ? Colors.green.shade600
                              : Colors.orange.shade700,
                    ),
                    title: Text(device.name),
                    subtitle: Text([
                      if (device.revoked) 'Revoked' else if (device.online) 'Online' else 'Offline',
                      if (device.lastSeenAt != null)
                        'seen ${formatter.format(device.lastSeenAt!)}',
                      '${device.allowedBundleIds.length} allowed app(s)',
                    ].join(' · ')),
                    trailing: Text(device.fingerprint,
                        style: Theme.of(context).textTheme.bodySmall),
                  );
                },
              ),
      ),
    );
  }
}
