package com.kemal.jarvis.conv

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import com.kemal.jarvis.BuildConfig
import com.kemal.jarvis.JarvisApp
import com.kemal.jarvis.audio.AudioRoute
import com.kemal.jarvis.audio.MicCapture
import com.kemal.jarvis.audio.VoicePlayer
import com.kemal.jarvis.call.CallSpeakerService
import com.kemal.jarvis.call.GeminiTts
import com.kemal.jarvis.core.Memory
import com.kemal.jarvis.core.SecureStore
import com.kemal.jarvis.core.Settings
import com.kemal.jarvis.core.TextMatch
import com.kemal.jarvis.live.LiveClient
import com.kemal.jarvis.live.LiveProtocol
import com.kemal.jarvis.live.LiveProtocol.Event
import com.kemal.jarvis.mac.MacLink
import com.kemal.jarvis.service.JarvisService
import com.kemal.jarvis.tools.PhoneTools
import com.kemal.jarvis.tools.ToolDecl
import com.kemal.jarvis.tools.ToolHost
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * Tek konuşma: telefonda Gemini canlı ses + telefon araçları + (bağlıysa) Mac araçları.
 * Bütün durum değişiklikleri tek iş parçacıklı "engine" kuyruğunda yapılır.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class Conversation(private val app: JarvisApp) : ToolHost {
    enum class Phase { IDLE, CONNECTING, LISTENING, THINKING, SPEAKING }
    enum class Who { USER, JARVIS, ACTION, NOTICE }
    data class Line(val id: Long, val who: Who, val text: String, val time: Long = System.currentTimeMillis())
    data class Confirm(val id: Long, val title: String, val lines: List<String>, val confirmLabel: String, val expiresAt: Long)
    data class State(
        val phase: Phase = Phase.IDLE,
        val micOn: Boolean = false,
        val lines: List<Line> = emptyList(),
        val partialUser: String = "",
        val partialJarvis: String = "",
        val confirm: Confirm? = null,
        val error: String? = null,
        val halfDuplex: Boolean = false,
    )

    override val context: Context get() = app
    override val memory: Memory get() = app.memory
    override val settings: Settings get() = app.settings

    private val engine = Dispatchers.Default.limitedParallelism(1)
    private val scope = CoroutineScope(SupervisorJob() + engine)
    private val ids = AtomicLong(1)
    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(0, TimeUnit.SECONDS)
        .pingInterval(30, TimeUnit.SECONDS).build()

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state
    private val _level = MutableStateFlow(0f)
    /** Küre animasyonu için anlık ses düzeyi (0..1). */
    val level: StateFlow<Float> = _level

    val phoneTools = PhoneTools(this)

    // ── Oturum durumu (yalnız engine'de değişir) ──
    private var client: LiveClient? = null
    @Volatile private var ready = false
    @Volatile private var micWanted = false
    @Volatile private var halfDuplex = false
    @Volatile private var suppressAudio = false
    private var route: AudioRoute? = null
    private var mic: MicCapture? = null
    private var player: VoicePlayer? = null
    private var resumeHandle: String? = null
    private var reconnects = 0
    private var searchOn = true
    private var setupTried = false
    private var macTools: Map<String, MacLink.Tool> = emptyMap()
    private var lastActivity = 0L
    private var ticker: Job? = null
    private var pendingTexts = ArrayList<String>()
    private var turnUser = StringBuilder()
    private var turnJarvis = StringBuilder()
    private var speakingSince = 0L
    private var userSpokeWhileSpeaking = false
    private var selfInterrupts = 0
    private var endAfterTurn = false
    private var stopAfterTool = false
    private val toolJobs = HashMap<String, Job>()
    private var confirmWaiter: CompletableDeferred<Boolean>? = null
    private var holdsMac = false

    val active: Boolean get() = _state.value.phase != Phase.IDLE

    // ── Dışarıdan çağrılanlar (UI, bildirim, widget) ────────────────────────────────────────────

    fun start(withMic: Boolean = true) { scope.launch { startInternal(withMic) } }
    fun stop(reason: String? = null) { scope.launch { stopInternal(reason) } }
    fun toggleMic() { scope.launch { if (_state.value.phase == Phase.IDLE) startInternal(true) else setMic(!micWanted) } }
    fun sendText(text: String) {
        val t = text.trim().take(4000)
        if (t.isEmpty()) return
        scope.launch {
            addLine(Who.USER, t)
            turnUser.append(t).append(' ')
            lastActivity = SystemClock.elapsedRealtime()
            if (ready) { client?.send(LiveProtocol.text(t)); setPhase(Phase.THINKING) }
            else { pendingTexts.add(t); if (_state.value.phase == Phase.IDLE) startInternal(false) }
        }
    }

    /** Küreye dokunma: JARVIS konuşuyorsa susturur. */
    fun interrupt() {
        scope.launch {
            if (player?.isSpeaking == true) {
                suppressAudio = true
                player?.interrupt()
                commitJarvis(cut = true)
                setPhase(Phase.LISTENING)
            }
        }
    }

    fun answerConfirm(id: Long, approved: Boolean) {
        scope.launch {
            if (_state.value.confirm?.id == id) {
                _state.update { it.copy(confirm = null) }
                confirmWaiter?.complete(approved)
                confirmWaiter = null
                JarvisApp.cancelConfirmNotice(app)
            }
        }
    }

    fun clearError() = _state.update { it.copy(error = null) }
    fun clearLines() = _state.update { it.copy(lines = emptyList()) }

    // ── Başlat / bitir ──────────────────────────────────────────────────────────────────────────

    private suspend fun startInternal(withMic: Boolean) {
        if (_state.value.phase != Phase.IDLE) { if (withMic) setMic(true); return }
        val dev = BuildConfig.DEV_ENDPOINTS && settings.devLiveUrl.isNotEmpty()
        val key = app.store.get(SecureStore.API_KEY).ifEmpty { if (dev) "dev-key" else "" }
        if (key.isEmpty()) {
            _state.update { it.copy(error = "Gemini anahtarı yok. Ayarlar › Gemini anahtarı bölümünden gir ya da Mac'ten aktar.") }
            return
        }
        if (withMic && !hasPermission(Manifest.permission.RECORD_AUDIO)) {
            _state.update { it.copy(error = "Mikrofon izni yok. Konuşmak için izin ver; yazarak da kullanabilirsin.") }
            return
        }
        _state.update { it.copy(phase = Phase.CONNECTING, error = null, micOn = withMic, partialUser = "", partialJarvis = "", halfDuplex = settings.halfDuplex) }
        micWanted = withMic
        halfDuplex = settings.halfDuplex
        resumeHandle = null; reconnects = 0; searchOn = settings.webSearch; setupTried = false
        selfInterrupts = 0; endAfterTurn = false; stopAfterTool = false; suppressAudio = false
        turnUser.clear(); turnJarvis.clear()
        lastActivity = SystemClock.elapsedRealtime()
        JarvisService.start(app)
        if (!holdsMac) { app.mac.acquire(); holdsMac = true }

        // Mac: ortak geçmişi eşitle ve araçlarını al (en çok ~5 sn; Mac yoksa telefon araçlarıyla devam).
        var macState = Prompt.Mac.UNPAIRED
        macTools = emptyMap()
        if (app.mac.isPaired) {
            val result = withTimeoutOrNull(6000) {
                val sync = scope.async(Dispatchers.IO) { app.mac.sync() }
                val tools = app.mac.fetchTools()
                sync.await()
                tools
            }
            macState = when {
                result == null -> Prompt.Mac.OFFLINE
                app.mac.status.value.state == MacLink.State.NO_AGENT -> Prompt.Mac.NO_AGENT
                else -> Prompt.Mac.ONLINE
            }
            if (result != null && macState == Prompt.Mac.ONLINE) macTools = result.associateBy { "mac_" + it.name }
        }
        if (_state.value.phase != Phase.CONNECTING) return // bu arada durduruldu
        systemText = Prompt.build(LocalDateTime.now(), settings.ownerName, "${Build.MANUFACTURER} ${Build.MODEL}",
            memory.notes().map { it.text }, memory.turns(), memory.macContext, macState, settings.webSearch)
        declarations = phoneTools.declarations + macTools.map { (name, t) ->
            val o = JSONObject().put("name", name).put("description", "[MAC'te çalışır] " + t.description)
            t.parameters?.let { o.put("parameters", it) }
            o
        }

        val r = AudioRoute(app) { stop("Başka bir uygulama sesi aldı; dinlemeyi kapattım.") }
        route = r
        r.enter()
        player = VoicePlayer(r.attributes) { lvl -> if (player?.isSpeaking == true) _level.value = lvl }.also { it.start() }
        if (withMic) setMic(true)
        connect()
        ticker?.cancel()
        ticker = scope.launch { tick() }
    }

    private var systemText = ""
    private var declarations: List<JSONObject> = emptyList()

    private fun liveUrl(): String =
        if (BuildConfig.DEV_ENDPOINTS && settings.devLiveUrl.isNotEmpty()) settings.devLiveUrl
        else LiveProtocol.HOST + LiveProtocol.PATH

    private fun connect() {
        ready = false
        client?.close()
        val key = app.store.get(SecureStore.API_KEY).ifEmpty { "dev-key" }
        val c = LiveClient(http, liveUrl(), key, object : LiveClient.Listener {
            override fun onEvents(events: List<Event>) { scope.launch { if (client === thisClient) handle(events) } }
            override fun onClosed(code: Int, reason: String, opened: Boolean) { scope.launch { if (client === thisClient) closed(code, reason) } }
        })
        thisClient = c
        client = c
        c.open(LiveProtocol.setup(systemText, settings.voice, declarations, searchOn, resumeHandle))
    }
    private var thisClient: LiveClient? = null

    private suspend fun stopInternal(reason: String?) {
        if (_state.value.phase == Phase.IDLE) return
        ticker?.cancel(); ticker = null
        toolJobs.values.forEach { it.cancel() }; toolJobs.clear()
        confirmWaiter?.complete(false); confirmWaiter = null
        ready = false
        client?.close(); client = null; thisClient = null
        mic?.stop(); mic = null
        player?.stop(); player = null
        route?.exit(); route = null
        commitUser(); commitJarvis(cut = false)
        recordTurn()
        pendingTexts.clear()
        _level.value = 0f
        _state.update { it.copy(phase = Phase.IDLE, micOn = false, partialUser = "", partialJarvis = "", confirm = null) }
        if (reason != null) addLine(Who.NOTICE, reason)
        JarvisService.stop(app)
        JarvisApp.cancelConfirmNotice(app)
        if (holdsMac) { app.mac.release(); holdsMac = false }
        if (app.mac.isPaired) scope.launch(Dispatchers.IO) { app.mac.sync() }
    }

    private fun setMic(on: Boolean) {
        if (on && !hasPermission(Manifest.permission.RECORD_AUDIO)) {
            _state.update { it.copy(error = "Mikrofon izni yok.") }; return
        }
        micWanted = on
        if (on && mic == null) {
            val m = MicCapture { pcm, lvl -> onMicFrame(pcm, lvl) }
            if (m.start()) mic = m else {
                micWanted = false
                _state.update { it.copy(error = "Mikrofon açılamadı (başka bir uygulama kullanıyor olabilir).") }
            }
        } else if (!on) {
            mic?.stop(); mic = null
            if (ready) client?.send(LiveProtocol.audioStreamEnd())
        }
        _state.update { it.copy(micOn = micWanted) }
        app.service?.refresh()
    }

    /** Mikrofon iş parçacığından. */
    private fun onMicFrame(pcm: ByteArray, lvl: Float) {
        val speaking = player?.isSpeaking == true
        if (!speaking) _level.value = lvl
        if (!ready || !micWanted) return
        if (halfDuplex && speaking) return
        val c = client ?: return
        if (c.queued() > 256 * 1024) return // ağ çok yavaş: eski sesi biriktirme
        c.send(LiveProtocol.audio(pcm))
    }

    // ── Gemini olayları ─────────────────────────────────────────────────────────────────────────

    private suspend fun handle(events: List<Event>) {
        for (e in events) when (e) {
            is Event.SetupComplete -> {
                ready = true
                setupTried = true
                reconnects = 0
                if (_state.value.phase == Phase.CONNECTING) setPhase(Phase.LISTENING)
                val queued = pendingTexts.toList(); pendingTexts.clear()
                queued.forEach { client?.send(LiveProtocol.text(it)); setPhase(Phase.THINKING) }
            }
            is Event.Audio -> {
                if (suppressAudio) continue
                lastActivity = SystemClock.elapsedRealtime()
                commitUser()
                if (player?.isSpeaking != true) { speakingSince = SystemClock.elapsedRealtime(); userSpokeWhileSpeaking = false }
                player?.enqueue(e.pcm)
                setPhase(Phase.SPEAKING)
            }
            is Event.InputText -> {
                lastActivity = SystemClock.elapsedRealtime()
                if (player?.isSpeaking == true) userSpokeWhileSpeaking = true
                if (suppressAudio) suppressAudio = false // kullanıcı yeni bir şey söyledi
                _state.update { it.copy(partialUser = it.partialUser + e.text) }
            }
            is Event.OutputText -> {
                if (suppressAudio) continue
                commitUser()
                _state.update { it.copy(partialJarvis = it.partialJarvis + e.text) }
            }
            is Event.Interrupted -> {
                val now = SystemClock.elapsedRealtime()
                if (micWanted && player?.isSpeaking == true && !userSpokeWhileSpeaking && now - speakingSince < 2500 && !halfDuplex) {
                    selfInterrupts++
                    if (selfInterrupts >= 2) {
                        halfDuplex = true
                        _state.update { it.copy(halfDuplex = true) }
                        addLine(Who.NOTICE, "Hoparlörün sesi mikrofona karışıyor gibi. JARVIS konuşurken dinlemeyi kapattım; sözünü kesmek için küreye dokun. (Ayarlar'dan değiştirebilirsin.)")
                    }
                }
                player?.interrupt()
                commitJarvis(cut = true)
                setPhase(Phase.LISTENING)
            }
            is Event.GenerationComplete -> {}
            is Event.TurnComplete -> {
                suppressAudio = false
                commitUser(); commitJarvis(cut = false)
                recordTurn()
                if (_state.value.phase == Phase.THINKING) setPhase(Phase.LISTENING)
                if (endAfterTurn) scope.launch {
                    // Vedanın sesi bitsin, sonra kapat.
                    val until = SystemClock.elapsedRealtime() + 8000
                    while (player?.isSpeaking == true && SystemClock.elapsedRealtime() < until) delay(150)
                    stopInternal(null)
                }
            }
            is Event.ToolCalls -> handleTools(e.calls)
            is Event.ToolCancel -> e.ids.forEach { id ->
                val job = toolJobs.remove(id) ?: return@forEach
                if (_state.value.confirm != null) { confirmWaiter?.complete(false); confirmWaiter = null; _state.update { it.copy(confirm = null) } }
                job.cancel()
            }
            is Event.Resumption -> if (e.resumable && e.handle != null) resumeHandle = e.handle
            is Event.GoAway -> { if (resumeHandle != null) { reconnects = 0; connect() } }
            is Event.ServerError -> addLine(Who.NOTICE, "Gemini: ${e.message.take(200)}")
        }
    }

    private suspend fun closed(code: Int, reason: String) {
        if (_state.value.phase == Phase.IDLE) return
        ready = false
        // Kurulum aşamasında kota hatası: web araması olmadan bir kez daha dene (1.x'teki 429 sorunu).
        if (!setupTried && searchOn && LiveProtocol.isQuotaClose(reason)) {
            searchOn = false
            addLine(Who.NOTICE, "Google arama kotası dolu görünüyor; bu konuşmada web araması olmadan devam ediyorum.")
            connect(); return
        }
        val transient = code == -1 && reason != "NO_INTERNET" || code == 1001 || code == 1006 || code == 1011 && !LiveProtocol.isQuotaClose(reason)
        if (setupTried && resumeHandle != null && reconnects < 3 && (transient || code == 1000 && reason.isEmpty())) {
            reconnects++
            delay(600L * reconnects)
            if (_state.value.phase != Phase.IDLE) connect()
            return
        }
        val message = when (reason) {
            "NO_INTERNET" -> "İnternet bağlantısı yok."
            "TIMEOUT" -> "Gemini'ye bağlanırken zaman aşımı oldu."
            "TLS" -> "Güvenli bağlantı kurulamadı (saat/tarih yanlış olabilir)."
            else -> LiveProtocol.explainClose(code, reason)
        }
        stopInternal(null)
        _state.update { it.copy(error = message) }
    }

    // ── Araçlar ────────────────────────────────────────────────────────────────────────────────

    private fun handleTools(calls: List<LiveProtocol.Call>) {
        setPhase(Phase.THINKING)
        lastActivity = SystemClock.elapsedRealtime()
        commitUser()
        val job = scope.launch {
            val responses = ArrayList<Triple<String, String, Any>>()
            for (call in calls) {
                addLine(Who.ACTION, ToolLabels.label(call.name, call.args))
                val result: Any = try {
                    if (call.name.startsWith("mac_")) runMacTool(call)
                    else withContext(Dispatchers.IO) { phoneTools.run(call.name, call.args) }
                } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (e: ActivityNotFoundException) {
                    ToolDecl.result("unsupported", "Bu işi yapacak uygulama telefonda yok.")
                } catch (e: Exception) { ToolDecl.result("error", "İşlem yapılamadı.") }
                ToolLabels.outcome(result)?.let { addLine(Who.ACTION, it) }
                responses.add(Triple(call.id, call.name, result))
            }
            lastActivity = SystemClock.elapsedRealtime()
            // Telefon araması başladıysa konuşmanın mikrofonu/hoparlörü görüşmeyle çakışmasın.
            if (responses.any { (it.third as? JSONObject)?.optString("status") == "dialing" }) stopAfterTool = true
            if (ready) client?.send(LiveProtocol.toolResponse(responses))
            calls.forEach { toolJobs.remove(it.id) }
            if (stopAfterTool) { stopAfterTool = false; stopInternal("Arama başladı; konuşmayı kapattım.") }
        }
        calls.forEach { toolJobs[it.id] = job }
    }

    private suspend fun runMacTool(call: LiveProtocol.Call): Any {
        val tool = macTools[call.name] ?: return ToolDecl.result("unavailable", "Bu Mac aracı şu an yok.")
        if (tool.approval) {
            val lines = listOf(ToolLabels.macTitle(tool.name)) + ToolLabels.argLines(call.args)
            if (!confirm("Mac'te yapılsın mı?", lines, "ONAYLA")) return ToolDecl.result("declined", "Kullanıcı onaylamadı; Mac'te bir şey yapılmadı.")
        }
        return app.mac.runTool(tool.name, call.args, approved = tool.approval)
    }

    // ── ToolHost ───────────────────────────────────────────────────────────────────────────────

    override suspend fun confirm(title: String, lines: List<String>, confirmLabel: String): Boolean =
        confirmFor(title, lines, confirmLabel, 60_000)

    private suspend fun confirmFor(title: String, lines: List<String>, confirmLabel: String, timeoutMs: Long): Boolean {
        withContext(engine) {
            confirmWaiter?.complete(false)
            val waiter = CompletableDeferred<Boolean>()
            confirmWaiter = waiter
            val c = Confirm(ids.getAndIncrement(), title, lines, confirmLabel, System.currentTimeMillis() + timeoutMs)
            _state.update { it.copy(confirm = c) }
            if (!app.uiVisible) JarvisApp.showConfirmNotice(app, title, lines.firstOrNull().orEmpty())
        }
        val waiter = withContext(engine) { confirmWaiter }!!
        val answer = withTimeoutOrNull(timeoutMs) { waiter.await() } ?: false
        withContext(engine) {
            if (confirmWaiter === waiter) { confirmWaiter = null; _state.update { it.copy(confirm = null) } }
            JarvisApp.cancelConfirmNotice(app)
            lastActivity = SystemClock.elapsedRealtime()
        }
        return answer
    }

    override suspend fun ensurePermission(permission: String, why: String): Boolean {
        if (hasPermission(permission)) return true
        val asker = app.permissionAsker ?: return false
        return asker.ask(permission)
    }

    private fun hasPermission(p: String) = app.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    override fun launch(intent: Intent, label: String): Boolean = app.launch(intent, label)

    override fun endAfterTurn() { endAfterTurn = true }

    override suspend fun syncMemory(): Boolean? =
        if (!app.mac.isPaired) null else withTimeoutOrNull(6000) { app.mac.sync() } ?: false

    override fun macForgetMessage(text: String): String? =
        app.mac.lastForgotten.firstOrNull { it.first == text.trim().take(200) }?.second

    override suspend fun callAndSay(name: String, number: String, message: String): JSONObject {
        val r = placeCallAndSay(name, number, message, mac = false, expiresMs = 60_000)
        if (r.optString("status") == "dialing") stopAfterTool = true
        return r
    }

    /** Ara ve söyle (telefondaki istek ya da Mac'ten gelen istek). */
    suspend fun placeCallAndSay(name: String, number: String, message: String, mac: Boolean, expiresMs: Long): JSONObject {
        val (contact, problem) = phoneTools.resolveForCall(name, number)
        if (contact == null) return problem!!
        val channel = settings.callChannel
        val lines = mutableListOf(contact.name + " · " + TextMatch.prettyNumber(contact.number), "Mesaj: \"${message.take(300)}\"",
            "Arama açılınca mesaj hoparlörden 2 kez okunur. Karşı taraf cevap veremez; duyduğu doğrulanamaz.")
        if (channel == "none") lines.add("⚠ Ses testinde karşı taraf hiçbir sesi duymamıştı; mesaj büyük olasılıkla ulaşmaz.")
        val title = if (mac) "Mac arama istiyor" else "Aransın ve mesaj okunsun mu?"
        if (!confirmFor(title, lines, "ARA", expiresMs)) return ToolDecl.result("declined", "Kullanıcı telefonda onaylamadı; arama yapılmadı.", mapOf("contact" to contact.name))
        if (!ensurePermission(Manifest.permission.CALL_PHONE, "Arama yapabilmem için"))
            return ToolDecl.result("no_permission", "Arama izni verilmedi; arama yapılmadı.", mapOf("contact" to contact.name))
        ensurePermission(Manifest.permission.READ_PHONE_STATE, "Aramanın başladığını anlayabilmem için")
        val spoken = TextMatch.spokenCallMessage(message, TextMatch.genitive(settings.ownerName))
        val key = app.store.get(SecureStore.API_KEY)
        val pcmPath = withContext(Dispatchers.IO) {
            try {
                val pcm = withTimeoutOrNull(20_000) { GeminiTts.synthesize(key, spoken, settings.voice) } ?: return@withContext ""
                File(app.cacheDir, "call_message.pcm").apply { writeBytes(pcm) }.path
            } catch (e: Exception) { "" }
        }
        // Aramadan önce konuşmanın sesi kapanmalı; yoksa telefon görüşmesiyle çakışır.
        withContext(engine) { mic?.stop(); mic = null; micWanted = false; player?.interrupt(); route?.exit() }
        CallSpeakerService.start(app, spoken, pcmPath, if (channel == "none") "media" else channel)
        val intent = Intent(Intent.ACTION_CALL, android.net.Uri.parse("tel:" + contact.number))
        if (!app.launch(intent, "Arama")) return ToolDecl.result("needs_tap", "Arama ekranı açılamadı; bildirime dokununca arama başlar.", mapOf("contact" to contact.name))
        val voice = if (pcmPath.isNotEmpty()) "JARVIS'in doğal sesiyle" else "telefonun kendi sesiyle"
        return ToolDecl.result("dialing", "${contact.name} aranıyor. Arama açıldıktan yaklaşık 8 sn sonra mesaj $voice hoparlörden 2 kez okunacak. Karşı tarafın duyduğu doğrulanamaz.",
            mapOf("contact" to contact.name))
    }

    /** Mac'ten gelen "ara ve söyle" isteği. */
    fun handleMacCall(req: MacLink.CallRequest) {
        scope.launch {
            addLine(Who.NOTICE, "Mac'ten arama isteği: ${req.name.ifEmpty { req.number }}")
            val r = withContext(Dispatchers.Default) {
                placeCallAndSay(req.name, req.number, req.message, mac = true, expiresMs = (req.expiresInSec.coerceIn(20, 110)) * 1000L)
            }
            app.mac.sendCallResult(req.id, r.optString("status"), r.optString("message"), r.optString("contact"))
            addLine(Who.NOTICE, "Mac isteği · " + r.optString("message"))
            if (r.optString("status") == "dialing" && _state.value.phase != Phase.IDLE) stopInternal("Arama başladı; konuşmayı kapattım.")
        }
    }

    // ── Yardımcılar ────────────────────────────────────────────────────────────────────────────

    private suspend fun tick() {
        while (scope.isActive && _state.value.phase != Phase.IDLE) {
            delay(150)
            val p = player
            val s = _state.value
            if (p != null && s.phase == Phase.SPEAKING && !p.isSpeaking) {
                setPhase(if (toolJobs.isNotEmpty()) Phase.THINKING else Phase.LISTENING)
                if (!micWanted) _level.value = 0f
            }
            val idleMs = settings.idleMinutes * 60_000L
            if (s.confirm == null && toolJobs.isEmpty() && p?.isSpeaking != true &&
                SystemClock.elapsedRealtime() - lastActivity > idleMs) {
                stopInternal("Bir süredir konuşmadığın için dinlemeyi kapattım.")
            }
        }
    }

    private fun setPhase(p: Phase) {
        if (_state.value.phase == Phase.IDLE && p != Phase.CONNECTING) return
        _state.update { it.copy(phase = p) }
        app.service?.refresh()
    }

    private fun addLine(who: Who, text: String) {
        _state.update { it.copy(lines = (it.lines + Line(ids.getAndIncrement(), who, text)).takeLast(120)) }
    }

    private fun commitUser() {
        val t = _state.value.partialUser.trim()
        if (t.isEmpty()) return
        turnUser.append(t).append(' ')
        _state.update { it.copy(partialUser = "", lines = (it.lines + Line(ids.getAndIncrement(), Who.USER, t)).takeLast(120)) }
    }

    private fun commitJarvis(cut: Boolean) {
        val t = _state.value.partialJarvis.trim()
        if (t.isEmpty()) return
        val shown = if (cut) "$t…" else t
        turnJarvis.append(shown).append(' ')
        _state.update { it.copy(partialJarvis = "", lines = (it.lines + Line(ids.getAndIncrement(), Who.JARVIS, shown)).takeLast(120)) }
    }

    private fun recordTurn() {
        val u = turnUser.toString().trim(); val a = turnJarvis.toString().trim()
        if (u.isNotEmpty() && a.isNotEmpty()) { memory.addTurn(u, a); turnUser.clear(); turnJarvis.clear() }
        else if (a.isNotEmpty()) turnJarvis.clear()
    }
}
