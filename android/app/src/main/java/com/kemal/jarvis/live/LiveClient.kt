package com.kemal.jarvis.live

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

/**
 * Tek bir Gemini canlı ses bağlantısı. Anahtar URL'ye değil x-goog-api-key başlığına konur (kayıtlara düşmesin).
 * Olaylar OkHttp iş parçacığında gelir; çağıran kendi sırasına aktarmalıdır.
 */
class LiveClient(
    private val http: OkHttpClient,
    private val url: String,
    private val apiKey: String,
    private val listener: Listener,
) {
    interface Listener {
        fun onEvents(events: List<LiveProtocol.Event>)
        /** opened=false: bağlantı hiç kurulamadı. */
        fun onClosed(code: Int, reason: String, opened: Boolean)
    }

    @Volatile private var socket: WebSocket? = null
    @Volatile private var opened = false
    @Volatile private var finished = false

    fun open(setupJson: String) {
        val request = Request.Builder().url(url).header("x-goog-api-key", apiKey).build()
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                opened = true
                webSocket.send(setupJson)
            }

            override fun onMessage(webSocket: WebSocket, text: String) = deliver(text)
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) = deliver(bytes.utf8())

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                finish(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = finish(code, reason)

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val reason = when {
                    response != null && response.code == 403 -> "HTTP 403 ${response.message}"
                    response != null -> "HTTP ${response.code} ${response.message}"
                    t is UnknownHostException -> "NO_INTERNET"
                    t is SocketTimeoutException -> "TIMEOUT"
                    t is SSLException -> "TLS"
                    else -> t.javaClass.simpleName
                }
                finish(-1, reason)
            }
        })
    }

    private fun deliver(raw: String) {
        if (finished) return
        val events = LiveProtocol.parse(raw)
        if (events.isNotEmpty()) listener.onEvents(events)
    }

    private fun finish(code: Int, reason: String) {
        if (finished) return
        finished = true
        listener.onClosed(code, reason, opened)
    }

    fun send(json: String): Boolean = !finished && socket?.send(json) == true

    /** Bekleyen gönderim kuyruğu (bayt). Çok büyürse ağ yavaştır; mikrofon kareleri atlanabilir. */
    fun queued(): Long = socket?.queueSize() ?: 0L

    fun close() {
        finished = true
        socket?.close(1000, "bye")
        socket = null
    }
}
