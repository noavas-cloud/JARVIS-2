package com.kemal.jarvis.conv

import org.json.JSONObject

/** Sohbette görünen kısa işlem satırları (JVM'de test edilir). */
object ToolLabels {
    fun label(name: String, a: JSONObject): String = when (name) {
        "device_status" -> "Telefon durumu okunuyor"
        "set_volume" -> "Ses düzeyi ayarlanıyor"
        "flashlight" -> if (a.optBoolean("on", true)) "Fener açılıyor" else "Fener kapatılıyor"
        "set_alarm" -> "Alarm kuruluyor · %02d:%02d".format(a.optInt("hour"), a.optInt("minute"))
        "set_timer" -> "Zamanlayıcı kuruluyor"
        "show_alarms" -> "Alarmlar açılıyor"
        "phone_calendar_events" -> "Telefon takvimi okunuyor"
        "phone_add_calendar_event" -> "Takvime ekleniyor · ${a.optString("title")}"
        "open_app" -> "Açılıyor · ${a.optString("name")}"
        "open_url" -> "Sayfa açılıyor"
        "search_in_browser" -> "Tarayıcıda aranıyor · ${a.optString("query")}"
        "navigate" -> "Yol tarifi · ${a.optString("destination")}"
        "media_control" -> "Medya · ${mapOf("play" to "oynat", "pause" to "duraklat", "next" to "sonraki", "previous" to "önceki")[a.optString("action")] ?: a.optString("action")}"
        "find_contact" -> "Rehberde aranıyor · ${a.optString("name")}"
        "call_contact" -> "Arama hazırlanıyor · ${a.optString("name").ifEmpty { a.optString("number") }}"
        "call_and_say" -> "Ara ve söyle · ${a.optString("recipient_name").ifEmpty { a.optString("phone_number") }}"
        "message_draft" -> "Mesaj taslağı · ${a.optString("name").ifEmpty { a.optString("number") }}"
        "open_settings" -> "Ayar ekranı açılıyor"
        "open_camera" -> "Kamera açılıyor"
        "remember" -> "Hafızaya kaydediliyor"
        "forget" -> "Hafızadan siliniyor"
        "list_memories" -> "Hafıza okunuyor"
        "end_conversation" -> "Konuşma kapanıyor"
        else -> if (name.startsWith("mac_")) "Mac · ${macTitle(name.removePrefix("mac_"))}" else name
    }

    fun macTitle(name: String): String = when (name) {
        "open_app" -> "Uygulama aç"
        "get_calendar_events" -> "Takvimi oku"
        "add_calendar_event" -> "Takvime ekle"
        "delete_calendar_event" -> "Takvimden sil"
        "get_reminders" -> "Anımsatıcıları oku"
        "add_reminder" -> "Anımsatıcı ekle"
        "get_weather" -> "Hava durumu"
        "find_file", "smart_search" -> "Dosya ara"
        "move_file" -> "Dosya taşı"
        "rename_file" -> "Dosya adını değiştir"
        "trash_file" -> "Dosyayı çöpe at"
        "play_media" -> "Medya oynat"
        "browser_control" -> "Tarayıcı"
        "send_whatsapp_message" -> "WhatsApp mesajı"
        "save_whatsapp_contact" -> "WhatsApp kişisi kaydet"
        "sys_info", "status_report" -> "Mac durumu"
        "prepare_day" -> "Günü hazırla"
        "analyze_screen" -> "Mac ekranını analiz et"
        "get_shared_memory", "get_conversation_history" -> "Ortak hafıza"
        "context_control" -> "Mac'te işlem"
        else -> name.replace('_', ' ')
    }

    /** Onay kartında argümanların okunur hâli (en çok 6 satır, uzun değerler kısaltılır). */
    fun argLines(a: JSONObject): List<String> {
        val out = ArrayList<String>()
        val keys = a.keys()
        while (keys.hasNext() && out.size < 6) {
            val k = keys.next()
            val v = a.opt(k)?.toString().orEmpty()
            if (v.isBlank()) continue
            out.add("$k: " + if (v.length > 140) v.take(140) + "…" else v)
        }
        return out
    }

    /** Başarısız/onaysız sonuçlar sohbette ayrıca gösterilir; başarılılar için null. */
    fun outcome(result: Any): String? {
        val o = result as? JSONObject ?: return null
        return when (o.optString("status")) {
            "declined" -> "✕ Onaylanmadı — yapılmadı"
            "no_permission" -> "✕ İzin yok — " + o.optString("message")
            "needs_tap" -> "↗ Açmak için bildirime dokun"
            "error", "unsupported", "unavailable" -> "✕ " + o.optString("message")
            else -> null
        }
    }
}
