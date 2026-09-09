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
        // A messaging app posts a group *summary* ("3 new messages") alongside each real
        // notification. Forwarding both is why one message arrived in JARVIS twice.
        if (sbn.notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
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
                    // sbn.key is Android's own identity for the notification *slot*
                    // (user|package|id|tag). The old key mixed in postTime, so every
                    // update of the same chat looked like a brand-new notification —
                    // the server could never recognise the repeat. Content is hashed
                    // server-side, so a genuine new message still gets through.
                    "key" to sbn.key,
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
