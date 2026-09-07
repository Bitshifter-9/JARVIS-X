package dev.jarvisx.jarvis_x

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Push, without a Flutter plugin: Firebase is initialised from options the server hands
 * the app after sign-in, so nothing account-specific is compiled into the APK.
 */
class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        ensureAlertChannel(this)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/push").setMethodCallHandler { call, result ->
            when (call.method) {
                "init" -> {
                    try {
                        if (FirebaseApp.getApps(this).isEmpty()) {
                            val options = FirebaseOptions.Builder()
                                .setApiKey(call.argument<String>("apiKey")!!)
                                .setApplicationId(call.argument<String>("appId")!!)
                                .setGcmSenderId(call.argument<String>("senderId")!!)
                                .setProjectId(call.argument<String>("projectId")!!)
                                .build()
                            FirebaseApp.initializeApp(this, options)
                        }
                        result.success(true)
                    } catch (e: Exception) {
                        result.error("init_failed", e.message, null)
                    }
                }
                "token" -> {
                    if (FirebaseApp.getApps(this).isEmpty()) {
                        result.error("not_initialised", "call init first", null)
                    } else {
                        FirebaseMessaging.getInstance().token
                            .addOnSuccessListener { result.success(it) }
                            .addOnFailureListener { result.error("token_failed", it.message, null) }
                    }
                }
                else -> result.notImplemented()
            }
        }
        // WhatsApp auto-send: arm the accessibility service for one tap (PLAN.md 12.8).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/whatsapp").setMethodCallHandler { call, result ->
            when (call.method) {
                "enabled" -> result.success(isAccessibilityEnabled())
                "openSettings" -> {
                    startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    result.success(true)
                }
                "send" -> {
                    if (!isAccessibilityEnabled()) { result.success(false); return@setMethodCallHandler }
                    WhatsAppSendService.clicked = false
                    WhatsAppSendService.armed = true
                    // Give the service a few seconds to see the chat and tap Send.
                    android.os.Handler(mainLooper).postDelayed({
                        WhatsAppSendService.armed = false
                        result.success(WhatsAppSendService.clicked)
                    }, 4500)
                }
                else -> result.notImplemented()
            }
        }
        // The activity sampler: foreground apps from UsageStatsManager (PLAN.md 10.6.3).
        UsageChannel.attach(this, MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/usage"))
        // The notification mirror: Dart polls what the listener queued (PLAN.md 10.4.4).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/notifications").setMethodCallHandler { call, result ->
            when (call.method) {
                "enabled" -> result.success(
                    NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)
                )
                "openSettings" -> {
                    startActivity(
                        Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS")
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                    result.success(true)
                }
                "drain" -> result.success(JarvisNotificationListener.drain())
                else -> result.notImplemented()
            }
        }
    }

    private fun isAccessibilityEnabled(): Boolean {
        val flat = Settings.Secure.getString(
            contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        return flat.contains("$packageName/.WhatsAppSendService")
    }

    companion object {
        const val ALERT_CHANNEL = "jarvis_alerts"

        fun ensureAlertChannel(context: Context) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                if (manager.getNotificationChannel(ALERT_CHANNEL) == null) {
                    val channel = NotificationChannel(
                        ALERT_CHANNEL, "Deadline alerts", NotificationManager.IMPORTANCE_HIGH
                    ).apply { description = "JARVIS X deadline and approval alerts" }
                    manager.createNotificationChannel(channel)
                }
            }
        }
    }
}
