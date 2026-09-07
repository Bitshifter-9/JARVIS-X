package dev.jarvisx.jarvis_x

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * WhatsApp auto-send (PLAN.md 12.8). Off until the user enables it in system settings.
 * When the Dart node asks (armed = true) and a WhatsApp window appears, it finds the
 * Send button by id/description and clicks it once, then disarms. It reads nothing else
 * and does nothing when not armed — so it cannot send on its own.
 */
class WhatsAppSendService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (!armed) return
        val root = rootInActiveWindow ?: return
        if (root.packageName?.toString()?.startsWith("com.whatsapp") != true) return
        val send = findSend(root) ?: return
        if (send.isClickable) {
            send.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            armed = false
            clicked = true
        }
    }

    override fun onInterrupt() {}

    private fun findSend(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        for (id in listOf("com.whatsapp:id/send", "com.whatsapp.w4b:id/send")) {
            root.findAccessibilityNodeInfosByViewId(id).firstOrNull()?.let { return it }
        }
        // Fall back to the content description WhatsApp gives the send button.
        for (desc in listOf("Send", "send")) {
            root.findAccessibilityNodeInfosByText(desc).firstOrNull { it.isClickable }?.let { return it }
        }
        return null
    }

    companion object {
        /** The Dart side sets this true just before it opens the chat, false otherwise. */
        @Volatile var armed = false
        @Volatile var clicked = false
    }
}
