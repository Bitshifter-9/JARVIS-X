import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'screens/home.dart';
import 'screens/sign_in.dart';
import 'state/providers.dart';

void main() => runApp(const ProviderScope(child: JarvisApp()));

class JarvisApp extends ConsumerStatefulWidget {
  const JarvisApp({super.key});

  @override
  ConsumerState<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends ConsumerState<JarvisApp> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => ref.read(authProvider.notifier).restore(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final signedIn = ref.watch(authProvider).signedIn;

    return MaterialApp(
      title: 'JARVIS X',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF2F81F7)),
        useMaterial3: true,
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2F81F7),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: signedIn ? const HomeScreen() : const SignInScreen(),
    );
  }
}
