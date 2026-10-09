package com.kemal.jarvis.access

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.widget.RemoteViews
import com.kemal.jarvis.MainActivity
import com.kemal.jarvis.R

private fun talkIntent(context: Context) = Intent(context, MainActivity::class.java).setAction(MainActivity.ACTION_TALK)
    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)

/** Ana ekran widget'ı: dokununca JARVIS açılır ve dinlemeye başlar. */
class TalkWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val pi = PendingIntent.getActivity(context, 21, talkIntent(context), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val type = PendingIntent.getActivity(context, 22, Intent(context, MainActivity::class.java).setAction(MainActivity.ACTION_TYPE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        for (id in ids) {
            val views = RemoteViews(context.packageName, R.layout.widget_talk)
            views.setOnClickPendingIntent(R.id.widget_root, pi)
            views.setOnClickPendingIntent(R.id.widget_type, type)
            manager.updateAppWidget(id, views)
        }
    }
}

/** Hızlı ayarlar kutucuğu: bildirim panelinden tek dokunuşla konuş. */
class TalkTileService : TileService() {
    override fun onStartListening() {
        qsTile?.apply { state = Tile.STATE_INACTIVE; label = "JARVIS"; if (Build.VERSION.SDK_INT >= 29) subtitle = "Konuş"; updateTile() }
    }

    // Android 13 ve öncesinde PendingIntent sürümü yok; eski yöntem yalnız orada çağrılır.
    @Suppress("DEPRECATION")
    @android.annotation.SuppressLint("StartActivityAndCollapseDeprecated")
    override fun onClick() {
        val intent = talkIntent(this)
        if (Build.VERSION.SDK_INT >= 34) startActivityAndCollapse(PendingIntent.getActivity(this, 23, intent, PendingIntent.FLAG_IMMUTABLE))
        else startActivityAndCollapse(intent)
    }
}
