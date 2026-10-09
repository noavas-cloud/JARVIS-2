package com.kemal.jarvis

import android.app.Activity
import android.app.Application
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import com.kemal.jarvis.conv.Conversation
import com.kemal.jarvis.core.Memory
import com.kemal.jarvis.core.SecureStore
import com.kemal.jarvis.core.Settings
import com.kemal.jarvis.mac.MacLink
import com.kemal.jarvis.service.JarvisService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import java.util.concurrent.atomic.AtomicInteger

/** Ekran açıkken izin isteyebilen taraf (MainActivity). */
fun interface PermissionAsker { suspend fun ask(permission: String): Boolean }

class JarvisApp : Application() {
    lateinit var store: SecureStore; private set
    lateinit var settings: Settings; private set
    lateinit var memory: Memory; private set
    lateinit var mac: MacLink; private set
    lateinit var conversation: Conversation; private set
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** JARVIS ekranı görünür mü (onStart–onStop). */
    @Volatile var uiVisible = false
    @Volatile var resumed: Activity? = null
    /** Görünür (onStart–onStop) etkinlik: izin penceresi kapanırken bir an "resumed" boş kalır. */
    @Volatile var visible: Activity? = null
    @Volatile var permissionAsker: PermissionAsker? = null
    @Volatile var service: JarvisService? = null
    /** 1.x'ten taşınan bilgi varsa ilk açılışta bir kez gösterilir. */
    @Volatile var migratedNotice: String? = null

    override fun onCreate() {
        super.onCreate()
        store = SecureStore(this)
        settings = Settings(this)
        memory = Memory(store)
        val moved = store.migrateFromV1()
        if (moved.isNotEmpty()) migratedNotice = "JARVIS 2.0'a hoş geldin. Önceki sürümdeki ${moved.joinToString(" ve ")} korundu." +
            if ("Mac eşleşmen" in moved) " (Mac'te JARVIS TELEFON yeniden başlatıldıysa adres değişmiştir; o zaman yeniden eşleştir.)" else ""
        mac = MacLink(this, store, memory, appScope, devHttp = BuildConfig.DEV_ENDPOINTS)
        conversation = Conversation(this)
        mac.onCallRequest = { conversation.handleMacCall(it) }
        createChannels()
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            private var started = 0
            override fun onActivityStarted(a: Activity) { started++; uiVisible = true; visible = a }
            override fun onActivityStopped(a: Activity) { started = maxOf(0, started - 1); uiVisible = started > 0; if (visible === a) visible = null }
            override fun onActivityResumed(a: Activity) { resumed = a }
            override fun onActivityPaused(a: Activity) { if (resumed === a) resumed = null }
            override fun onActivityCreated(a: Activity, b: Bundle?) {}
            override fun onActivitySaveInstanceState(a: Activity, b: Bundle) {}
            override fun onActivityDestroyed(a: Activity) {}
        })
    }

    /**
     * Başka bir uygulamayı açar. JARVIS ekranı açıksa (ya da "diğer uygulamaların üzerinde göster" izni varsa) hemen açar;
     * değilse Android arka plandan açmaya izin vermediği için dokununca açılan bir bildirim gösterir ve false döner.
     */
    fun launch(intent: Intent, label: String): Boolean {
        val activity = resumed ?: visible
        if (activity != null) { activity.startActivity(intent); return true }
        if (AndroidSettings.canDrawOverlays(this)) {
            startActivity(Intent(intent).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); return true
        }
        val pi = PendingIntent.getActivity(this, tapIds.incrementAndGet(), Intent(intent).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        notify(this, NOTE_TAP, Notification.Builder(this, CH_ALERT).setSmallIcon(R.drawable.ic_stat_jarvis)
            .setContentTitle("JARVIS · $label").setContentText("Açmak için dokun").setContentIntent(pi).setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER).build())
        return false
    }

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannels(listOf(
            NotificationChannel(CH_LISTEN, "JARVIS konuşması", NotificationManager.IMPORTANCE_LOW).apply {
                description = "JARVIS dinlerken görünen kalıcı bildirim"; setShowBadge(false)
            },
            NotificationChannel(CH_ALERT, "Onaylar ve açılacak ekranlar", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Arama/işlem onayları ve dokununca açılan uygulamalar"
            },
            NotificationChannel(CH_CALL, "Ara ve söyle", NotificationManager.IMPORTANCE_LOW),
            NotificationChannel(CH_INFO, "Bilgilendirme", NotificationManager.IMPORTANCE_DEFAULT),
        ))
    }

    companion object {
        const val CH_LISTEN = "jarvis_listen"
        const val CH_ALERT = "jarvis_alert"
        const val CH_CALL = "jarvis_call"
        const val CH_INFO = "jarvis_info"
        const val NOTE_LISTEN = 4001
        private const val NOTE_CONFIRM = 4002
        private const val NOTE_TAP = 4003
        private const val NOTE_INFO = 4004
        private val tapIds = AtomicInteger(100)

        fun of(context: Context) = context.applicationContext as JarvisApp

        private fun notify(context: Context, id: Int, n: Notification) {
            try { context.getSystemService(NotificationManager::class.java).notify(id, n) } catch (_: SecurityException) {}
        }

        fun notifyInfo(context: Context, title: String, text: String) = notify(context, NOTE_INFO,
            Notification.Builder(context, CH_INFO).setSmallIcon(R.drawable.ic_stat_jarvis).setContentTitle(title)
                .setContentText(text).setStyle(Notification.BigTextStyle().bigText(text)).setAutoCancel(true)
                .setContentIntent(openApp(context, 0)).build())

        fun showConfirmNotice(context: Context, title: String, text: String) = notify(context, NOTE_CONFIRM,
            Notification.Builder(context, CH_ALERT).setSmallIcon(R.drawable.ic_stat_jarvis)
                .setContentTitle("JARVIS · $title").setContentText(if (text.isEmpty()) "Onaylamak için dokun" else "$text — onaylamak için dokun")
                .setCategory(Notification.CATEGORY_CALL).setAutoCancel(true).setContentIntent(openApp(context, 1)).build())

        fun cancelConfirmNotice(context: Context) = context.getSystemService(NotificationManager::class.java).cancel(NOTE_CONFIRM)

        fun openApp(context: Context, request: Int, action: String? = null): PendingIntent =
            PendingIntent.getActivity(context, request, Intent(context, MainActivity::class.java).setAction(action)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE)
    }
}
