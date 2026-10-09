package com.kemal.jarvis.service

import android.Manifest
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import com.kemal.jarvis.JarvisApp
import com.kemal.jarvis.R
import com.kemal.jarvis.conv.Conversation

/**
 * Konuşma sürerken JARVIS'i ön planda tutar: ekran kapansa ya da başka uygulamaya geçilse de dinleme sürer.
 * Bildirimde "Mikrofon" ve "Bitir" düğmeleri vardır.
 */
class JarvisService : Service() {
    private val app get() = JarvisApp.of(this)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        app.service = this
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { app.conversation.stop(null); return START_NOT_STICKY }
            ACTION_MIC -> app.conversation.toggleMic()
        }
        val n = build()
        try {
            val micOk = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
            if (Build.VERSION.SDK_INT >= 30 && micOk) startForeground(JarvisApp.NOTE_LISTEN, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            else startForeground(JarvisApp.NOTE_LISTEN, n)
        } catch (e: Exception) {
            // Android 14+: arka plandan mikrofon servisi başlatılamaz; konuşma ekran açıkken sürer.
            stopSelf()
        }
        return START_NOT_STICKY
    }

    fun refresh() {
        try { getSystemService(android.app.NotificationManager::class.java).notify(JarvisApp.NOTE_LISTEN, build()) } catch (_: SecurityException) {}
    }

    private fun build(): Notification {
        val s = app.conversation.state.value
        val text = when {
            s.phase == Conversation.Phase.CONNECTING -> "Bağlanıyor…"
            s.phase == Conversation.Phase.SPEAKING -> "Konuşuyor"
            s.phase == Conversation.Phase.THINKING -> "Düşünüyor"
            !s.micOn -> "Mikrofon kapalı"
            else -> "Dinliyor"
        }
        fun action(a: String, req: Int) = PendingIntent.getService(this, req, Intent(this, JarvisService::class.java).setAction(a), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, JarvisApp.CH_LISTEN)
            .setSmallIcon(R.drawable.ic_stat_jarvis)
            .setContentTitle("JARVIS")
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setContentIntent(JarvisApp.openApp(this, 2))
            .addAction(Notification.Action.Builder(null, if (s.micOn) "Mikrofonu kapat" else "Mikrofonu aç", action(ACTION_MIC, 11)).build())
            .addAction(Notification.Action.Builder(null, "Bitir", action(ACTION_STOP, 12)).build())
            .build()
    }

    override fun onDestroy() {
        if (app.service === this) app.service = null
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.kemal.jarvis.STOP"
        const val ACTION_MIC = "com.kemal.jarvis.MIC"

        fun start(context: Context) {
            try { context.startForegroundService(Intent(context, JarvisService::class.java)) } catch (_: Exception) {}
        }

        fun stop(context: Context) { context.stopService(Intent(context, JarvisService::class.java)) }
    }
}
