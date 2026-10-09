package com.kemal.jarvis.live

import org.json.JSONArray
import org.json.JSONObject
import java.util.Base64

/** Gemini canlı ses (Live API, BidiGenerateContent) iletileri. Biçim Mac'teki google-genai 2.24 ile aynıdır. */
object LiveProtocol {
    const val MODEL = "models/gemini-2.5-flash-native-audio-latest"
    const val HOST = "wss://generativelanguage.googleapis.com"
    const val PATH = "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    const val INPUT_RATE = 16000
    const val OUTPUT_RATE = 24000

    data class Call(val id: String, val name: String, val args: JSONObject)

    sealed class Event {
        object SetupComplete : Event()
        class Audio(val pcm: ByteArray) : Event()
        data class InputText(val text: String) : Event()
        data class OutputText(val text: String) : Event()
        object Interrupted : Event()
        object TurnComplete : Event()
        object GenerationComplete : Event()
        data class ToolCalls(val calls: List<Call>) : Event()
        data class ToolCancel(val ids: List<String>) : Event()
        data class GoAway(val timeLeftMs: Long) : Event()
        data class Resumption(val handle: String?, val resumable: Boolean) : Event()
        data class ServerError(val message: String) : Event()
    }

    fun setup(
        systemText: String,
        voice: String,
        functions: List<JSONObject>,
        googleSearch: Boolean,
        resumeHandle: String?,
        model: String = MODEL,
    ): String {
        val tools = JSONArray()
        if (googleSearch) tools.put(JSONObject().put("googleSearch", JSONObject()))
        if (functions.isNotEmpty()) tools.put(JSONObject().put("functionDeclarations", JSONArray(functions)))
        val resumption = JSONObject()
        if (!resumeHandle.isNullOrEmpty()) resumption.put("handle", resumeHandle)
        val setup = JSONObject()
            .put("model", model)
            .put("generationConfig", JSONObject()
                .put("responseModalities", JSONArray().put("AUDIO"))
                .put("speechConfig", JSONObject().put("voiceConfig", JSONObject()
                    .put("prebuiltVoiceConfig", JSONObject().put("voiceName", voice)))))
            .put("systemInstruction", JSONObject().put("role", "user")
                .put("parts", JSONArray().put(JSONObject().put("text", systemText))))
            .put("tools", tools)
            .put("inputAudioTranscription", JSONObject())
            .put("outputAudioTranscription", JSONObject())
            .put("sessionResumption", resumption)
            // Uzun konuşmalarda 15 dk ses sınırına takılmamak için bağlam penceresi kaydırılır.
            .put("contextWindowCompression", JSONObject().put("slidingWindow", JSONObject()))
        return JSONObject().put("setup", setup).toString()
    }

    fun audio(pcm: ByteArray, length: Int = pcm.size): String {
        val data = Base64.getEncoder().encodeToString(if (length == pcm.size) pcm else pcm.copyOf(length))
        return JSONObject().put("realtimeInput", JSONObject().put("audio", JSONObject()
            .put("data", data).put("mimeType", "audio/pcm;rate=$INPUT_RATE"))).toString()
    }

    fun audioStreamEnd(): String =
        JSONObject().put("realtimeInput", JSONObject().put("audioStreamEnd", true)).toString()

    fun text(text: String): String =
        JSONObject().put("clientContent", JSONObject()
            .put("turns", JSONArray().put(JSONObject().put("role", "user")
                .put("parts", JSONArray().put(JSONObject().put("text", text)))))
            .put("turnComplete", true)).toString()

    /** result: String ya da JSONObject. */
    fun toolResponse(responses: List<Triple<String, String, Any>>): String {
        val list = JSONArray()
        for ((id, name, result) in responses) {
            list.put(JSONObject().put("id", id).put("name", name)
                .put("response", JSONObject().put("result", result)))
        }
        return JSONObject().put("toolResponse", JSONObject().put("functionResponses", list)).toString()
    }

    /** Sunucu iletisini olaylara ayırır; bilinmeyen alanlar yok sayılır. */
    fun parse(raw: String): List<Event> {
        val obj = try { JSONObject(raw) } catch (e: Exception) { return emptyList() }
        val out = ArrayList<Event>()
        if (obj.has("setupComplete")) out.add(Event.SetupComplete)
        obj.optJSONObject("serverContent")?.let { sc ->
            sc.optJSONObject("inputTranscription")?.optString("text")?.takeIf { it.isNotEmpty() }
                ?.let { out.add(Event.InputText(it)) }
            sc.optJSONObject("modelTurn")?.optJSONArray("parts")?.let { parts ->
                for (i in 0 until parts.length()) {
                    val inline = parts.optJSONObject(i)?.optJSONObject("inlineData") ?: continue
                    if (!inline.optString("mimeType").startsWith("audio/")) continue
                    val data = inline.optString("data")
                    if (data.isEmpty()) continue
                    try { out.add(Event.Audio(Base64.getDecoder().decode(data))) } catch (_: IllegalArgumentException) {}
                }
            }
            sc.optJSONObject("outputTranscription")?.optString("text")?.takeIf { it.isNotEmpty() }
                ?.let { out.add(Event.OutputText(it)) }
            if (sc.optBoolean("interrupted")) out.add(Event.Interrupted)
            if (sc.optBoolean("generationComplete")) out.add(Event.GenerationComplete)
            if (sc.optBoolean("turnComplete")) out.add(Event.TurnComplete)
        }
        obj.optJSONObject("toolCall")?.optJSONArray("functionCalls")?.let { calls ->
            val list = ArrayList<Call>()
            for (i in 0 until calls.length()) {
                val c = calls.optJSONObject(i) ?: continue
                list.add(Call(c.optString("id"), c.optString("name"), c.optJSONObject("args") ?: JSONObject()))
            }
            if (list.isNotEmpty()) out.add(Event.ToolCalls(list))
        }
        obj.optJSONObject("toolCallCancellation")?.optJSONArray("ids")?.let { ids ->
            out.add(Event.ToolCancel((0 until ids.length()).map { ids.optString(it) }))
        }
        obj.optJSONObject("goAway")?.let { out.add(Event.GoAway(parseDurationMs(it.optString("timeLeft")))) }
        obj.optJSONObject("sessionResumptionUpdate")?.let {
            out.add(Event.Resumption(it.optString("newHandle").ifEmpty { null }, it.optBoolean("resumable")))
        }
        obj.optJSONObject("error")?.let { out.add(Event.ServerError(it.optString("message", "Bilinmeyen hata"))) }
        return out
    }

    /** "12.5s" → 12500. */
    fun parseDurationMs(s: String): Long =
        s.removeSuffix("s").toDoubleOrNull()?.let { (it * 1000).toLong() } ?: 0L

    /** Google'ın kapanış gerekçesini kullanıcıya anlaşılır Türkçeye çevirir; gerçek gerekçe de eklenir. */
    fun explainClose(code: Int, reason: String): String {
        val r = reason.lowercase()
        val plain = when {
            "api key not valid" in r || "api_key_invalid" in r || "invalid api key" in r ->
                "Gemini anahtarı geçersiz. Ayarlar'dan doğru anahtarı gir."
            "quota" in r || "resource_exhausted" in r || "rate limit" in r || code == 1011 && "exceeded" in r ->
                "Gemini kullanım sınırına ulaşıldı (Google kotası)."
            "permission" in r || "permission_denied" in r || "not have access" in r ->
                "Bu Gemini anahtarının canlı ses modeline izni yok."
            "not found" in r && "model" in r -> "Canlı ses modeli bu anahtarla bulunamadı."
            "location" in r && "not supported" in r -> "Gemini canlı ses bu bölgede kullanılamıyor."
            code == 1000 || code == 1001 -> "Gemini bağlantıyı kapattı."
            else -> "Gemini bağlantısı kesildi."
        }
        val detail = reason.trim().take(160)
        return if (detail.isEmpty()) plain else "$plain (Google: $detail)"
    }

    fun isQuotaClose(reason: String): Boolean {
        val r = reason.lowercase()
        return "quota" in r || "resource_exhausted" in r || "rate limit" in r || "exceeded" in r
    }

    fun asTextLines(array: JSONArray?): List<String> =
        if (array == null) emptyList() else (0 until array.length()).map { array.optString(it) }
}
