package com.kemal.jarvis.mac

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import com.kemal.jarvis.core.Memory
import com.kemal.jarvis.core.SecureStore
import com.kemal.jarvis.core.SharedMemory
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Eşleşmiş Mac ile bağlantı. Mac'in araçları telefondaki JARVIS'in araçlarına eklenir ("mac_" önekiyle);
 * tek konuşmada hem telefon hem Mac işleri yapılır. Ayrıca Mac'ten gelen "ara ve söyle" istekleri buradan alınır.
 */
class MacLink(
    context: Context,
    private val store: SecureStore,
    private val memory: Memory,
    private val scope: CoroutineScope,
    private val devHttp: Boolean,
) {
    enum class State { UNPAIRED, CONNECTING, ONLINE, NO_AGENT, OFFLINE, REVOKED }
    data class Status(val state: State, val name: String = "", val detail: String = "")
    data class Pairing(val url: String, val token: String, val name: String)
    data class Tool(val name: String, val description: String, val parameters: JSONObject?, val approval: Boolean)
    data class CallRequest(val id: String, val name: String, val number: String, val message: String, val expiresInSec: Int)

    class MacError(message: String, val revoked: Boolean = false) : IOException(message)

    private val app = context.applicationContext
    private val json = "application/json; charset=utf-8".toMediaType()
    private val http = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(75, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS).callTimeout(80, TimeUnit.SECONDS)
        .followRedirects(false).followSslRedirects(false).build()
    private val linkHttp = http.newBuilder().readTimeout(0, TimeUnit.SECONDS).callTimeout(0, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS).build()

    private val _status = MutableStateFlow(if (pairing() == null) Status(State.UNPAIRED) else Status(State.OFFLINE, pairing()!!.name))
    val status: StateFlow<Status> = _status

    /** Mac'ten "ara ve söyle" isteği geldiğinde (ana iş parçacığında değil). */
    @Volatile var onCallRequest: ((CallRequest) -> Unit)? = null

    private var users = 0
    private var socket: WebSocket? = null
    private var loop: Job? = null
    private var attempt = 0
    @Volatile private var wake: (() -> Unit)? = null

    init {
        val cm = app.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        try {
            cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) { attempt = 0; wake?.invoke() }
            })
        } catch (_: Exception) {}
    }

    fun pairing(): Pairing? {
        val raw = store.get(SecureStore.MAC_PAIRING)
        if (raw.isEmpty()) return null
        return try {
            val o = JSONObject(raw)
            Pairing(o.getString("url"), o.getString("token"), o.optString("name", "JARVIS Mac"))
        } catch (e: Exception) { null }
    }

    val isPaired get() = pairing() != null
    val isOnline get() = _status.value.state == State.ONLINE || _status.value.state == State.NO_AGENT

    // ── Bağlantı yaşam döngüsü: ekran açıkken ya da konuşma sürerken bağlı kalır ──────────────────────

    @Synchronized fun acquire() { users++; if (users == 1) startLoop() }

    @Synchronized fun release() { users = maxOf(0, users - 1); if (users == 0) stopLoop() }

    private fun startLoop() {
        if (loop?.isActive == true || pairing() == null) return
        loop = scope.launch(Dispatchers.IO) {
            while (true) {
                val p = pairing() ?: run { _status.value = Status(State.UNPAIRED); return@launch }
                if (_status.value.state == State.REVOKED) return@launch
                if (_status.value.state != State.ONLINE && _status.value.state != State.NO_AGENT)
                    _status.value = Status(State.CONNECTING, p.name)
                val closed = kotlinx.coroutines.CompletableDeferred<Pair<Int, String>>()
                wake = null
                val ws = linkHttp.newWebSocket(
                    Request.Builder().url(MacRules.wsUrl(p.url, "/ws/android/link"))
                        .header("Authorization", "Bearer ${p.token}").build(),
                    object : WebSocketListener() {
                        override fun onOpen(webSocket: WebSocket, response: Response) { attempt = 0 }
                        override fun onMessage(webSocket: WebSocket, text: String) = handleLink(webSocket, text, p)
                        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                            webSocket.close(1000, null); closed.complete(code to reason)
                        }
                        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { closed.complete(code to reason) }
                        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                            closed.complete((response?.code ?: -1) to (response?.message ?: t.javaClass.simpleName))
                        }
                    })
                synchronized(this@MacLink) { socket = ws }
                val (code, reason) = closed.await()
                synchronized(this@MacLink) { if (socket === ws) socket = null }
                if (MacRules.isRevoked(code) || code == 401 || code == 403) {
                    _status.value = Status(State.REVOKED, p.name, MacRules.explainHttp(401)); return@launch
                }
                val detail = when {
                    code == 4009 -> "Bu telefon Mac'e başka bir yerden bağlandı."
                    code == 404 -> MacRules.explainHttp(404)
                    code in 400..599 -> MacRules.explainHttp(code)
                    code == -1 -> MacRules.explainNetwork(reason, p.url)
                    else -> "Mac bağlantısı kapandı."
                }
                _status.value = Status(State.OFFLINE, p.name, detail)
                val wait = MacRules.backoffMs(attempt++)
                val woke = kotlinx.coroutines.CompletableDeferred<Unit>()
                wake = { woke.complete(Unit) }
                kotlinx.coroutines.withTimeoutOrNull(wait) { woke.await() }
            }
        }
    }

    private fun stopLoop() {
        loop?.cancel(); loop = null
        socket?.close(1000, "bye"); socket = null
        if (pairing() != null && _status.value.state != State.REVOKED) _status.value = Status(State.OFFLINE, pairing()!!.name)
    }

    /** Ağ geldiğinde ya da kullanıcı "yeniden dene" dediğinde beklemeden dener. */
    fun retryNow() { attempt = 0; if (_status.value.state == State.REVOKED) return; wake?.invoke() ?: run { if (users > 0) startLoop() } }

    private fun handleLink(ws: WebSocket, text: String, p: Pairing) {
        val o = try { JSONObject(text) } catch (e: Exception) { return }
        when (o.optString("type")) {
            "hello", "agent_status" -> {
                val agent = o.optBoolean("agent_connected")
                _status.value = Status(if (agent) State.ONLINE else State.NO_AGENT, o.optString("server_name", p.name).ifEmpty { p.name },
                    if (agent) "" else "Mac'teki JARVIS çalışmıyor: yalnız ortak hafıza kullanılabilir.")
            }
            "call_request" -> {
                val id = o.optString("id")
                if (id.isEmpty()) return
                onCallRequest?.invoke(CallRequest(id, o.optString("name").take(80), o.optString("number").take(32),
                    o.optString("message").take(600), o.optInt("expires_in", 110)))
            }
        }
    }

    fun sendCallResult(id: String, status: String, detail: String, contact: String) {
        socket?.send(JSONObject().put("type", "call_result").put("id", id).put("status", status)
            .put("detail", detail.take(400)).put("contact", contact.take(80)).toString())
    }

    // ── HTTP uç noktaları ─────────────────────────────────────────────────────────────────────────

    private fun call(p: Pairing?, method: String, path: String, body: JSONObject? = null, base: String? = null): JSONObject {
        val url = (base ?: p?.url ?: throw MacError("Mac ile eşleşmedin.")) + path
        val builder = Request.Builder().url(url)
        if (p != null) builder.header("Authorization", "Bearer ${p.token}")
        when (method) {
            "GET" -> builder.get()
            "DELETE" -> builder.delete()
            else -> builder.post((body ?: JSONObject()).toString().toRequestBody(json))
        }
        try {
            http.newCall(builder.build()).execute().use { r ->
                val text = r.body?.string().orEmpty()
                if (!r.isSuccessful) {
                    val detail = try { JSONObject(text).optString("detail") } catch (e: Exception) { "" }
                    if (r.code == 401 || r.code == 403) {
                        if (p != null) _status.value = Status(State.REVOKED, p.name, MacRules.explainHttp(401))
                        throw MacError(detail.ifEmpty { MacRules.explainHttp(r.code) }, revoked = true)
                    }
                    throw MacError(if (r.code == 400 && detail.isNotEmpty()) detail else MacRules.explainHttp(r.code))
                }
                return try { JSONObject(text) } catch (e: Exception) { throw MacError("Mac'in yanıtı okunamadı.") }
            }
        } catch (e: MacError) { throw e } catch (e: IOException) {
            throw MacError(MacRules.explainNetwork(e.javaClass.simpleName, url))
        }
    }

    suspend fun pair(url: String, code: String, deviceName: String): Pairing = withContext(Dispatchers.IO) {
        val clean = MacRules.validateUrl(url, devHttp) ?: throw MacError("Adres geçerli değil (https:// ile başlamalı).")
        if (!code.matches(Regex("[0-9]{8}"))) throw MacError("Kod 8 rakam olmalı.")
        val r = call(null, "POST", "/api/android/pair", JSONObject().put("code", code).put("device_name", deviceName.take(80)), base = clean)
        val token = r.optString("device_token")
        if (token.isEmpty()) throw MacError("Mac eşleştirmeyi tamamlamadı.")
        val p = Pairing(clean, token, r.optString("server_name", "JARVIS Mac"))
        store.put(SecureStore.MAC_PAIRING, JSONObject().put("url", p.url).put("token", p.token).put("name", p.name).toString())
        _status.value = Status(State.CONNECTING, p.name)
        attempt = 0
        synchronized(this@MacLink) { if (users > 0) { stopLoopKeepStatus(); startLoop() } }
        p
    }

    private fun stopLoopKeepStatus() { loop?.cancel(); loop = null; socket?.close(1000, "bye"); socket = null }

    /** Mac'teki kaydı da siler (Mac'e ulaşılamazsa yalnız telefondan silinir ve bu söylenir). */
    suspend fun unpair(): String = withContext(Dispatchers.IO) {
        val p = pairing()
        var note = "Eşleşme kaldırıldı."
        if (p != null) {
            try { call(p, "DELETE", "/api/android/pair") } catch (e: MacError) {
                if (!e.revoked) note = "Telefondaki eşleşme silindi; Mac'e ulaşılamadığı için Mac'teki kayıt ancak Mac'ten kaldırılabilir."
            }
        }
        synchronized(this@MacLink) { stopLoopKeepStatus() }
        store.remove(SecureStore.MAC_PAIRING)
        memory.macContext = ""
        memory.clearMac()
        _status.value = Status(State.UNPAIRED)
        note
    }

    suspend fun importApiKey(): String = withContext(Dispatchers.IO) {
        val key = call(pairing(), "GET", "/api/android/standalone-key").optString("api_key").trim()
        if (key.isEmpty()) throw MacError("Mac'te Gemini anahtarı yok.")
        store.put(SecureStore.API_KEY, key)
        key
    }

    /** Mac'in telefona açtığı araçlar; Mac'e ulaşılamazsa null. */
    suspend fun fetchTools(): List<Tool>? = withContext(Dispatchers.IO) {
        val p = pairing() ?: return@withContext null
        try {
            val r = call(p, "GET", "/api/android/v2/tools")
            val agent = r.optBoolean("agent_connected")
            _status.value = Status(if (agent) State.ONLINE else State.NO_AGENT, r.optString("server_name", p.name),
                if (agent) "" else "Mac'teki JARVIS çalışmıyor: yalnız ortak hafıza kullanılabilir.")
            val arr = r.optJSONArray("tools") ?: JSONArray()
            (0 until arr.length()).mapNotNull { i ->
                val t = arr.optJSONObject(i) ?: return@mapNotNull null
                val name = t.optString("name")
                if (!name.matches(Regex("[a-z_]{2,40}"))) null
                else Tool(name, t.optString("description").take(1500), t.optJSONObject("parameters"), t.optBoolean("approval", true))
            }
        } catch (e: MacError) {
            if (!e.revoked) _status.value = Status(State.OFFLINE, p.name, e.message ?: "")
            null
        }
    }

    suspend fun runTool(name: String, args: JSONObject, approved: Boolean): String = withContext(Dispatchers.IO) {
        try {
            call(pairing(), "POST", "/api/android/v2/tool",
                JSONObject().put("name", name).put("args", args).put("approved", approved)).optString("result", "Sonuç yok.")
        } catch (e: MacError) { "Mac'te çalıştırılamadı: ${e.message}" }
    }

    private val syncLock = Mutex()

    /** Son eşitlemede Mac'te uygulanan "unut" istekleri: (istenen metin, Mac'in cevabı). */
    @Volatile var lastForgotten: List<Pair<String, String>> = emptyList(); private set

    /**
     * Ortak hafıza: telefondaki tamamlanmış konuşmaları ve henüz gönderilmemiş notları Mac'e bir kez aktarır, bekleyen
     * "unut" isteklerini Mac'te uygular; Mac'in hafızasını, kayıt listesini ve son konuşmalarını alır.
     * Aynı anda tek eşitleme çalışır (bir silme isteği iki kez gidip başka bir kaydı silmesin).
     */
    suspend fun sync(): Boolean = withContext(Dispatchers.IO) {
        val p = pairing() ?: return@withContext false
        syncLock.withLock {
            val pending = memory.turns().filter { !it.synced }.takeLast(30)
            val notes = memory.notes().takeLast(60)
            val forgets = memory.pendingForgets().takeLast(20)
            val body = JSONObject()
                .put("turns", JSONArray(pending.map { JSONObject().put("id", it.id).put("user", it.user).put("assistant", it.assistant) }))
                .put("notes", JSONArray(notes.map { JSONObject().put("id", it.id).put("text", it.text) }))
                .put("forget", JSONArray(forgets))
            try {
                val r = call(p, "POST", "/api/android/sync", body)
                val ack = r.optJSONArray("acknowledged")
                memory.markSynced((0 until (ack?.length() ?: 0)).map { ack!!.optString(it) })
                // Eski Mac sürümü bu alanları göndermez: o zaman notlar telefonda kalır, hiçbir şey kaybolmaz.
                val forgotten = SharedMemory.forgotten(r.optJSONArray("forgotten"))
                memory.applyMacSync(
                    storedNoteIds = r.optJSONArray("notes_stored")?.let { SharedMemory.strings(it) },
                    forgotten = forgotten.map { it.first },
                    items = r.optJSONArray("memory_items")?.let { SharedMemory.parseItems(it) })
                lastForgotten = forgotten
                val ctx = StringBuilder()
                r.optString("saved_memory").takeIf { it.isNotBlank() }?.let { ctx.append("[MAC'TEKİ KAYITLI HAFIZA — telefonla ortak]\n").append(it.take(3500)).append("\n\n") }
                r.optJSONArray("recent_conversations")?.let { arr ->
                    if (arr.length() > 0) ctx.append("[MAC İLE SON KONUŞMALAR]\n")
                    for (i in 0 until arr.length()) {
                        val m = arr.optJSONObject(i) ?: continue
                        val role = m.optString("role", m.optString("who"))
                        val text = m.optString("text", m.optString("content")).take(400)
                        if (text.isNotBlank()) ctx.append(if (role == "user") "Kullanıcı: " else "JARVIS: ").append(text).append('\n')
                    }
                }
                memory.macContext = ctx.toString()
                true
            } catch (e: MacError) { false }
        }
    }

    companion object {
        fun deviceName(): String = "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(80)
    }
}
