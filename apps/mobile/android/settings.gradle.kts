pluginManagement {
    val flutterSdkPath =
        run {
            val properties = java.util.Properties()
            file("local.properties").inputStream().use { properties.load(it) }
            val flutterSdkPath = properties.getProperty("flutter.sdk")
            require(flutterSdkPath != null) { "flutter.sdk not set in local.properties" }
            flutterSdkPath
        }

    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "9.0.1" apply false
    id("org.jetbrains.kotlin.android") version "2.3.20" apply false
}

// porcupine_flutter still declares compileSdk 31 while its own dependencies need 34+.
// Lift every plugin module to this app's compileSdk. Registered from settings so the
// afterEvaluate hook exists before Flutter's plugin loader evaluates the modules (a root
// build-script `subprojects { afterEvaluate }` arrives too late and is refused). Done by
// reflection because AGP's classes are not on the settings classpath.
gradle.beforeProject {
    afterEvaluate {
        if (path != ":app") {
            val android = extensions.findByName("android")
            if (android != null) {
                val current = runCatching {
                    android.javaClass.getMethod("getCompileSdk").invoke(android) as? Int
                }.getOrNull()
                if (current == null || current < 36) {
                    runCatching {
                        android.javaClass.getMethod("setCompileSdk", Int::class.javaObjectType)
                            .invoke(android, 36)
                    }
                }
            }
        }
    }
}

include(":app")
