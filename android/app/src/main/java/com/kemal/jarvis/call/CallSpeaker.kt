package com.kemal.jarvis.call

import android.Manifest
import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.telecom.TelecomManager
import com.kemal.jarvis.JarvisApp
import com.kemal.jarvis.R
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.util.Base64
import java.util.Locale
import java.util.concurrent.TimeUnit

/** Mesajı aramadan önce Gemini'nin doğal sesiyle (Charon) 24 kHz PCM'e çevirir. Başarısızsa telefonun sesi kullanılır. */
object GeminiTts {
    const val MODEL = "gemini-3.8-flash-tts"
    const val RATE = 24000
    private const val URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
    private val http = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(40, TimeUnit.SECONDS)
        .callTimeout(45, TimeUnit.SECONDS).followRedirects(false).build()

    fun request(text: String, voice: String): JSONObject {
        val content = JSONObject().put("type", "text").put("text", text)
            .put("annotations", JSONArray().put(JSONObject().put("type", "speech_metadata")
                .put("style", "warm, calm and natural phone voice, clear Turkish pronunciation, moderate pace")))
        return JSONObject().put("model", MODEL)
            .put("input", JSONArray().put(JSONObject().put("type", "user_input").put("content", JSONArray().put(content))))
            .put("response_format", JSONObject().put("type", "audio"))
            .put("generation_config", JSONObject().put("speech_config", JSONArray().put(JSONObject().put("voice", voice))))
    }

    /** steps[].content[] içindeki son ses bloğu. */
    fun audio(response: JSONObject): ByteArray? {
        var last: ByteArray? = null
        val steps = response.optJSONArray("steps") ?: return null
        for (i in 0 until steps.length()) {
            val content = steps.optJSONObject(i)?.optJSONArray("content") ?: continue
            for (j in 0 until content.length()) {
                val part = content.optJSONObject(j) ?: continue
                if (part.optString("type") == "audio" && part.optString("data").isNotEmpty()) {
                    try { last = Base64.getDecoder().decode(part.optString("data")) } catch (_: IllegalArgumentException) {}
                }
            }
        }
        return last
    }

    /** WAV ise "data" bölümü, değilse ham PCM. */
    fun pcm(audio: ByteArray?): ByteArray? {
        if (audio == null || audio.size < 12 || audio[0] != 'R'.code.toByte() || audio[1] != 'I'.code.toByte()) return audio
        var pos = 12
        while (pos + 8 <= audio.size) {
            val size = (audio[pos + 4].toInt() and 255) or ((audio[pos + 5].toInt() and 255) shl 8) or
                ((audio[pos + 6].toInt() and 255) shl 16) or ((audio[pos + 7].toInt() and 255) shl 24)
            if (String(audio, pos, 4, Charsets.US_ASCII) == "data") {
                val start = pos + 8
                val len = maxOf(0, minOf(if (size < 0) Int.MAX_VALUE else size, audio.size - start))
                return ByteArrayOutputStream(len).apply { write(audio, start, len - (len and 1)) }.toByteArray()
            }
            if (size < 0) break
            pos += 8 + size + (size and 1)
        }
        return null
    }

    /** Ağ çağrısı (ana iş parçacığında çağırma). */
    fun synthesize(key: String, text: String, voice: String = "Charon"): ByteArray {
        if (key.isEmpty()) throw IOException("Gemini anahtarı yok")
        val req = Request.Builder().url(URL).header("x-goog-api-key", key)
            .post(request(text, voice).toString().toRequestBody("application/json; charset=utf-8".toMediaType())).build()
        http.newCall(req).execute().use { r ->
            val raw = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("Gemini ses isteği reddedildi (${r.code})")
            val pcm = try { pcm(audio(JSONObject(raw))) } catch (e: Exception) { null }
            if (pcm == null || pcm.size < RATE / 5) throw IOException("Gemini ses döndürmedi")
            return pcm
        }
    }
}

/**
 * Arama sürerken mesajı hoparlörden çalar: arama başlayınca ~8 sn bekler, 2 kez çalar, arama biterse durur.
 * Android, uygulamaların sesi görüşme hattına doğrudan vermesine izin vermez; ses hoparlörden çıkar ve telefonun
 * mikrofonu karşıya iletir. Karşı tarafın duyduğu doğrulanamaz.
 */
class CallSpeakerService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var text = ""
    private var pcmPath = ""
    private var channel = "media"
    private var startedAt = 0L
    private var inCallSince = 0L
    private var played = 0
    private var playing = false
    private var usedFallback = false
    @Volatile private var track: AudioTrack? = null
    private var savedStream = -1
    private var savedVolume = -1
    private var routed = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val t = intent?.getStringExtra(EXTRA_TEXT)
        if (t.isNullOrEmpty()) { stopSelf(); return START_NOT_STICKY }
        text = t
        pcmPath = intent.getStringExtra(EXTRA_PCM).orEmpty()
        channel = intent.getStringExtra(EXTRA_CHANNEL)?.takeIf { it in listOf("media", "alarm", "voice") } ?: "media"
        foreground("Arama başlayınca mesaj hoparlörden çalınacak.")
        startedAt = SystemClock.elapsedRealtime(); inCallSince = 0; played = 0; playing = false; usedFallback = false
        if (tts == null) tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val r = tts?.setLanguage(Locale("tr", "TR"))
                ttsReady = r != TextToSpeech.LANG_MISSING_DATA && r != TextToSpeech.LANG_NOT_SUPPORTED
            }
        }
        main.removeCallbacksAndMessages(null)
        main.postDelayed(::tick, POLL_MS)
        return START_NOT_STICKY
    }

    private fun inCall(): Boolean {
        if (checkSelfPermission(Manifest.permission.READ_PHONE_STATE) != PackageManager.PERMISSION_GRANTED) return true
        return try { (getSystemService(Context.TELECOM_SERVICE) as TelecomManager).isInCall } catch (e: SecurityException) { true }
    }

    private fun tick() {
        val now = SystemClock.elapsedRealtime()
        val call = inCall()
        if (inCallSince == 0L) {
            if (call) inCallSince = now
            else if (now - startedAt > WAIT_FOR_CALL_MS) { finish("Arama başlamadı; mesaj çalınmadı."); return }
        } else if (!call) {
            finish(if (played > 0) "Arama bitti; mesaj $played kez çalındı." else "Arama, mesaj çalınmadan bitti."); return
        }
        if (inCallSince != 0L && !playing && now - inCallSince >= DELAY_MS) playNext()
        main.postDelayed(::tick, POLL_MS)
    }

    private fun playNext() {
        if (played >= REPEATS) {
            finish("Mesaj $played kez çalındı${if (usedFallback) " (telefonun kendi sesiyle)" else ""}. Karşı tarafın duyduğu doğrulanamaz.")
            return
        }
        playing = true
        prepareRoute()
        val pcm = readPcm(pcmPath)
        if (pcm != null) playPcm(pcm) else speak()
    }

    private fun done() { main.postDelayed({ played++; playing = false; if (played >= REPEATS) playNext() }, GAP_MS) }

    private fun readPcm(path: String): ByteArray? {
        if (path.isEmpty()) return null
        val f = File(path)
        if (!f.isFile || f.length() < 1000 || f.length() > 20_000_000) return null
        return try { f.readBytes().let { it.copyOf(it.size and 1.inv()) } } catch (e: IOException) { null }
    }

    private fun attributes(): AudioAttributes = AudioAttributes.Builder()
        .setUsage(when (channel) { "alarm" -> AudioAttributes.USAGE_ALARM; "voice" -> AudioAttributes.USAGE_VOICE_COMMUNICATION; else -> AudioAttributes.USAGE_MEDIA })
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()

    private fun stream(): Int = when (channel) { "alarm" -> AudioManager.STREAM_ALARM; "voice" -> AudioManager.STREAM_VOICE_CALL; else -> AudioManager.STREAM_MUSIC }

    private fun playPcm(pcm: ByteArray) {
        Thread({
            var current: AudioTrack? = null
            try {
                current = AudioTrack.Builder().setAudioAttributes(attributes())
                    .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(GeminiTts.RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setBufferSizeInBytes(maxOf(AudioTrack.getMinBufferSize(GeminiTts.RATE, AudioFormat.CHANNEL_OUT_MONO,
                        AudioFormat.ENCODING_PCM_16BIT), 9600)).build()
                track = current
                current.setVolume(1f)
                current.play()
                var off = 0
                while (off < pcm.size && track === current) {
                    val n = current.write(pcm, off, minOf(4800, pcm.size - off), AudioTrack.WRITE_BLOCKING)
                    if (n <= 0) break
                    off += n
                }
                SystemClock.sleep(300)
            } catch (e: RuntimeException) {
                main.post { usedFallback = true; speak() }
                return@Thread
            } finally {
                current?.let { try { it.stop() } catch (_: RuntimeException) {}; it.release() }
                if (track === current) track = null
            }
            main.post(::done)
        }, "jarvis-call-audio").start()
    }

    private fun speak() {
        val engine = tts
        if (!ttsReady || engine == null) { finish("Telefonda Türkçe konuşma sesi yok ve Gemini sesi hazırlanamadı; mesaj çalınmadı."); return }
        usedFallback = usedFallback || pcmPath.isEmpty()
        engine.setAudioAttributes(attributes())
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) { main.post(::done) }
            @Deprecated("Deprecated in Java")
            override fun onError(id: String?) { main.post { finish("Mesaj çalınırken ses hatası oluştu.") } }
        })
        engine.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle().apply { putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1f) }, "jarvis-call-$played")
    }

    /** Hoparlör + seçilen kanal en yüksek ses (bitince eski düzeye döner). Etkisi cihaza göre değişebilir. */
    @Suppress("DEPRECATION")
    private fun prepareRoute() {
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        try {
            restoreVolume(audio)
            savedStream = stream()
            savedVolume = audio.getStreamVolume(savedStream)
            audio.setStreamVolume(savedStream, audio.getStreamMaxVolume(savedStream), 0)
            if (Build.VERSION.SDK_INT >= 31) {
                if (!routed) audio.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                    ?.let { routed = audio.setCommunicationDevice(it) }
            } else audio.isSpeakerphoneOn = true
        } catch (_: RuntimeException) {}
    }

    private fun restoreVolume(audio: AudioManager) {
        if (savedStream >= 0 && savedVolume >= 0) try { audio.setStreamVolume(savedStream, savedVolume, 0) } catch (_: RuntimeException) {}
        savedStream = -1; savedVolume = -1
    }

    private fun releaseRoute() {
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        restoreVolume(audio)
        try { if (Build.VERSION.SDK_INT >= 31 && routed) audio.clearCommunicationDevice() } catch (_: RuntimeException) {}
        routed = false
    }

    private fun finish(message: String) {
        main.removeCallbacksAndMessages(null)
        track = null
        tts?.stop()
        releaseRoute()
        playing = false
        JarvisApp.notifyInfo(this, "JARVIS · ara ve söyle", message)
        if (pcmPath.isNotEmpty()) File(pcmPath).delete()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun foreground(message: String) {
        val n = Notification.Builder(this, JarvisApp.CH_CALL).setSmallIcon(R.drawable.ic_stat_jarvis)
            .setContentTitle("JARVIS · ara ve söyle").setContentText(message).setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= 29) startForeground(NOTE_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        else startForeground(NOTE_ID, n)
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        track = null
        releaseRoute()
        tts?.let { it.stop(); it.shutdown() }
        tts = null
        super.onDestroy()
    }

    companion object {
        const val EXTRA_TEXT = "text"
        const val EXTRA_PCM = "pcm"
        const val EXTRA_CHANNEL = "channel"
        const val DELAY_MS = 8000L
        const val REPEATS = 2
        private const val POLL_MS = 500L
        private const val GAP_MS = 1200L
        private const val WAIT_FOR_CALL_MS = 45_000L
        private const val NOTE_ID = 4101

        fun start(context: Context, text: String, pcmPath: String, channel: String) {
            context.startForegroundService(Intent(context, CallSpeakerService::class.java)
                .putExtra(EXTRA_TEXT, text).putExtra(EXTRA_PCM, pcmPath).putExtra(EXTRA_CHANNEL, channel))
        }
    }
}
