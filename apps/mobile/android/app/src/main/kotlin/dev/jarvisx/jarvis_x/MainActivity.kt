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
    private var navChannel: MethodChannel? = null
    private var pendingRoute: String? = null

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val route = intent.getStringExtra("route")
        if (!route.isNullOrEmpty()) {
            // App already running: tell Flutter to navigate now.
            navChannel?.invokeMethod("route", route) ?: run { pendingRoute = route }
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        ensureAlertChannel(this)
        // A notification tap carries a route; the app consumes it on start and on tap.
        pendingRoute = intent?.getStringExtra("route")
        navChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/nav")
        navChannel!!.setMethodCallHandler { call, result ->
            when (call.method) {
                "consumeInitialRoute" -> {
                    result.success(pendingRoute)
                    pendingRoute = null
                }
                else -> result.notImplemented()
            }
        }
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
        // Live alerts: one ongoing, in-place-updating "live activity" notification that
        // mirrors Jarvis's current status (FEATURES-50 live alerts).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/live").setMethodCallHandler { call, result ->
            when (call.method) {
                "show" -> {
                    showLive(
                        call.argument<String>("title") ?: "JARVIS X",
                        call.argument<String>("body") ?: "",
                        call.argument<Int>("progress"),
                    )
                    result.success(true)
                }
                "hide" -> {
                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(LIVE_ID)
                    result.success(true)
                }
                else -> result.notImplemented()
            }
        }
        // Media remote (FEATURES-50 #28): dispatch a media key to the active session.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/media").setMethodCallHandler { call, result ->
            when (call.method) {
                "key" -> {
                    val code = when (call.argument<String>("command")) {
                        "next" -> android.view.KeyEvent.KEYCODE_MEDIA_NEXT
                        "previous" -> android.view.KeyEvent.KEYCODE_MEDIA_PREVIOUS
                        "play" -> android.view.KeyEvent.KEYCODE_MEDIA_PLAY
                        "pause" -> android.view.KeyEvent.KEYCODE_MEDIA_PAUSE
                        else -> android.view.KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
                    }
                    val am = getSystemService(Context.AUDIO_SERVICE) as android.media.AudioManager
                    am.dispatchMediaKeyEvent(android.view.KeyEvent(android.view.KeyEvent.ACTION_DOWN, code))
                    am.dispatchMediaKeyEvent(android.view.KeyEvent(android.view.KeyEvent.ACTION_UP, code))
                    result.success(true)
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

    private fun showLive(title: String, body: String, progress: Int?) {
        ensureLiveChannel(this)
        val open = Intent(this, MainActivity::class.java)
            .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val pending = android.app.PendingIntent.getActivity(
            this, 1, open,
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = androidx.core.app.NotificationCompat.Builder(this, LIVE_CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setColorized(true)
            .setCategory(androidx.core.app.NotificationCompat.CATEGORY_STATUS)
            .setPriority(androidx.core.app.NotificationCompat.PRIORITY_LOW)
            .setContentIntent(pending)
        if (progress != null) builder.setProgress(100, progress.coerceIn(0, 100), progress < 0)
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(LIVE_ID, builder.build())
    }

    private fun isAccessibilityEnabled(): Boolean {
        val flat = Settings.Secure.getString(
            contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        return flat.contains("$packageName/.WhatsAppSendService")
    }

    companion object {
        const val ALERT_CHANNEL = "jarvis_alerts"
        const val LIVE_CHANNEL = "jarvis_live"
        const val LIVE_ID = 42

        fun ensureLiveChannel(context: Context) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                if (manager.getNotificationChannel(LIVE_CHANNEL) == null) {
                    val channel = NotificationChannel(
                        LIVE_CHANNEL, "Live status", NotificationManager.IMPORTANCE_LOW
                    ).apply {
                        description = "What JARVIS X is doing right now"
                        setShowBadge(false)
                    }
                    manager.createNotificationChannel(channel)
                }
            }
        }

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
