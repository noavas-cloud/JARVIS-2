package com.kemal.jarvis.tools

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.provider.ContactsContract
import android.provider.MediaStore
import android.provider.Settings as AndroidSettings
import android.view.KeyEvent
import android.media.AudioManager
import android.app.ActivityManager
import com.kemal.jarvis.core.Memory
import com.kemal.jarvis.core.Settings
import com.kemal.jarvis.core.SharedMemory
import com.kemal.jarvis.core.TextMatch
import com.kemal.jarvis.tools.ToolDecl.bool
import com.kemal.jarvis.tools.ToolDecl.fn
import com.kemal.jarvis.tools.ToolDecl.int
import com.kemal.jarvis.tools.ToolDecl.result
import com.kemal.jarvis.tools.ToolDecl.str
import com.kemal.jarvis.tools.ToolDecl.strList
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/** Araçların ihtiyaç duyduğu ekran/izin/onay işlemleri (Conversation sağlar). */
interface ToolHost {
    val context: Context
    val memory: Memory
    val settings: Settings
    /** Ekranda onay kartı gösterir; kullanıcı ONAYLA'ya basarsa true. Süre dolarsa ya da reddederse false. */
    suspend fun confirm(title: String, lines: List<String>, confirmLabel: String): Boolean
    /** İzin verilmişse true; JARVIS ekranı açıksa ister, değilse false. */
    suspend fun ensurePermission(permission: String, why: String): Boolean
    /** Başka bir uygulamayı/ekranı açar. Android arka plandan açmaya izin vermezse bildirim gösterir ve false döner. */
    fun launch(intent: Intent, label: String): Boolean
    /** Bu yanıt bitince konuşmayı kapat (ör. müzik başlatıldı). */
    fun endAfterTurn()
    suspend fun callAndSay(name: String, number: String, message: String): JSONObject
    /** Ortak hafızayı Mac ile hemen eşitler; Mac eşleşmemişse null, ulaşılamazsa false. */
    suspend fun syncMemory(): Boolean?
    /** Son eşitlemede bu "unut" isteğine Mac'in verdiği cevap (yoksa null). */
    fun macForgetMessage(text: String): String?
}

/** Telefonun kendi yetenekleri. Android'in izin vermediği işler dürüstçe reddedilir. */
class PhoneTools(private val host: ToolHost) {
    private val ctx get() = host.context
    private val tr = Locale("tr", "TR")

    val declarations: List<JSONObject> = listOf(
        fn("device_status", "Telefonun durumu: pil yüzdesi ve şarj, internet (Wi-Fi/mobil), boş depolama, RAM, ses düzeyleri, zil modu, saat ve tarih."),
        fn("set_volume", "Telefonun ses düzeyini ayarlar. Yüzde ver ya da artır/azalt/sessiz.",
            mapOf("stream" to str("Hangi ses", listOf("media", "ring", "alarm", "notification")),
                "percent" to int("0-100 arası hedef düzey"),
                "change" to str("Göreli değişiklik", listOf("up", "down", "mute", "unmute"))), listOf("stream")),
        fn("flashlight", "Telefonun fenerini (flaş ışığı) açar ya da kapatır.", mapOf("on" to bool("true=aç, false=kapat")), listOf("on")),
        fn("set_alarm", "Telefonun saat uygulamasında alarm kurar.",
            mapOf("hour" to int("0-23"), "minute" to int("0-59"), "label" to str("Alarm adı"),
                "days" to strList("Tekrar günleri: mon,tue,wed,thu,fri,sat,sun (boşsa bir kez)")), listOf("hour", "minute")),
        fn("set_timer", "Geri sayım zamanlayıcısı kurar (ör. 10 dakika).",
            mapOf("seconds" to int("Toplam saniye"), "label" to str("Zamanlayıcı adı")), listOf("seconds")),
        fn("show_alarms", "Telefonun alarm listesini açar."),
        fn("phone_calendar_events", "Telefonun takviminden (Google/Samsung takvimi) bir günün etkinliklerini okur.",
            mapOf("day" to str("today, tomorrow ya da YYYY-MM-DD")), listOf("day")),
        fn("phone_add_calendar_event", "Telefonun takvimine etkinlik ekler. Kullanıcı ekranda onaylar. Saat yerel saattir.",
            mapOf("title" to str("Başlık"), "start" to str("YYYY-MM-DDTHH:MM (tüm gün ise YYYY-MM-DD)"),
                "end" to str("YYYY-MM-DDTHH:MM; boşsa 1 saat"), "all_day" to bool("Tüm gün"),
                "location" to str("Yer"), "notes" to str("Not"), "reminder_minutes" to int("Kaç dakika önce hatırlatılsın (varsayılan 15)")),
            listOf("title", "start")),
        fn("open_app", "Telefonda yüklü bir uygulamayı adıyla açar (WhatsApp, Spotify, YouTube, Instagram, Ayarlar…).",
            mapOf("name" to str("Uygulama adı")), listOf("name")),
        fn("open_url", "Bir web sayfasını telefonun tarayıcısında açar. Yalnız kullanıcı sayfayı açmanı isterse.",
            mapOf("url" to str("https:// adresi")), listOf("url")),
        fn("search_in_browser", "Kullanıcı 'Google'da aç/tarayıcıda ara' derse arama sonuçlarını tarayıcıda açar.",
            mapOf("query" to str("Arama")), listOf("query")),
        fn("navigate", "Haritalar'da bir yere yol tarifi başlatır.",
            mapOf("destination" to str("Varış yeri"), "mode" to str("Ulaşım", listOf("driving", "walking", "transit", "bicycling"))),
            listOf("destination")),
        fn("media_control", "Telefonda çalan müziği/videoyu yönetir (Spotify, YouTube Music…).",
            mapOf("action" to str("İşlem", listOf("play", "pause", "next", "previous"))), listOf("action")),
        fn("find_contact", "Rehberde kişi arar; ad ve numaraları döndürür.", mapOf("name" to str("Kişi adı")), listOf("name")),
        fn("call_contact", "Telefonla birini arar. Kullanıcı ekranda onaylar.",
            mapOf("name" to str("Rehberdeki ad"), "number" to str("Numara (ad yoksa)"))),
        fn("call_and_say", "Birini telefonla arar ve mesajı hoparlörden okur (karşı taraf cevap veremez, tek yönlü). Kullanıcı ekranda onaylar.",
            mapOf("recipient_name" to str("Rehberdeki ad"), "phone_number" to str("Numara (ad yoksa)"),
                "message" to str("İletilecek mesaj (en çok 600 karakter)")), listOf("message")),
        fn("message_draft", "SMS ya da WhatsApp mesaj TASLAĞI açar; mesajı kullanıcı kendisi gönderir.",
            mapOf("name" to str("Rehberdeki ad"), "number" to str("Numara (ad yoksa)"), "message" to str("Mesaj"),
                "app" to str("Uygulama", listOf("sms", "whatsapp"))), listOf("message", "app")),
        fn("open_settings", "Telefon ayarlarının ilgili ekranını açar. Wi-Fi, Bluetooth, mobil veri, uçak modu gibi ayarları Android uygulamaların doğrudan değiştirmesine izin vermez; bu ekranı açarsın, kullanıcı dokunur.",
            mapOf("panel" to str("Ekran", listOf("wifi", "bluetooth", "internet", "airplane", "nfc", "volume", "display", "battery",
                "location", "dnd", "sound", "notifications", "apps", "hotspot", "main"))), listOf("panel")),
        fn("open_camera", "Kamera uygulamasını açar (fotoğraf ya da video). Fotoğrafı kullanıcı çeker.",
            mapOf("mode" to str("Mod", listOf("photo", "video")))),
        fn("remember", "Kullanıcının söylediği bir bilgiyi kalıcı ortak hafızaya kaydeder (telefon ve Mac'teki JARVIS ikisi de bilir; ör. tercih, plan, park yeri).",
            mapOf("note" to str("Kaydedilecek bilgi (tek cümle)")), listOf("note")),
        fn("forget", "Ortak hafızadan (telefon ve Mac) bir bilgiyi siler.", mapOf("text" to str("Silinecek bilginin bir parçası")), listOf("text")),
        fn("list_memories", "Ortak hafızadaki (telefon + Mac) kayıtları listeler."),
        fn("end_conversation", "Kullanıcı konuşmayı bitirmek istediğinde (\"tamam teşekkürler\", \"kapat\", \"görüşürüz\") kısa bir vedadan sonra çağır; dinleme durur."),
    )

    val names: Set<String> = declarations.map { it.getString("name") }.toSet()

    /** Bu araçlar kullanıcıya görünür bir onay kartı gösterir (tool içinde). */
    suspend fun run(name: String, args: JSONObject): JSONObject = try {
        when (name) {
            "device_status" -> deviceStatus()
            "set_volume" -> setVolume(args)
            "flashlight" -> flashlight(args.optBoolean("on", true))
            "set_alarm" -> setAlarm(args)
            "set_timer" -> setTimer(args)
            "show_alarms" -> open(Intent(AlarmClock.ACTION_SHOW_ALARMS), "Alarmlar")
            "phone_calendar_events" -> calendarEvents(args.optString("day", "today"))
            "phone_add_calendar_event" -> addCalendarEvent(args)
            "open_app" -> openApp(args.optString("name"))
            "open_url" -> openUrl(args.optString("url"))
            "search_in_browser" -> open(Intent(Intent.ACTION_VIEW,
                Uri.parse("https://www.google.com/search?q=" + URLEncoder.encode(args.optString("query"), "UTF-8"))), "Google araması")
            "navigate" -> navigate(args)
            "media_control" -> mediaControl(args.optString("action"))
            "find_contact" -> findContact(args.optString("name"))
            "call_contact" -> callContact(args)
            "call_and_say" -> callAndSay(args)
            "message_draft" -> messageDraft(args)
            "open_settings" -> openSettings(args.optString("panel", "main"))
            "open_camera" -> open(Intent(if (args.optString("mode") == "video") MediaStore.INTENT_ACTION_VIDEO_CAMERA
                else MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA), "Kamera")
            "remember" -> remember(args.optString("note"))
            "forget" -> forget(args.optString("text"))
            "list_memories" -> listMemories()
            "end_conversation" -> { host.endAfterTurn(); result("ok", "Konuşma bu yanıttan sonra kapanacak.") }
            else -> result("error", "Bilinmeyen telefon aracı: $name")
        }
    } catch (e: SecurityException) {
        result("no_permission", "Android bu işlem için gereken izni vermedi.")
    } catch (e: Exception) {
        result("error", "İşlem yapılamadı: ${e.javaClass.simpleName}")
    }

    // ── Durum ──────────────────────────────────────────────────────────────────────────────────

    private fun deviceStatus(): JSONObject {
        val bm = ctx.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val sticky = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val plugged = (sticky?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        val net = when {
            caps == null -> "yok"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "Wi-Fi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "mobil veri"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "kablo"
            else -> "var"
        }
        val stat = StatFs(Environment.getDataDirectory().path)
        val mem = ActivityManager.MemoryInfo().also { (ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(it) }
        val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        fun pct(s: Int) = (am.getStreamVolume(s) * 100 / maxOf(1, am.getStreamMaxVolume(s)))
        val ringer = when (am.ringerMode) { AudioManager.RINGER_MODE_SILENT -> "sessiz"; AudioManager.RINGER_MODE_VIBRATE -> "titreşim"; else -> "normal" }
        val now = LocalDateTime.now()
        return result("ok", "Telefon durumu", mapOf(
            "battery_percent" to bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY),
            "charging" to plugged,
            "internet" to net,
            "storage_free_gb" to String.format(Locale.US, "%.1f", stat.availableBytes / 1e9),
            "storage_total_gb" to String.format(Locale.US, "%.0f", stat.totalBytes / 1e9),
            "ram_free_gb" to String.format(Locale.US, "%.1f", mem.availMem / 1e9),
            "media_volume_percent" to pct(AudioManager.STREAM_MUSIC),
            "ring_volume_percent" to pct(AudioManager.STREAM_RING),
            "ringer_mode" to ringer,
            "time" to now.format(DateTimeFormatter.ofPattern("HH:mm")),
            "date" to now.format(DateTimeFormatter.ofPattern("d MMMM yyyy, EEEE", tr)),
            "model" to "${Build.MANUFACTURER} ${Build.MODEL}",
        ))
    }

    private fun setVolume(a: JSONObject): JSONObject {
        val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val stream = when (a.optString("stream", "media")) {
            "ring" -> AudioManager.STREAM_RING; "alarm" -> AudioManager.STREAM_ALARM
            "notification" -> AudioManager.STREAM_NOTIFICATION; else -> AudioManager.STREAM_MUSIC
        }
        val max = am.getStreamMaxVolume(stream)
        val current = am.getStreamVolume(stream)
        val target = when (a.optString("change")) {
            "up" -> minOf(max, current + maxOf(1, max / 6)); "down" -> maxOf(0, current - maxOf(1, max / 6))
            "mute" -> 0; "unmute" -> if (current == 0) maxOf(1, max / 2) else current
            else -> if (a.has("percent")) (a.optInt("percent").coerceIn(0, 100) * max + 50) / 100 else current
        }
        return try {
            am.setStreamVolume(stream, target, AudioManager.FLAG_SHOW_UI)
            result("ok", "Ses düzeyi %${target * 100 / maxOf(1, max)} yapıldı.")
        } catch (e: SecurityException) {
            result("no_permission", "Zil sesini sıfırlamak Rahatsız Etme modunu değiştirir; Android buna izin vermedi. Ses ekranını açmamı isteyebilirsin.")
        }
    }

    private fun flashlight(on: Boolean): JSONObject {
        val cm = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val id = cm.cameraIdList.firstOrNull { cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
            ?: return result("unsupported", "Bu telefonda fener yok.")
        return try {
            cm.setTorchMode(id, on)
            result("ok", if (on) "Fener açıldı." else "Fener kapatıldı.")
        } catch (e: Exception) { result("error", "Fener şu an kullanılamıyor (kamera başka uygulamada olabilir).") }
    }

    // ── Saat / takvim ─────────────────────────────────────────────────────────────────────────

    private fun setAlarm(a: JSONObject): JSONObject {
        val hour = a.optInt("hour", -1); val minute = a.optInt("minute", -1)
        if (hour !in 0..23 || minute !in 0..59) return result("invalid", "Saat geçersiz.")
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, hour)
            .putExtra(AlarmClock.EXTRA_MINUTES, minute).putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        a.optString("label").takeIf { it.isNotBlank() }?.let { intent.putExtra(AlarmClock.EXTRA_MESSAGE, it.take(60)) }
        val days = ToolArgs.days(a.optJSONArray("days"))
        if (days.isNotEmpty()) intent.putExtra(AlarmClock.EXTRA_DAYS, ArrayList(days))
        val time = String.format(Locale.US, "%02d:%02d", hour, minute)
        return if (host.launch(intent, "Alarm $time")) result("ok", "Alarm $time için kuruldu${if (days.isNotEmpty()) " (tekrarlı)" else ""}.")
        else needsTap("Alarm")
    }

    private fun setTimer(a: JSONObject): JSONObject {
        val sec = a.optInt("seconds", 0)
        if (sec !in 1..86_399) return result("invalid", "Süre 1 sn ile 24 saat arasında olmalı.")
        val intent = Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, sec).putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        a.optString("label").takeIf { it.isNotBlank() }?.let { intent.putExtra(AlarmClock.EXTRA_MESSAGE, it.take(60)) }
        return if (host.launch(intent, "Zamanlayıcı")) result("ok", "Zamanlayıcı ${ToolArgs.duration(sec)} için başladı.") else needsTap("Zamanlayıcı")
    }

    private suspend fun calendarEvents(day: String): JSONObject {
        if (!host.ensurePermission(Manifest.permission.READ_CALENDAR, "Takvimini okuyabilmem için"))
            return result("no_permission", "Takvim izni yok. JARVIS ekranında izin ver ya da Ayarlar'dan aç.")
        val date = ToolArgs.day(day) ?: return result("invalid", "Gün anlaşılmadı.")
        val zone = ZoneId.systemDefault()
        val start = date.atStartOfDay(zone).toInstant().toEpochMilli()
        val end = date.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            android.content.ContentUris.appendId(it, start); android.content.ContentUris.appendId(it, end)
        }.build()
        val list = JSONArray()
        ctx.contentResolver.query(uri, arrayOf(CalendarContract.Instances.TITLE, CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END, CalendarContract.Instances.ALL_DAY, CalendarContract.Instances.EVENT_LOCATION),
            null, null, CalendarContract.Instances.BEGIN + " ASC")?.use { c ->
            val fmt = DateTimeFormatter.ofPattern("HH:mm")
            while (c.moveToNext() && list.length() < 30) {
                val allDay = c.getInt(3) == 1
                val b = java.time.Instant.ofEpochMilli(c.getLong(1)).atZone(zone)
                val e = java.time.Instant.ofEpochMilli(c.getLong(2)).atZone(zone)
                list.put(JSONObject().put("title", c.getString(0) ?: "(adsız)")
                    .put("time", if (allDay) "tüm gün" else "${b.format(fmt)}–${e.format(fmt)}")
                    .put("location", c.getString(4) ?: ""))
            }
        }
        val label = date.format(DateTimeFormatter.ofPattern("d MMMM EEEE", tr))
        return result("ok", if (list.length() == 0) "$label için telefonun takviminde etkinlik yok." else "$label etkinlikleri",
            mapOf("events" to list))
    }

    @SuppressLint("MissingPermission")
    private suspend fun addCalendarEvent(a: JSONObject): JSONObject {
        val title = a.optString("title").trim().take(120)
        if (title.isEmpty()) return result("invalid", "Başlık gerekli.")
        val allDay = a.optBoolean("all_day", false) || a.optString("start").length == 10
        val span = ToolArgs.eventSpan(a.optString("start"), a.optString("end"), allDay) ?: return result("invalid", "Başlangıç tarihi anlaşılmadı.")
        val (begin, end) = span
        val zone = ZoneId.systemDefault()
        val shown = if (allDay) begin.format(DateTimeFormatter.ofPattern("d MMMM EEEE", tr)) + " (tüm gün)"
            else begin.format(DateTimeFormatter.ofPattern("d MMMM EEEE HH:mm", tr)) + "–" + end.format(DateTimeFormatter.ofPattern("HH:mm"))
        val lines = listOfNotNull(title, shown, a.optString("location").takeIf { it.isNotBlank() }?.let { "Yer: $it" })
        if (!host.confirm("Takvime eklensin mi?", lines, "EKLE")) return result("declined", "Kullanıcı onaylamadı; etkinlik eklenmedi.")
        // Android okuma ve yazma iznini ayrı verir; takvim seçmek için okuma da gerekir.
        val allowed = host.ensurePermission(Manifest.permission.READ_CALENDAR, "Takvimi görebilmem için") &&
            host.ensurePermission(Manifest.permission.WRITE_CALENDAR, "Takvime ekleyebilmem için")
        val calId = if (allowed) primaryCalendar() else null
        if (calId == null) {
            // İzin yoksa takvim uygulamasının kendi formunu aç (kullanıcı Kaydet'e basar).
            val intent = Intent(Intent.ACTION_INSERT, CalendarContract.Events.CONTENT_URI)
                .putExtra(CalendarContract.Events.TITLE, title)
                .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, begin.atZone(zone).toInstant().toEpochMilli())
                .putExtra(CalendarContract.EXTRA_EVENT_END_TIME, end.atZone(zone).toInstant().toEpochMilli())
                .putExtra(CalendarContract.EXTRA_EVENT_ALL_DAY, allDay)
                .putExtra(CalendarContract.Events.EVENT_LOCATION, a.optString("location"))
            val why = if (allowed) "Telefonda yazılabilir bir takvim hesabı bulunamadığı" else "Takvim izni olmadığı"
            return if (host.launch(intent, "Takvim")) result("form_opened", "$why için takvim uygulamasının formu açıldı; kaydetmek için Kaydet'e basman gerekiyor.")
            else needsTap("Takvim")
        }
        val values = ContentValues().apply {
            put(CalendarContract.Events.CALENDAR_ID, calId)
            put(CalendarContract.Events.TITLE, title)
            put(CalendarContract.Events.DESCRIPTION, a.optString("notes").take(1000))
            put(CalendarContract.Events.EVENT_LOCATION, a.optString("location").take(200))
            if (allDay) {
                put(CalendarContract.Events.ALL_DAY, 1)
                put(CalendarContract.Events.EVENT_TIMEZONE, "UTC")
                put(CalendarContract.Events.DTSTART, begin.toLocalDate().atStartOfDay(ZoneId.of("UTC")).toInstant().toEpochMilli())
                put(CalendarContract.Events.DTEND, end.toLocalDate().atStartOfDay(ZoneId.of("UTC")).toInstant().toEpochMilli())
            } else {
                put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
                put(CalendarContract.Events.DTSTART, begin.atZone(zone).toInstant().toEpochMilli())
                put(CalendarContract.Events.DTEND, end.atZone(zone).toInstant().toEpochMilli())
            }
        }
        val uri = ctx.contentResolver.insert(CalendarContract.Events.CONTENT_URI, values) ?: return result("error", "Etkinlik eklenemedi.")
        val reminder = a.optInt("reminder_minutes", 15)
        val eventId = uri.lastPathSegment?.toLongOrNull()
        if (eventId != null && reminder in 0..10080 && !allDay) {
            ctx.contentResolver.insert(CalendarContract.Reminders.CONTENT_URI, ContentValues().apply {
                put(CalendarContract.Reminders.EVENT_ID, eventId)
                put(CalendarContract.Reminders.MINUTES, reminder)
                put(CalendarContract.Reminders.METHOD, CalendarContract.Reminders.METHOD_ALERT)
            })
        }
        return result("ok", "Etkinlik telefonun takvimine eklendi: $title, $shown.")
    }

    @SuppressLint("MissingPermission")
    private fun primaryCalendar(): Long? {
        ctx.contentResolver.query(CalendarContract.Calendars.CONTENT_URI,
            arrayOf(CalendarContract.Calendars._ID, CalendarContract.Calendars.IS_PRIMARY, CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL,
                CalendarContract.Calendars.VISIBLE, CalendarContract.Calendars.ACCOUNT_TYPE),
            null, null, null)?.use { c ->
            var best: Long? = null; var bestScore = -1
            while (c.moveToNext()) {
                if (c.getInt(2) < CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR || c.getInt(3) == 0) continue
                val score = (if (c.getInt(1) == 1) 4 else 0) + (if (c.getString(4) == "com.google") 2 else 0) + 1
                if (score > bestScore) { bestScore = score; best = c.getLong(0) }
            }
            return best
        }
        return null
    }

    // ── Uygulamalar / web / harita / medya ────────────────────────────────────────────────────

    private data class App(val label: String, val pkg: String)

    private fun openApp(name: String): JSONObject {
        if (name.isBlank()) return result("invalid", "Uygulama adı gerekli.")
        val pm = ctx.packageManager
        val apps = pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
            .map { App(it.loadLabel(pm).toString(), it.activityInfo.packageName) }.distinctBy { it.pkg }
        val found = ToolArgs.appAliases(name).asSequence().map { TextMatch.bestApps(it, apps) { a -> a.label } }
            .firstOrNull { it.isNotEmpty() }.orEmpty()
        if (found.isEmpty()) return result("not_found", "\"$name\" adında yüklü uygulama bulunamadı.")
        if (found.size > 1 && found.map { it.label }.distinct().size > 1)
            return result("ambiguous", "Birden fazla uygulama eşleşti; hangisi?", mapOf("candidates" to JSONArray(found.take(5).map { it.label })))
        val app = found.first()
        val launch = pm.getLaunchIntentForPackage(app.pkg) ?: return result("error", "${app.label} açılamıyor.")
        return if (host.launch(launch, app.label)) result("ok", "${app.label} açıldı.") else needsTap(app.label)
    }

    private fun openUrl(raw: String): JSONObject {
        val url = raw.trim().let { if (it.startsWith("http://") || it.startsWith("https://")) it else "https://$it" }
        val uri = Uri.parse(url)
        if (uri.host.isNullOrEmpty() || url.length > 2000) return result("invalid", "Adres geçersiz.")
        return open(Intent(Intent.ACTION_VIEW, uri), uri.host ?: "Sayfa")
    }

    private fun navigate(a: JSONObject): JSONObject {
        val dest = a.optString("destination").trim()
        if (dest.isEmpty()) return result("invalid", "Varış yeri gerekli.")
        val mode = a.optString("mode", "driving").takeIf { it in listOf("driving", "walking", "transit", "bicycling") } ?: "driving"
        val uri = Uri.parse("https://www.google.com/maps/dir/?api=1&destination=" + URLEncoder.encode(dest, "UTF-8") + "&travelmode=$mode&dir_action=navigate")
        return if (host.launch(Intent(Intent.ACTION_VIEW, uri), "Yol tarifi")) result("ok", "$dest için yol tarifi açıldı.") else needsTap("Yol tarifi")
    }

    private fun mediaControl(action: String): JSONObject {
        val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val key = when (action) {
            "play" -> KeyEvent.KEYCODE_MEDIA_PLAY; "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
            "next" -> KeyEvent.KEYCODE_MEDIA_NEXT; "previous" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
            else -> return result("invalid", "İşlem anlaşılmadı.")
        }
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, key))
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, key))
        // Müzik başlarken JARVIS'in konuşması onu bastırmasın: bu yanıttan sonra dinleme kapanır.
        if (action == "play" || action == "next" || action == "previous") host.endAfterTurn()
        return result("ok", "Medya komutu gönderildi ($action). Hangi uygulamanın tepki verdiği doğrulanamaz; çalan bir müzik uygulaması yoksa bir şey olmayabilir.")
    }

    // ── Kişiler / arama / mesaj ───────────────────────────────────────────────────────────────

    private suspend fun contacts(): List<TextMatch.Contact>? {
        if (!host.ensurePermission(Manifest.permission.READ_CONTACTS, "Rehberde kişiyi bulabilmem için")) return null
        val out = ArrayList<TextMatch.Contact>()
        ctx.contentResolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER),
            null, null, null)?.use { c -> while (c.moveToNext()) out.add(TextMatch.Contact(c.getString(0) ?: "", c.getString(1) ?: "")) }
        return out
    }

    private suspend fun findContact(name: String): JSONObject {
        val all = contacts() ?: return result("no_permission", "Rehber izni yok.")
        val scored = all.mapNotNull { c -> TextMatch.normalizeNumber(c.number)?.let { n -> Triple(TextMatch.score(TextMatch.stripSuffix(name), c.name), c.name, n) } }
            .filter { it.first > 0 }.sortedByDescending { it.first }.distinctBy { it.third }.take(5)
        if (scored.isEmpty()) return result("not_found", "Rehberde \"$name\" bulunamadı.")
        return result("ok", "Bulunan kişiler", mapOf("contacts" to JSONArray(scored.map {
            JSONObject().put("name", it.second).put("number", TextMatch.prettyNumber(it.third))
        })))
    }

    /** Ad ya da numaradan tek numara çözer; olmazsa modele açıklayıcı sonuç. */
    private suspend fun resolve(name: String, number: String): Pair<TextMatch.Contact?, JSONObject?> {
        if (number.isNotBlank()) {
            val n = TextMatch.normalizeNumber(number) ?: return null to result("invalid_number", "Numara geçersiz.")
            return TextMatch.Contact(name.ifBlank { TextMatch.prettyNumber(n) }, n) to null
        }
        if (name.isBlank()) return null to result("needs_recipient", "Kimin aranacağı/yazılacağı belli değil.")
        if (TextMatch.looksLikeNumber(name)) return resolve("", name)
        val all = contacts() ?: return null to result("no_permission", "Rehber izni yok; numarayı söylersen onu kullanırım.")
        val m = TextMatch.matchContact(name, all)
        if (m.contact != null) return m.contact to null
        return null to if (m.candidates.isEmpty()) result("not_found", "Rehberde \"$name\" bulunamadı.")
        else result("ambiguous", "Birden fazla kişi eşleşti; tam adı sor.", mapOf("candidates" to JSONArray(m.candidates)))
    }

    private suspend fun callContact(a: JSONObject): JSONObject {
        val (contact, problem) = resolve(a.optString("name"), a.optString("number"))
        if (contact == null) return problem!!
        if (!host.confirm("Aransın mı?", listOf(contact.name, TextMatch.prettyNumber(contact.number)), "ARA"))
            return result("declined", "Kullanıcı onaylamadı; arama yapılmadı.")
        if (!host.ensurePermission(Manifest.permission.CALL_PHONE, "Arama yapabilmem için")) {
            return if (host.launch(Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + contact.number)), "Telefon"))
                result("dialer_opened", "Arama izni olmadığı için numara Telefon uygulamasında açıldı; aramak için yeşil düğmeye basman gerekiyor.")
            else needsTap("Telefon")
        }
        return if (host.launch(Intent(Intent.ACTION_CALL, Uri.parse("tel:" + contact.number)), "Arama"))
            result("dialing", "${contact.name} aranıyor.") else needsTap("Arama")
    }

    private suspend fun callAndSay(a: JSONObject): JSONObject {
        val message = a.optString("message").trim()
        if (message.isEmpty() || message.length > 600) return result("invalid", "Mesaj 1–600 karakter olmalı.")
        return host.callAndSay(a.optString("recipient_name"), a.optString("phone_number"), message)
    }

    /** Ara ve söyle için (Mac isteği de buradan geçer). */
    suspend fun resolveForCall(name: String, number: String) = resolve(name, number)

    private suspend fun messageDraft(a: JSONObject): JSONObject {
        val message = a.optString("message").trim().take(2000)
        if (message.isEmpty()) return result("invalid", "Mesaj boş.")
        val (contact, problem) = resolve(a.optString("name"), a.optString("number"))
        if (contact == null) return problem!!
        val app = a.optString("app", "sms")
        val intent = if (app == "whatsapp") {
            Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/" + contact.number.removePrefix("+") + "?text=" + URLEncoder.encode(message, "UTF-8")))
                .also { if (isInstalled("com.whatsapp")) it.setPackage("com.whatsapp") }
        } else {
            Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + contact.number)).putExtra("sms_body", message)
        }
        val label = if (app == "whatsapp") "WhatsApp" else "Mesajlar"
        return if (host.launch(intent, label)) result("draft_opened", "$label'da ${contact.name} için taslak açıldı. Mesaj GÖNDERİLMEDİ; kullanıcı Gönder'e basmalı.")
        else needsTap(label)
    }

    private fun isInstalled(pkg: String) = try { ctx.packageManager.getPackageInfo(pkg, 0); true } catch (e: PackageManager.NameNotFoundException) { false }

    // ── Ayarlar / kamera ─────────────────────────────────────────────────────────────────────

    private fun openSettings(panel: String): JSONObject {
        val q = Build.VERSION.SDK_INT >= 29
        val action = when (panel) {
            "wifi" -> if (q) AndroidSettings.Panel.ACTION_WIFI else AndroidSettings.ACTION_WIFI_SETTINGS
            "internet" -> if (q) AndroidSettings.Panel.ACTION_INTERNET_CONNECTIVITY else AndroidSettings.ACTION_WIRELESS_SETTINGS
            "nfc" -> if (q) AndroidSettings.Panel.ACTION_NFC else AndroidSettings.ACTION_NFC_SETTINGS
            "volume" -> if (q) AndroidSettings.Panel.ACTION_VOLUME else AndroidSettings.ACTION_SOUND_SETTINGS
            "bluetooth" -> AndroidSettings.ACTION_BLUETOOTH_SETTINGS
            "airplane" -> AndroidSettings.ACTION_AIRPLANE_MODE_SETTINGS
            "display" -> AndroidSettings.ACTION_DISPLAY_SETTINGS
            "battery" -> Intent.ACTION_POWER_USAGE_SUMMARY
            "location" -> AndroidSettings.ACTION_LOCATION_SOURCE_SETTINGS
            "dnd" -> AndroidSettings.ACTION_ZEN_MODE_PRIORITY_SETTINGS
            "sound" -> AndroidSettings.ACTION_SOUND_SETTINGS
            "notifications" -> AndroidSettings.ACTION_APP_NOTIFICATION_SETTINGS
            "apps" -> AndroidSettings.ACTION_APPLICATION_SETTINGS
            "hotspot" -> AndroidSettings.ACTION_WIRELESS_SETTINGS
            else -> AndroidSettings.ACTION_SETTINGS
        }
        val intent = Intent(action)
        if (panel == "notifications") intent.putExtra(AndroidSettings.EXTRA_APP_PACKAGE, ctx.packageName)
        val opened = try { host.launch(intent, "Ayarlar") } catch (e: ActivityNotFoundException) {
            host.launch(Intent(AndroidSettings.ACTION_SETTINGS), "Ayarlar")
        }
        return if (opened) result("ok", "İlgili ayar ekranı açıldı. Değişikliği kullanıcı dokunarak yapar; Android uygulamaların bunu kendisinin değiştirmesine izin vermez.")
        else needsTap("Ayarlar")
    }

    private fun open(intent: Intent, label: String): JSONObject =
        if (host.launch(intent, label)) result("ok", "$label açıldı.") else needsTap(label)

    private fun needsTap(label: String) = result("needs_tap",
        "Telefon ekranı kapalı ya da JARVIS arka planda olduğu için Android $label ekranını açmama izin vermedi. Bildirimdeki \"$label\" düğmesine dokununca açılır. (Ayarlar › Diğer uygulamaların üzerinde göster izni verilirse bu bir daha olmaz.)")

    // ── Hafıza ───────────────────────────────────────────────────────────────────────────────

    private suspend fun remember(note: String): JSONObject {
        if (note.isBlank()) return result("invalid", "Kaydedilecek bilgi boş.")
        host.memory.remember(note)
        return when (host.syncMemory()) {
            null -> result("ok", "Telefona kaydedildi (Mac ile eşleşme yok).")
            true -> result("ok", "Kaydedildi; Mac'teki JARVIS de biliyor.")
            false -> result("ok", "Telefona kaydedildi; Mac'e ulaşılamadı, bağlanınca oraya da eklenecek.")
        }
    }

    private suspend fun forget(text: String): JSONObject {
        val paired = host.syncMemory() != null   // önce bekleyenleri gönder: Mac listesi güncel olsun
        val gone = host.memory.forget(text, queueForMac = paired)
        if (!paired) return if (gone.isEmpty()) result("not_found", "Bu bilgiyi içeren not bulunamadı.")
            else result("ok", "${gone.size} not silindi.", mapOf("deleted" to JSONArray(gone.map { it.text })))
        val mac = if (host.syncMemory() == true)
            host.macForgetMessage(text)?.let { SharedMemory.explainForget(it) } else null
        val macNote = mac ?: "Mac'e ulaşılamadı; bağlanınca Mac'teki eşleşen kayıt da silinecek."
        val local = if (gone.isEmpty()) "Telefondaki listede eşleşen not yoktu." else "${gone.size} kayıt silindi."
        return result("ok", "$local $macNote", mapOf("deleted" to JSONArray(gone.map { it.text })))
    }

    private suspend fun listMemories(): JSONObject {
        val synced = host.syncMemory()   // Mac'te yeni eklenenler de görünsün
        return result("ok", if (synced == false) "Ortak hafıza (Mac'e ulaşılamadı; son bilinen hâli)" else "Ortak hafıza (telefon + Mac)", mapOf(
        "notes" to JSONArray(host.memory.allNotes().map { it.text }),
        "pending_on_phone" to JSONArray(host.memory.notes().map { it.text })))
    }
}

/** Araç parametrelerinin Android'den bağımsız yorumlanması (JVM'de test edilir). */
object ToolArgs {
    /** "mon,tue" → Calendar.MONDAY…; Türkçe adlar/kısaltmalar da kabul edilir ("Cumartesi" Cuma sanılmaz). */
    fun days(array: JSONArray?): List<Int> {
        if (array == null) return emptyList()
        fun one(raw: String): Int? {
            val d = TextMatch.fold(raw)
            return when {
                d.startsWith("cumartesi") || d == "cmt" || d.startsWith("sat") -> Calendar.SATURDAY
                d.startsWith("pazartesi") || d == "pzt" || d.startsWith("mon") -> Calendar.MONDAY
                d.startsWith("pazar") || d == "paz" || d.startsWith("sun") -> Calendar.SUNDAY
                d.startsWith("sal") || d.startsWith("tue") -> Calendar.TUESDAY
                d.startsWith("car") || d.startsWith("wed") -> Calendar.WEDNESDAY
                d.startsWith("per") || d.startsWith("thu") -> Calendar.THURSDAY
                d.startsWith("cum") || d.startsWith("fri") -> Calendar.FRIDAY
                else -> null
            }
        }
        return (0 until array.length()).mapNotNull { one(array.optString(it)) }.distinct()
    }

    fun duration(sec: Int): String {
        val h = sec / 3600; val m = (sec % 3600) / 60; val s = sec % 60
        return listOfNotNull(if (h > 0) "$h saat" else null, if (m > 0) "$m dakika" else null, if (s > 0) "$s saniye" else null).joinToString(" ")
    }

    fun day(raw: String, today: LocalDate = LocalDate.now()): LocalDate? = when (TextMatch.fold(raw)) {
        "", "today", "bugun" -> today
        "tomorrow", "yarin" -> today.plusDays(1)
        "yesterday", "dun" -> today.minusDays(1)
        else -> try { LocalDate.parse(raw.trim().take(10)) } catch (e: Exception) { null }
    }

    /** Başlangıç/bitiş; bitiş yoksa 1 saat (tüm gün: ertesi gün). */
    fun eventSpan(start: String, end: String, allDay: Boolean): Pair<LocalDateTime, LocalDateTime>? {
        val b = parseDateTime(start) ?: return null
        if (allDay) {
            val d = b.toLocalDate().atStartOfDay()
            val e = parseDateTime(end)?.toLocalDate()?.atStartOfDay()?.takeIf { it.isAfter(d) } ?: d.plusDays(1)
            return d to e
        }
        val e = parseDateTime(end)?.takeIf { it.isAfter(b) } ?: b.plusHours(1)
        return b to e
    }

    fun parseDateTime(raw: String): LocalDateTime? {
        val t = raw.trim().removeSuffix("Z")
        if (t.isEmpty()) return null
        return try {
            when {
                t.length == 10 -> LocalDate.parse(t).atStartOfDay()
                else -> LocalDateTime.parse(t.take(19).let { if (it.length == 16) "$it:00" else it })
            }
        } catch (e: Exception) { null }
    }

    /** Sık söylenen adlar → olası uygulama etiketleri (Türkçe ve İngilizce telefon dili, Samsung adları). */
    fun appAliases(name: String): List<String> = when (TextMatch.fold(name).removeSuffix(" uygulamasi").removeSuffix(" uygulamasini")) {
        "ayarlar", "settings" -> listOf("Ayarlar", "Settings")
        "kamera", "camera" -> listOf("Kamera", "Camera")
        "galeri", "fotograflar", "gallery", "photos" -> listOf("Galeri", "Gallery", "Fotoğraflar", "Photos", "Google Foto")
        "mesajlar", "sms", "messages" -> listOf("Mesajlar", "Messages")
        "telefon", "arama", "phone" -> listOf("Telefon", "Phone")
        "rehber", "kisiler", "contacts" -> listOf("Kişiler", "Rehber", "Contacts")
        "saat", "alarm", "clock" -> listOf("Saat", "Clock")
        "takvim", "calendar" -> listOf("Takvim", "Calendar")
        "harita", "haritalar", "google haritalar", "maps" -> listOf("Haritalar", "Maps", "Google Haritalar")
        "hesap makinesi", "calculator" -> listOf("Hesap Makinesi", "Calculator")
        "dosyalar", "dosyalarim", "files" -> listOf("Dosyalarım", "Dosyalar", "My Files", "Files")
        "youtube muzik" -> listOf("YouTube Music")
        "play store", "google play" -> listOf("Play Store", "Google Play Store")
        "tarayici", "internet" -> listOf("Chrome", "Internet", "Samsung Internet")
        else -> listOf(name)
    }
}
