package com.kemal.jarvis.core

import org.json.JSONArray

/** Telefon ↔ Mac ortak hafıza yanıtlarının ayrıştırılması (Android'den bağımsız, JVM'de test edilir). */
object SharedMemory {
    /** Mac'in ortak hafızasındaki bir kayıt (Mac'te category/key; telefonda gösterilen metin). */
    data class Item(val category: String, val key: String, val text: String)

    fun parseItems(array: JSONArray?): List<Item> {
        if (array == null) return emptyList()
        return (0 until array.length()).mapNotNull { i ->
            val o = array.optJSONObject(i) ?: return@mapNotNull null
            val text = o.optString("text").trim()
            if (text.isEmpty()) null else Item(o.optString("category"), o.optString("key"), text.take(300))
        }.takeLast(200)
    }

    fun strings(array: JSONArray?): List<String> =
        if (array == null) emptyList() else (0 until array.length()).mapNotNull { array.opt(it) as? String }.filter { it.isNotBlank() }

    /** "forgotten": [{text, message}] → (istenen metin, Mac'in cevabı). */
    fun forgotten(array: JSONArray?): List<Pair<String, String>> {
        if (array == null) return emptyList()
        return (0 until array.length()).mapNotNull { i ->
            val o = array.optJSONObject(i) ?: return@mapNotNull null
            val text = o.optString("text")
            if (text.isBlank()) null else text to o.optString("message")
        }
    }

    /** Mac'in Türkçe karakter kullanmayan silme cevabını telefonda okunur hâle getirir. */
    fun explainForget(message: String): String {
        val m = message.lowercase()
        return when {
            "kaldirildi" in m -> "Mac'teki kayıt da silindi."
            "birden fazla" in m -> "Mac'te birden fazla kayıt eşleşti, hiçbiri silinmedi; hangisi olduğunu daha açık söyle."
            "bulamadim" in m || "kayit yok" in m -> "Mac'te eşleşen kayıt yoktu."
            else -> "Mac: $message"
        }
    }
}
