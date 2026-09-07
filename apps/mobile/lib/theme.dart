import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// The design system, in one file.
///
/// Dark-first: the icon's ink and cyan. Light is a real theme too, not an afterthought —
/// the same accent, the same shapes, paper instead of ink. Everything else in the app
/// reads colours from `Theme.of(context)`, never from a literal, so the two stay in step.
class JarvisColors {
  static const ink = Color(0xFF0B1220);
  static const ink2 = Color(0xFF111A2E);
  static const ink3 = Color(0xFF18233B);
  static const cyan = Color(0xFF38BDF8);
  static const sky = Color(0xFF7DD3FC);
  static const mist = Color(0xFFE0F2FE);
  static const paper = Color(0xFFF6F8FC);
  static const paper2 = Color(0xFFFFFFFF);
  static const paper3 = Color(0xFFEAF0F8);
  static const success = Color(0xFF34D399);
  static const warning = Color(0xFFFBBF24);
  static const danger = Color(0xFFF87171);
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: JarvisColors.cyan,
    brightness: brightness,
    primary: dark ? JarvisColors.sky : const Color(0xFF0369A1),
    onPrimary: dark ? JarvisColors.ink : Colors.white,
    secondary: dark ? JarvisColors.mist : const Color(0xFF0E7490),
    surface: dark ? JarvisColors.ink : JarvisColors.paper,
    onSurface: dark ? const Color(0xFFE6EDF3) : const Color(0xFF0F172A),
    error: JarvisColors.danger,
  );

  final base = ThemeData(brightness: brightness, colorScheme: scheme, useMaterial3: true);
  final text = GoogleFonts.interTextTheme(base.textTheme).copyWith(
    displaySmall: GoogleFonts.inter(fontSize: 34, fontWeight: FontWeight.w700, letterSpacing: -1),
    headlineSmall: GoogleFonts.inter(fontSize: 24, fontWeight: FontWeight.w700, letterSpacing: -0.6),
    titleLarge: GoogleFonts.inter(fontSize: 20, fontWeight: FontWeight.w600, letterSpacing: -0.3),
    titleMedium: GoogleFonts.inter(fontSize: 16, fontWeight: FontWeight.w600),
    labelLarge: GoogleFonts.inter(fontSize: 14, fontWeight: FontWeight.w600),
  );

  final surface2 = dark ? JarvisColors.ink2 : JarvisColors.paper2;
  final surface3 = dark ? JarvisColors.ink3 : JarvisColors.paper3;
  final shape = RoundedRectangleBorder(borderRadius: BorderRadius.circular(18));

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
        color: dark ? JarvisColors.ink3 : const Color(0xFF0F172A),
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
      backgroundColor: dark ? JarvisColors.ink3 : const Color(0xFF0F172A),
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
