import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// The design system, in one file.
///
/// Warm and calm like Claude — bone paper and clay in the light, warm charcoal in the
/// dark — with one cinematic exception: the voice orb glows electric (`voice`), so the
/// app stays quiet while the moment you actually talk to Jarvis feels alive. Everything
/// reads colours from `Theme.of(context)`, never from a literal, so the two stay in step.
class JarvisColors {
  // Warm neutrals (dark).
  static const ink = Color(0xFF1E1D1B); // warm charcoal background
  static const ink2 = Color(0xFF272522); // raised surface
  static const ink3 = Color(0xFF322F2B); // higher surface
  // Warm neutrals (light) — Claude's "bone".
  static const paper = Color(0xFFFAF9F5);
  static const paper2 = Color(0xFFFFFFFF);
  static const paper3 = Color(0xFFF0EEE7);
  // Clay accent.
  static const clay = Color(0xFFC96442); // accent on light
  static const clayLight = Color(0xFFE08363); // accent on dark
  // Cinematic voice accent (the orb).
  static const voice = Color(0xFF22D3EE);
  // Retained aliases so existing widgets keep compiling.
  static const cyan = voice;
  static const sky = clayLight;
  static const mist = Color(0xFFF3E9E3);
  static const success = Color(0xFF4FB477);
  static const warning = Color(0xFFE0A32E);
  static const danger = Color(0xFFDE6C5A);
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: JarvisColors.clay,
    brightness: brightness,
    primary: dark ? JarvisColors.clayLight : JarvisColors.clay,
    onPrimary: dark ? const Color(0xFF241A16) : Colors.white,
    secondary: dark ? JarvisColors.clayLight : const Color(0xFFA24E32),
    surface: dark ? JarvisColors.ink : JarvisColors.paper,
    onSurface: dark ? const Color(0xFFECE9E2) : const Color(0xFF2A2723),
    error: JarvisColors.danger,
  );

  final base = ThemeData(brightness: brightness, colorScheme: scheme, useMaterial3: true);
  // Serif for the big type (Claude's literary feel), clean sans for everything else.
  final serif = GoogleFonts.fraunces;
  final text = GoogleFonts.interTextTheme(base.textTheme).copyWith(
    displaySmall: serif(fontSize: 34, fontWeight: FontWeight.w600, letterSpacing: -0.5),
    headlineSmall: serif(fontSize: 25, fontWeight: FontWeight.w600, letterSpacing: -0.3),
    titleLarge: serif(fontSize: 21, fontWeight: FontWeight.w600, letterSpacing: -0.2),
    titleMedium: GoogleFonts.inter(fontSize: 16, fontWeight: FontWeight.w600),
    labelLarge: GoogleFonts.inter(fontSize: 14, fontWeight: FontWeight.w600),
  );

  final surface2 = dark ? JarvisColors.ink2 : JarvisColors.paper2;
  final surface3 = dark ? JarvisColors.ink3 : JarvisColors.paper3;
  final shape = RoundedRectangleBorder(borderRadius: BorderRadius.circular(20));

  return base.copyWith(
    textTheme: text,
    scaffoldBackgroundColor: scheme.surface,
    splashFactory: InkSparkle.splashFactory,
    pageTransitionsTheme: PageTransitionsTheme(builders: {
      TargetPlatform.android: const FadeForwardsPageTransitionsBuilder(),
      TargetPlatform.macOS: const FadeForwardsPageTransitionsBuilder(),
    }),
    appBarTheme: AppBarTheme(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      centerTitle: false,
      titleTextStyle: text.titleLarge?.copyWith(color: scheme.onSurface),
      foregroundColor: scheme.onSurface,
    ),
    cardTheme: CardThemeData(
      color: surface2,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      shape: shape.copyWith(
        side: BorderSide(color: scheme.onSurface.withValues(alpha: dark ? 0.06 : 0.08)),
      ),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
    ),
    listTileTheme: ListTileThemeData(
      iconColor: scheme.primary,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
    chipTheme: ChipThemeData(
      backgroundColor: surface3,
      side: BorderSide.none,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      labelStyle: text.labelLarge?.copyWith(color: scheme.onSurface),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: surface2,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: scheme.onSurface.withValues(alpha: 0.08)),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: scheme.onSurface.withValues(alpha: 0.08)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: scheme.primary, width: 1.5),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        textStyle: text.labelLarge,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        side: BorderSide(color: scheme.onSurface.withValues(alpha: 0.18)),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: surface2,
      indicatorColor: scheme.primary.withValues(alpha: 0.16),
      surfaceTintColor: Colors.transparent,
      height: 68,
      labelTextStyle: WidgetStatePropertyAll(text.labelSmall?.copyWith(fontWeight: FontWeight.w600)),
      iconTheme: WidgetStateProperty.resolveWith((states) => IconThemeData(
            color: states.contains(WidgetState.selected) ? scheme.primary : scheme.onSurface.withValues(alpha: 0.7),
          )),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: surface2,
      indicatorColor: scheme.primary.withValues(alpha: 0.16),
      selectedIconTheme: IconThemeData(color: scheme.primary),
      unselectedIconTheme: IconThemeData(color: scheme.onSurface.withValues(alpha: 0.7)),
      selectedLabelTextStyle: text.labelLarge?.copyWith(color: scheme.primary),
      unselectedLabelTextStyle: text.labelLarge?.copyWith(color: scheme.onSurface.withValues(alpha: 0.75)),
    ),
    dividerTheme: DividerThemeData(color: scheme.onSurface.withValues(alpha: 0.08), space: 1),
    popupMenuTheme: PopupMenuThemeData(
      color: surface2,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: scheme.onSurface.withValues(alpha: 0.08)),
      ),
      textStyle: text.bodyMedium?.copyWith(color: scheme.onSurface),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(
        color: dark ? JarvisColors.ink3 : const Color(0xFF2A2723),
        borderRadius: BorderRadius.circular(10),
      ),
      textStyle: text.labelSmall?.copyWith(color: Colors.white),
      waitDuration: const Duration(milliseconds: 400),
    ),
    segmentedButtonTheme: SegmentedButtonThemeData(
      style: SegmentedButton.styleFrom(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        side: BorderSide(color: scheme.onSurface.withValues(alpha: 0.12)),
        selectedBackgroundColor: scheme.primary.withValues(alpha: 0.18),
        selectedForegroundColor: scheme.primary,
      ),
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: surface2,
      surfaceTintColor: Colors.transparent,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      showDragHandle: true,
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: surface2,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      backgroundColor: dark ? JarvisColors.ink3 : const Color(0xFF2A2723),
      contentTextStyle: text.bodyMedium?.copyWith(color: Colors.white),
    ),
    sliderTheme: SliderThemeData(activeTrackColor: scheme.primary, thumbColor: scheme.primary),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected) ? scheme.onPrimary : null),
      trackColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected) ? scheme.primary : null),
    ),
    extensions: [JarvisSurfaces(surface2: surface2, surface3: surface3)],
  );
}

/// The two elevated surfaces, as a theme extension so widgets never hard-code them.
class JarvisSurfaces extends ThemeExtension<JarvisSurfaces> {
  const JarvisSurfaces({required this.surface2, required this.surface3});

  final Color surface2;
  final Color surface3;

  @override
  JarvisSurfaces copyWith({Color? surface2, Color? surface3}) =>
      JarvisSurfaces(surface2: surface2 ?? this.surface2, surface3: surface3 ?? this.surface3);

  @override
  JarvisSurfaces lerp(JarvisSurfaces? other, double t) => other == null
      ? this
      : JarvisSurfaces(
          surface2: Color.lerp(surface2, other.surface2, t)!,
          surface3: Color.lerp(surface3, other.surface3, t)!,
        );
}

extension JarvisTheme on BuildContext {
  JarvisSurfaces get surfaces =>
      Theme.of(this).extension<JarvisSurfaces>() ??
      const JarvisSurfaces(surface2: JarvisColors.ink2, surface3: JarvisColors.ink3);
}
