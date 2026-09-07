package dev.jarvisx.jarvis_x

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.util.ArrayDeque

/**
 * The notification mirror (PLAN.md 10.4.4). Opt-in: the user grants "notification
 * access" in system settings, and the Dart side drains this queue and forwards only the
 * apps they allowlisted — titles by default, bodies only where they said so. Nothing
 * here talks to the network.
 */
class JarvisNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName == packageName || sbn.isOngoing) return
        val extras = sbn.notification.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
        if (title.isBlank() && text.isBlank()) return
        val app = try {
            packageManager.getApplicationLabel(
                packageManager.getApplicationInfo(sbn.packageName, 0)
            ).toString()
        } catch (_: Exception) {
            sbn.packageName
        }
        synchronized(queue) {
            queue.addLast(
                mapOf(
                    "package" to sbn.packageName,
                    "app" to app,
                    "title" to title,
                    "text" to text,
                    "at" to java.time.Instant.ofEpochMilli(sbn.postTime).toString(),
                    "key" to "${sbn.packageName}:${sbn.postTime}:${sbn.id}",
                )
            )
            while (queue.size > 200) queue.removeFirst()
        }
    }

    companion object {
        val queue = ArrayDeque<Map<String, String>>()

        fun drain(): List<Map<String, String>> = synchronized(queue) {
            val out = queue.toList()
            queue.clear()
            out
        }
    }
}
