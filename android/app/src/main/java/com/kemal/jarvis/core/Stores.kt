package com.kemal.jarvis.core

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import java.util.Base64
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Gizli bilgiler (Gemini anahtarı, Mac eşleşmesi, hafıza, konuşma geçmişi) Android Keystore'daki
 * AES-GCM anahtarıyla şifrelenir; anahtar telefondan dışarı çıkamaz. Uygulama yedeklemesi kapalıdır.
 */
class SecureStore(context: Context) {
    private val prefs = context.getSharedPreferences("jarvis_secure", Context.MODE_PRIVATE)
    private val legacy = context.getSharedPreferences("pairing", Context.MODE_PRIVATE)

    private fun key(alias: String, create: Boolean): SecretKey? {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (ks.containsAlias(alias)) return ks.getKey(alias, null) as SecretKey
        if (!create) return null
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        return gen.generateKey()
    }

    @Synchronized
    fun get(name: String): String = decrypt(prefs, name + "_iv", name + "_data", ALIAS) ?: ""

    @SuppressLint("ApplySharedPref") // Gizli bilgi diske yazılmadan "kaydedildi" denmez.
    @Synchronized
    fun put(name: String, value: String) {
        if (value.isEmpty()) { remove(name); return }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key(ALIAS, true))
        val iv = Base64.getEncoder().encodeToString(cipher.iv)
        val data = Base64.getEncoder().encodeToString(cipher.doFinal(value.toByteArray(Charsets.UTF_8)))
        check(prefs.edit().putString(name + "_iv", iv).putString(name + "_data", data).commit()) { "Kaydedilemedi" }
    }

    @SuppressLint("ApplySharedPref")
    @Synchronized
    fun remove(name: String) { prefs.edit().remove(name + "_iv").remove(name + "_data").commit() }

    private fun decrypt(p: SharedPreferences, ivKey: String, dataKey: String, alias: String): String? {
        return try {
            val data = p.getString(dataKey, "").orEmpty()
            if (data.isEmpty()) return null
            val secret = key(alias, false) ?: return null
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, secret, GCMParameterSpec(128, Base64.getDecoder().decode(p.getString(ivKey, ""))))
            String(cipher.doFinal(Base64.getDecoder().decode(data)), Charsets.UTF_8)
        } catch (e: Exception) { null }
    }

    /**
     * JARVIS 1.x'ten bir kez taşıma: Gemini anahtarı ve Mac eşleşmesi korunur, eski kayıtlar silinir.
     * Eski konuşma geçmişi taşınmaz.
     */
    @SuppressLint("ApplySharedPref")
    @Synchronized
    fun migrateFromV1(): List<String> {
        if (legacy.all.isEmpty()) return emptyList()
        val moved = ArrayList<String>()
        decrypt(legacy, "api_key_iv", "api_key_data", LEGACY_ALIAS)?.takeIf { it.isNotBlank() && get(API_KEY).isEmpty() }
            ?.let { put(API_KEY, it); moved.add("Gemini anahtarın") }
        decrypt(legacy, "iv", "data", LEGACY_ALIAS)?.let { raw ->
            try {
                val o = JSONObject(raw)
                if (o.optString("url").isNotEmpty() && o.optString("token").isNotEmpty() && get(MAC_PAIRING).isEmpty()) {
                    put(MAC_PAIRING, JSONObject().put("url", o.optString("url")).put("token", o.optString("token"))
                        .put("name", o.optString("name", "JARVIS Mac")).toString())
                    moved.add("Mac eşleşmen")
                }
            } catch (_: Exception) {}
        }
        legacy.edit().clear().commit()
        try { KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(LEGACY_ALIAS) } catch (_: Exception) {}
        return moved
    }

    companion object {
        private const val ALIAS = "jarvis.v2.secure"
        private const val LEGACY_ALIAS = "jarvis.device.pairing.v1"
        const val API_KEY = "api_key"
        const val MAC_PAIRING = "mac_pairing"
        const val NOTES = "notes"
        const val TURNS = "turns"
        const val MAC_CONTEXT = "mac_context"
        const val MAC_ITEMS = "mac_items"
        const val FORGETS = "pending_forgets"
    }
}

/** Gizli olmayan tercihler. */
class Settings(context: Context) {
    private val p = context.getSharedPreferences("jarvis_settings", Context.MODE_PRIVATE)
    private val callAudio = context.getSharedPreferences("call_audio", Context.MODE_PRIVATE) // 1.3.1 ses testi sonucu

    var voice: String
        get() = p.getString("voice", "Charon") ?: "Charon"
        set(v) = p.edit().putString("voice", v).apply()

    /** true: JARVIS konuşurken mikrofon kapalı (yankı sorunu olan telefonlar / hoparlör için). */
    var halfDuplex: Boolean
        get() = p.getBoolean("half_duplex", false)
        set(v) = p.edit().putBoolean("half_duplex", v).apply()

    var webSearch: Boolean
        get() = p.getBoolean("web_search", true)
        set(v) = p.edit().putBoolean("web_search", v).apply()

    var ownerName: String
        get() = p.getString("owner_name", "Kullanıcı") ?: "Kullanıcı"
        set(v) = p.edit().putString("owner_name", v.trim().take(40)).apply()

    /** Sessizlikte konuşmanın kendiliğinden bitmesi (dakika). */
    var idleMinutes: Int
        get() = p.getInt("idle_minutes", 2)
        set(v) = p.edit().putInt("idle_minutes", v.coerceIn(1, 10)).apply()

    /** Ara ve söyle: mesajın çalındığı kanal (media/alarm/voice); 1.x ses testi sonucu korunur. */
    var callChannel: String
        get() = callAudio.getString("channel", "media")?.takeIf { it in CHANNELS } ?: "media"
        set(v) = callAudio.edit().putString("channel", v).apply()

    var onboarded: Boolean
        get() = p.getBoolean("onboarded", false)
        set(v) = p.edit().putBoolean("onboarded", v).apply()

    /** Yalnız debug derlemesi: sahte Gemini/Mac sunucusu. */
    var devLiveUrl: String
        get() = p.getString("dev_live_url", "") ?: ""
        set(v) = p.edit().putString("dev_live_url", v).apply()

    companion object {
        val CHANNELS = listOf("media", "alarm", "voice", "none")
        val VOICES = listOf("Charon", "Puck", "Kore", "Fenrir", "Aoede", "Orus", "Zephyr", "Leda")
    }
}

/**
 * Telefondaki hafıza (şifreli). Ortak hafızanın ana kaynağı Mac'tir: telefona söylenen notlar Mac'e ulaşana kadar
 * burada bekler (notes), ulaşınca buradan çıkar ve Mac'in listesinde (macItems) görünür. "Unut" istekleri de Mac'e
 * ulaşana kadar sırada bekler (pendingForgets). Mac ile eşleşmemiş telefonda notlar hep burada kalır.
 */
class Memory(private val store: SecureStore) {
    data class Note(val id: String, val text: String, val time: Long)
    data class Turn(val id: String, val user: String, val assistant: String, val time: Long, val synced: Boolean)

    /** Henüz Mac'e ulaşmamış (ya da Mac'siz) telefon notları. */
    @Synchronized
    fun notes(): List<Note> = parseArray(store.get(SecureStore.NOTES)).mapNotNull {
        val text = it.optString("text"); if (text.isBlank()) null else Note(it.optString("id"), text, it.optLong("time"))
    }

    /** Mac'in ortak hafızasındaki kayıtlar (son eşitlemedeki hâli). */
    @Synchronized
    fun macItems(): List<SharedMemory.Item> = SharedMemory.parseItems(jsonArray(store.get(SecureStore.MAC_ITEMS)))

    /** Telefonda listelenen tüm hafıza: Mac'teki kayıtlar + henüz gönderilmemiş notlar. */
    @Synchronized
    fun allNotes(): List<Note> = macItems().map { Note("mac:${it.category}/${it.key}", it.text, 0) } + notes()

    @Synchronized
    fun remember(text: String): Note {
        val note = Note(UUID.randomUUID().toString(), text.trim().take(300), System.currentTimeMillis())
        saveNotes((notes().filterNot { TextMatch.fold(it.text) == TextMatch.fold(note.text) } + note).takeLast(60))
        return note
    }

    /**
     * Metni içeren notları siler; silinenleri döndürür. Mac'teki eşleşen kayıtlar listeden hemen düşer ve silme isteği
     * Mac'e iletilmek üzere sıraya girer (queueForMac=false: Mac ile eşleşme yok).
     */
    @Synchronized
    fun forget(match: String, queueForMac: Boolean = true): List<Note> {
        val needle = TextMatch.fold(match)
        if (needle.isEmpty()) return emptyList()
        val all = notes()
        val gone = all.filter { TextMatch.fold(it.text).contains(needle) }
        if (gone.isNotEmpty()) saveNotes(all - gone.toSet())
        val mac = macItems()
        val macGone = mac.filter { TextMatch.fold(it.text).contains(needle) }
        if (queueForMac) {
            if (macGone.isNotEmpty()) saveItems(mac - macGone.toSet())
            val queue = (pendingForgets().filterNot { TextMatch.fold(it) == needle } + match.trim().take(200)).takeLast(20)
            store.put(SecureStore.FORGETS, JSONArray(queue).toString())
        }
        return gone + if (queueForMac) macGone.map { Note("mac:${it.category}/${it.key}", it.text, 0) } else emptyList()
    }

    @Synchronized
    fun pendingForgets(): List<String> = SharedMemory.strings(jsonArray(store.get(SecureStore.FORGETS)))

    private fun jsonArray(raw: String): JSONArray = try { JSONArray(raw.ifBlank { "[]" }) } catch (e: Exception) { JSONArray() }

    /** Mac yanıtı geldi: ulaşan notları ve uygulanan silmeleri sıradan çıkar, Mac'in güncel listesini sakla. */
    @Synchronized
    fun applyMacSync(storedNoteIds: Collection<String>?, forgotten: Collection<String>?, items: List<SharedMemory.Item>?) {
        if (storedNoteIds != null && storedNoteIds.isNotEmpty()) saveNotes(notes().filterNot { it.id in storedNoteIds })
        if (forgotten != null && forgotten.isNotEmpty())
            store.put(SecureStore.FORGETS, JSONArray(pendingForgets().filterNot { it in forgotten }).toString())
        if (items != null) saveItems(items)
    }

    /** Mac eşleşmesi kaldırılınca: Mac'in listesi ve bekleyen silmeler artık geçersiz (telefon notları kalır). */
    @Synchronized
    fun clearMac() { store.remove(SecureStore.MAC_ITEMS); store.remove(SecureStore.FORGETS) }

    private fun saveNotes(list: List<Note>) = store.put(SecureStore.NOTES, JSONArray(list.map {
        JSONObject().put("id", it.id).put("text", it.text).put("time", it.time)
    }).toString())

    private fun saveItems(list: List<SharedMemory.Item>) = store.put(SecureStore.MAC_ITEMS, JSONArray(list.map {
        JSONObject().put("category", it.category).put("key", it.key).put("text", it.text)
    }).toString())

    @Synchronized
    fun turns(): List<Turn> = parseArray(store.get(SecureStore.TURNS)).map {
        Turn(it.optString("id"), it.optString("user"), it.optString("assistant"), it.optLong("time"), it.optBoolean("synced"))
    }

    @Synchronized
    fun addTurn(user: String, assistant: String) {
        if (user.isBlank() || assistant.isBlank()) return
        val t = Turn(UUID.randomUUID().toString(), user.trim().take(4000), assistant.trim().take(6000), System.currentTimeMillis(), false)
        saveTurns((turns() + t).takeLast(30))
    }

    @Synchronized
    fun markSynced(ids: Collection<String>) {
        if (ids.isEmpty()) return
        saveTurns(turns().map { if (it.id in ids) it.copy(synced = true) else it })
    }

    @Synchronized
    fun clearConversations() = store.remove(SecureStore.TURNS)

    private fun saveTurns(list: List<Turn>) = store.put(SecureStore.TURNS, JSONArray(list.map {
        JSONObject().put("id", it.id).put("user", it.user).put("assistant", it.assistant)
            .put("time", it.time).put("synced", it.synced)
    }).toString())

    var macContext: String
        get() = store.get(SecureStore.MAC_CONTEXT)
        set(v) = store.put(SecureStore.MAC_CONTEXT, v.take(9000))

    private fun parseArray(raw: String): List<JSONObject> = try {
        val a = JSONArray(raw.ifEmpty { "[]" }); (0 until a.length()).mapNotNull { a.optJSONObject(it) }
    } catch (e: Exception) { emptyList() }
}
