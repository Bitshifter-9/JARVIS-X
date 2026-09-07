package dev.jarvisx.jarvis_x

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * Home-screen widget: the next deadline (FEATURES-50 #48). The Dart side writes
 * "next_deadline" / "next_deadline_sub" via home_widget; this paints them. Tapping it
 * opens the app.
 */
class NextDeadlineWidget : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: android.content.SharedPreferences,
    ) {
        for (id in appWidgetIds) {
            val views = RemoteViews(context.packageName, R.layout.next_deadline_widget)
            val title = widgetData.getString("next_deadline", null) ?: "No deadlines"
            val sub = widgetData.getString("next_deadline_sub", null) ?: "You're all clear"
            views.setTextViewText(R.id.widget_title, title)
            views.setTextViewText(R.id.widget_sub, sub)

            views.setOnClickPendingIntent(R.id.widget_root, fallbackIntent(context))
            appWidgetManager.updateAppWidget(id, views)
        }
    }

    private fun fallbackIntent(context: Context): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
        return PendingIntent.getActivity(
            context, 0, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
