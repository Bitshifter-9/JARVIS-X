package dev.jarvisx.jarvis_x

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * Shows every push as a notification — in the foreground too, where FCM would otherwise
 * stay silent. Tapping it opens the app; the ladder's next rung fires only if this one
 * is ignored, which is the whole point of the ladder.
 */
class JarvisMessagingService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val title = message.notification?.title ?: message.data["title"] ?: "JARVIS X"
        val body = message.notification?.body ?: message.data["body"] ?: ""
        MainActivity.ensureAlertChannel(this)

        val open = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("task_id", message.data["task_id"])
            putExtra("route", message.data["route"]) // deep-link target (FEATURES-50 notifications)
        }
        val pending = PendingIntent.getActivity(
            this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, MainActivity.ALERT_CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify((System.currentTimeMillis() % Int.MAX_VALUE).toInt(), notification)
    }

    // A rotated token is picked up by the Dart side on the next launch (it asks for the
    // current token every time and re-registers when it changed).
    override fun onNewToken(token: String) = super.onNewToken(token)
}
