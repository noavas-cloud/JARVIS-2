package com.kemal.jarvis.mac

import java.net.URI

/** Mac bağlantısı kuralları (Android'den bağımsız, JVM'de test edilir). */
object MacRules {
    /** Yeniden bağlanma aralığı: 2, 4, 8, 16, sonra hep 30 sn. */
    fun backoffMs(attempt: Int): Long = when {
        attempt <= 0 -> 2000L
        attempt >= 4 -> 30000L
        else -> 2000L shl attempt
    }

    /**
     * Eşleştirme adresi: yalnız https (geçerli sistem sertifikası), kullanıcı adı/parola/parça yok.
     * devHttp=true (yalnız debug derlemesi) emülatörden Mac'e http://10.0.2.2 izni verir.
     */
    fun validateUrl(raw: String?, devHttp: Boolean = false): String? {
        val text = raw?.trim().orEmpty()
        if (text.isEmpty() || text.length > 300) return null
        val uri = try { URI(text) } catch (e: Exception) { return null }
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase() ?: return null
        if (uri.rawUserInfo != null || uri.rawFragment != null || uri.rawQuery != null) return null
        val devHost = host == "10.0.2.2" || host == "127.0.0.1" || host == "localhost"
        if (scheme != "https" && !(devHttp && scheme == "http" && devHost)) return null
        if (!host.matches(Regex("[a-z0-9.-]+")) || host.startsWith(".") || host.endsWith(".")) return null
        val path = (uri.rawPath ?: "").trimEnd('/')
        if (path.isNotEmpty() && !path.matches(Regex("(/[A-Za-z0-9._~-]+)+"))) return null
        val port = if (uri.port == -1) "" else ":${uri.port}"
        return "$scheme://$host$port$path"
    }

    data class PairLink(val url: String, val code: String)

    /** jarvis://pair?url=…&code=12345678 (Mac'teki QR). */
    fun parsePairLink(raw: String?, devHttp: Boolean = false): PairLink? {
        val text = raw?.trim().orEmpty()
        if (!text.startsWith("jarvis://pair?")) return null
        val params = text.removePrefix("jarvis://pair?").split("&").mapNotNull {
            val i = it.indexOf('='); if (i <= 0) null else it.substring(0, i) to java.net.URLDecoder.decode(it.substring(i + 1), "UTF-8")
        }.toMap()
        val url = validateUrl(params["url"], devHttp) ?: return null
        val code = params["code"]?.takeIf { it.matches(Regex("[0-9]{8}")) } ?: return null
        return PairLink(url, code)
    }

    fun wsUrl(base: String, path: String): String =
        (if (base.startsWith("https://")) "wss://" + base.removePrefix("https://") else "ws://" + base.removePrefix("http://")) + path

    /** Sunucu yanıtı / ağ hatası → kullanıcıya gerçek neden. */
    fun explainHttp(code: Int): String = when (code) {
        401, 403 -> "Mac bu telefonun erişimini kaldırmış. Yeniden eşleştir."
        404 -> "Mac'teki JARVIS bu sürümü tanımıyor. Mac'te JARVIS'i güncelle (yeniden başlat)."
        502, 530 -> "Mac'in bağlantı tüneli kapalı. Mac'te JARVIS TELEFON açık mı?"
        503 -> "Mac'te JARVIS hazır değil."
        else -> "Mac bağlantı hatası ($code)."
    }

    /** Mac'in adresi Tailscale'in sabit adresi mi (https://mac-adi.xxx.ts.net)? */
    fun isTailscale(url: String): Boolean = try { URI(url).host?.lowercase()?.endsWith(".ts.net") == true } catch (e: Exception) { false }

    fun explainNetwork(kind: String, url: String = ""): String = when {
        // Sabit adres yalnız telefonda Tailscale açıkken bulunur; adres değişmediği için yeniden eşleştirme gerekmez.
        isTailscale(url) && kind in setOf("UnknownHostException", "ConnectException", "SocketTimeoutException", "NoRouteToHostException") ->
            "Mac'e ulaşılamadı. Telefonda Tailscale açık mı? (Mac'te de Tailscale ve JARVIS TELEFON açık olmalı.)"
        else -> explainNetworkKind(kind)
    }

    private fun explainNetworkKind(kind: String): String = when (kind) {
        "UnknownHostException" -> "Mac'in adresi bulunamadı. JARVIS TELEFON yeniden başlatıldıysa adres değişmiştir; yeniden eşleştir."
        "SocketTimeoutException" -> "Mac yanıt vermedi (zaman aşımı)."
        "ConnectException" -> "Mac'e bağlanılamadı."
        "SSLHandshakeException", "SSLException", "SSLPeerUnverifiedException" -> "Mac'in güvenli bağlantı sertifikası doğrulanamadı."
        else -> "Mac'e ulaşılamadı."
    }

    /** WebSocket kapanış kodları: 4401 erişim kaldırıldı, 4009 aynı telefon başka yerden bağlandı. */
    fun isRevoked(code: Int): Boolean = code == 4401 || code == 4403
}
