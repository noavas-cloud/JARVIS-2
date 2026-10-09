package com.kemal.jarvis.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.os.SystemClock
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.math.sqrt

/** 16 bit PCM bloğunun 0..1 arası ses düzeyi (küre animasyonu için). */
fun pcmLevel(pcm: ByteArray, length: Int = pcm.size): Float {
    var sum = 0.0
    var n = 0
    var i = 0
    while (i + 1 < length) {
        val s = (pcm[i].toInt() and 0xff) or (pcm[i + 1].toInt() shl 8)
        sum += s.toDouble() * s
        n++
        i += 2
    }
    if (n == 0) return 0f
    val rms = sqrt(sum / n) / 32768.0
    return (rms * 4.0).coerceIn(0.0, 1.0).toFloat()
}

/**
 * Konuşma sırasında ses yolu: iletişim modu (telefonun yankı gidericisi devreye girer), kulaklık yoksa hoparlör.
 * Ses odağı alınır; başka bir uygulama kalıcı olarak çalmaya başlarsa onFocusLost çağrılır.
 */
class AudioRoute(context: Context, private val onFocusLost: () -> Unit) {
    private val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var focus: AudioFocusRequest? = null
    private var previousMode = AudioManager.MODE_NORMAL
    private var active = false

    val attributes: AudioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()

    fun headsetConnected(): Boolean = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any {
        it.type == AudioDeviceInfo.TYPE_WIRED_HEADSET || it.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES ||
            it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || it.type == AudioDeviceInfo.TYPE_USB_HEADSET ||
            (Build.VERSION.SDK_INT >= 31 && it.type == AudioDeviceInfo.TYPE_BLE_HEADSET)
    }

    @Suppress("DEPRECATION")
    fun enter() {
        if (active) return
        active = true
        previousMode = am.mode
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(attributes)
            .setOnAudioFocusChangeListener { change -> if (change == AudioManager.AUDIOFOCUS_LOSS) onFocusLost() }
            .build()
        focus = request
        am.requestAudioFocus(request)
        am.mode = AudioManager.MODE_IN_COMMUNICATION
        if (!headsetConnected()) {
            if (Build.VERSION.SDK_INT >= 31) {
                am.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                    ?.let { am.setCommunicationDevice(it) }
            } else {
                am.isSpeakerphoneOn = true
            }
        }
    }

    @Suppress("DEPRECATION")
    fun exit() {
        if (!active) return
        active = false
        if (Build.VERSION.SDK_INT >= 31) am.clearCommunicationDevice() else am.isSpeakerphoneOn = false
        am.mode = if (previousMode == AudioManager.MODE_IN_COMMUNICATION) AudioManager.MODE_NORMAL else previousMode
        focus?.let { am.abandonAudioFocusRequest(it) }
        focus = null
    }
}

/** Mikrofon: 16 kHz, mono, 16 bit, 40 ms'lik bloklar. Yankı giderici ve gürültü bastırıcı varsa açılır. */
class MicCapture(private val onFrame: (ByteArray, Float) -> Unit) {
    private var recorder: AudioRecord? = null
    private var thread: Thread? = null
    @Volatile private var running = false
    private val effects = ArrayList<android.media.audiofx.AudioEffect>()

    val isRunning get() = running

    /** İzin yoksa ya da mikrofon başka uygulamadaysa false. */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running) return true
        val rate = 16000
        val frame = 1280 // 40 ms
        val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, rate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(min, frame * 4))
        } catch (e: Exception) { return false }
        if (rec.state != AudioRecord.STATE_INITIALIZED) { rec.release(); return false }
        val session = rec.audioSessionId
        if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(session)?.let { it.enabled = true; effects.add(it) }
        if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(session)?.let { it.enabled = true; effects.add(it) }
        if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(session)?.let { it.enabled = true; effects.add(it) }
        try { rec.startRecording() } catch (e: Exception) { release(rec); return false }
        if (rec.recordingState != AudioRecord.RECORDSTATE_RECORDING) { release(rec); return false }
        recorder = rec
        running = true
        thread = Thread({
            val buf = ByteArray(frame)
            while (running) {
                var got = 0
                while (running && got < frame) {
                    val n = rec.read(buf, got, frame - got)
                    if (n <= 0) { if (n < 0) running = false; break }
                    got += n
                }
                if (got == frame) onFrame(buf.copyOf(), pcmLevel(buf))
            }
        }, "jarvis-mic").also { it.start() }
        return true
    }

    fun stop() {
        running = false
        thread?.join(300)
        thread = null
        recorder?.let { release(it) }
        recorder = null
    }

    private fun release(rec: AudioRecord) {
        effects.forEach { try { it.release() } catch (_: Exception) {} }
        effects.clear()
        try { rec.stop() } catch (_: Exception) {}
        rec.release()
    }
}

/** Gemini sesini (24 kHz PCM) çalar. interrupt() kuyruğu anında boşaltır. */
class VoicePlayer(private val attributes: AudioAttributes, private val onLevel: (Float) -> Unit) {
    private val queue = LinkedBlockingQueue<ByteArray>()
    private var track: AudioTrack? = null
    private var thread: Thread? = null
    @Volatile private var running = false
    @Volatile private var generation = 0
    @Volatile private var lastWriteEnd = 0L

    /** Kuyrukta ses var ya da son yazılan ses hâlâ hoparlörden çıkıyor. */
    val isSpeaking: Boolean get() = queue.isNotEmpty() || SystemClock.elapsedRealtime() < lastWriteEnd

    fun start(): Boolean {
        if (running) return true
        val rate = 24000
        val min = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val t = try {
            AudioTrack.Builder().setAudioAttributes(attributes)
                .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setBufferSizeInBytes(maxOf(min, 9600)).setTransferMode(AudioTrack.MODE_STREAM).build()
        } catch (e: Exception) { return false }
        if (t.state != AudioTrack.STATE_INITIALIZED) { t.release(); return false }
        t.play()
        track = t
        running = true
        thread = Thread({ loop(t) }, "jarvis-speaker").also { it.start() }
        return true
    }

    private fun loop(t: AudioTrack) {
        while (running) {
            val chunk = queue.poll(150, TimeUnit.MILLISECONDS)
            if (chunk == null) { onLevel(0f); continue }
            val gen = generation
            onLevel(pcmLevel(chunk))
            // Bu parçanın ne zaman biteceği yazmaya başlamadan önce bilinmeli: uzun bir parça yazılırken de "konuşuyor" sayılır.
            lastWriteEnd = maxOf(lastWriteEnd, SystemClock.elapsedRealtime()) + chunk.size / 48L + 120L
            var done = 0
            while (running && done < chunk.size && gen == generation) {
                val step = minOf(4800, chunk.size - done)
                val n = t.write(chunk, done, step)
                if (n <= 0) break
                done += n
            }
        }
    }

    fun enqueue(pcm: ByteArray) { if (running && pcm.isNotEmpty()) queue.offer(pcm) }

    fun interrupt() {
        generation++
        queue.clear()
        track?.let { try { it.pause(); it.flush(); it.play() } catch (_: Exception) {} }
        lastWriteEnd = 0L
        onLevel(0f)
    }

    fun stop() {
        running = false
        queue.clear()
        thread?.join(300)
        thread = null
        track?.let { try { it.stop() } catch (_: Exception) {}; it.release() }
        track = null
        lastWriteEnd = 0L
    }
}
