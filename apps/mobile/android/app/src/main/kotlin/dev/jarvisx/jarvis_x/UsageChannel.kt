package dev.jarvisx.jarvis_x

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.os.Process
import android.provider.Settings
import io.flutter.plugin.common.MethodChannel

/**
 * The activity sampler's Android half (PLAN.md 10.6.3): which app was in the foreground,
 * from UsageStatsManager, only after the owner grants usage access. App names only —
 * there is no window title on Android and nothing here reads screen content.
 */
object UsageChannel {
    fun attach(context: Context, channel: MethodChannel) {
        channel.setMethodCallHandler { call, result ->
            when (call.method) {
                "enabled" -> result.success(granted(context))
                "openSettings" -> {
                    context.startActivity(
                        Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                    result.success(true)
                }
                "recent" -> {
                    val since = (call.argument<Number>("since") ?: 0L).toLong()
                    result.success(foregroundSince(context, since))
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun granted(context: Context): Boolean {
        val ops = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = ops.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    /** Foreground-resume events since `since` (epoch ms): [{package, app, at}]. */
    private fun foregroundSince(context: Context, since: Long): List<Map<String, Any>> {
        if (!granted(context)) return emptyList()
        val manager = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        val events = manager.queryEvents(if (since > 0) since else now - 60_000, now)
        val out = ArrayList<Map<String, Any>>()
        val event = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            if (event.eventType != UsageEvents.Event.ACTIVITY_RESUMED) continue
            val label = try {
                context.packageManager.getApplicationLabel(
                    context.packageManager.getApplicationInfo(event.packageName, 0)
                ).toString()
            } catch (_: Exception) {
                event.packageName
            }
            out.add(mapOf("package" to event.packageName, "app" to label, "at" to event.timeStamp))
            if (out.size >= 200) break
        }
        return out
    }
}
