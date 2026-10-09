package com.kemal.jarvis

import com.kemal.jarvis.call.GeminiTts
import com.kemal.jarvis.conv.Prompt
import com.kemal.jarvis.conv.ToolLabels
import com.kemal.jarvis.core.Memory
import com.kemal.jarvis.core.TextMatch
import com.kemal.jarvis.live.LiveProtocol
import com.kemal.jarvis.live.LiveProtocol.Event
import com.kemal.jarvis.mac.MacRules
import com.kemal.jarvis.tools.ToolArgs
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.util.Base64
import java.util.Calendar

class TextMatchTest {
    private val book = listOf(
        TextMatch.Contact("Ali Yılmaz", "0532 111 22 33"),
        TextMatch.Contact("Halil Demir", "0532 444 55 66"),
        TextMatch.Contact("Annem", "+90 533 777 88 99"),
        TextMatch.Contact("Ayşe Kaya", "05357778899"),
        TextMatch.Contact("Ayşe Kaya iş", "0216 555 00 11"),
        TextMatch.Contact("Ali Yılmaz", "+905321112233"),
    )

    @Test fun suffixesAreStripped() {
        assertEquals("Ali", TextMatch.stripSuffix("Ali'yi"))
        assertEquals("Ayşe", TextMatch.stripSuffix("Ayşe’ye"))
        assertEquals("annem", TextMatch.stripSuffix("annemi"))
        assertEquals("Ali", TextMatch.stripSuffix("Ali'yle"))
        assertEquals("0532 111 22 33", TextMatch.stripSuffix("0532 111 22 33"))
    }

    @Test fun numbersBecomeE164() {
        assertEquals("+905321112233", TextMatch.normalizeNumber("0532 111 22 33"))
        assertEquals("+905321112233", TextMatch.normalizeNumber("532 111 2233"))
        assertEquals("+905321112233", TextMatch.normalizeNumber("0090 532 111 22 33"))
        assertEquals("+4915112345678", TextMatch.normalizeNumber("+49 151 12345678"))
        assertNull(TextMatch.normalizeNumber("12"))
        assertEquals("0532 111 22 33", TextMatch.prettyNumber("+905321112233"))
    }

    @Test fun aliNeverMatchesHalilAndSameNumberCountsOnce() {
        val m = TextMatch.matchContact("Ali'yi", book)
        assertEquals("Ali Yılmaz", m.contact?.name)
        assertEquals("+905321112233", m.contact?.number)
        assertEquals("Annem", TextMatch.matchContact("annemi", book).contact?.name)
    }

    @Test fun ambiguousNamesReturnCandidates() {
        val m = TextMatch.matchContact("Ayşe", book)
        assertNull(m.contact)
        assertEquals(listOf("Ayşe Kaya", "Ayşe Kaya iş"), m.candidates)
        assertTrue(TextMatch.matchContact("Mehmet", book).candidates.isEmpty())
    }

    @Test fun appsMatchByWordNotSubstringFirst() {
        val apps = listOf("WhatsApp", "WhatsApp Business", "YouTube", "YouTube Music", "Ayarlar")
        assertEquals(listOf("WhatsApp"), TextMatch.bestApps("whatsapp", apps) { it })
        assertEquals(listOf("YouTube Music"), TextMatch.bestApps("youtube music", apps) { it })
        assertEquals(listOf("Ayarlar"), TextMatch.bestApps(ToolArgs.appAliases("settings").first(), apps) { it })
        assertEquals(listOf("Ayarlar", "Settings"), ToolArgs.appAliases("Ayarlar uygulamasını"))
        assertTrue(TextMatch.bestApps("spotify", apps) { it }.isEmpty())
    }

    @Test fun genitiveAndSpokenMessage() {
        assertEquals("Cemal'in", TextMatch.genitive("Cemal"))
        assertEquals("Ayşe'nin", TextMatch.genitive("Ayşe"))
        assertEquals("Uğur'un", TextMatch.genitive("Uğur"))
        assertEquals("Gül'ün", TextMatch.genitive("Gül"))
        assertTrue(TextMatch.spokenCallMessage("toplantı iptal", "Cemal'in").startsWith("Merhaba, ben JARVIS, Cemal'in asistanıyım."))
        assertEquals("Merhaba Ali, akşam gelemiyorum", TextMatch.spokenCallMessage("Merhaba Ali, akşam gelemiyorum", "Cemal'in"))
    }
}

class LiveProtocolTest {
    @Test fun setupMatchesTheSdkShape() {
        val fn = JSONObject().put("name", "flashlight").put("description", "d")
        val o = JSONObject(LiveProtocol.setup("SI", "Charon", listOf(fn), googleSearch = true, resumeHandle = "h1")).getJSONObject("setup")
        assertEquals(LiveProtocol.MODEL, o.getString("model"))
        assertEquals("AUDIO", o.getJSONObject("generationConfig").getJSONArray("responseModalities").getString(0))
        assertEquals("Charon", o.getJSONObject("generationConfig").getJSONObject("speechConfig").getJSONObject("voiceConfig")
            .getJSONObject("prebuiltVoiceConfig").getString("voiceName"))
        assertEquals("SI", o.getJSONObject("systemInstruction").getJSONArray("parts").getJSONObject(0).getString("text"))
        val tools = o.getJSONArray("tools")
        assertTrue(tools.getJSONObject(0).has("googleSearch"))
        assertEquals("flashlight", tools.getJSONObject(1).getJSONArray("functionDeclarations").getJSONObject(0).getString("name"))
        assertEquals("h1", o.getJSONObject("sessionResumption").getString("handle"))
        assertTrue(o.has("inputAudioTranscription") && o.has("outputAudioTranscription") && o.has("contextWindowCompression"))
        val noSearch = JSONObject(LiveProtocol.setup("SI", "Puck", emptyList(), false, null)).getJSONObject("setup")
        assertEquals(0, noSearch.getJSONArray("tools").length())
        assertFalse(noSearch.getJSONObject("sessionResumption").has("handle"))
    }

    @Test fun clientMessages() {
        val audio = JSONObject(LiveProtocol.audio(byteArrayOf(0, 1, 2, 3), 2)).getJSONObject("realtimeInput").getJSONObject("audio")
        assertEquals("audio/pcm;rate=16000", audio.getString("mimeType"))
        assertEquals("AAE=", audio.getString("data"))
        assertTrue(JSONObject(LiveProtocol.audioStreamEnd()).getJSONObject("realtimeInput").getBoolean("audioStreamEnd"))
        val text = JSONObject(LiveProtocol.text("selam")).getJSONObject("clientContent")
        assertTrue(text.getBoolean("turnComplete"))
        assertEquals("selam", text.getJSONArray("turns").getJSONObject(0).getJSONArray("parts").getJSONObject(0).getString("text"))
        val tr = JSONObject(LiveProtocol.toolResponse(listOf(Triple("id1", "flashlight", JSONObject().put("status", "ok")))))
        val fr = tr.getJSONObject("toolResponse").getJSONArray("functionResponses").getJSONObject(0)
        assertEquals("id1", fr.getString("id"))
        assertEquals("ok", fr.getJSONObject("response").getJSONObject("result").getString("status"))
    }

    @Test fun parsesServerContentInOrder() {
        val pcm = Base64.getEncoder().encodeToString(byteArrayOf(1, 2, 3, 4))
        val raw = JSONObject().put("serverContent", JSONObject()
            .put("inputTranscription", JSONObject().put("text", "feneri aç"))
            .put("modelTurn", JSONObject().put("parts", JSONArray().put(JSONObject().put("inlineData",
                JSONObject().put("mimeType", "audio/pcm;rate=24000").put("data", pcm)))))
            .put("outputTranscription", JSONObject().put("text", "Açtım"))
            .put("turnComplete", true)).toString()
        val ev = LiveProtocol.parse(raw)
        assertEquals(Event.InputText("feneri aç"), ev[0])
        assertEquals(4, (ev[1] as Event.Audio).pcm.size)
        assertEquals(Event.OutputText("Açtım"), ev[2])
        assertEquals(Event.TurnComplete, ev[3])
    }

    @Test fun parsesToolsGoAwayResumptionAndJunk() {
        val calls = LiveProtocol.parse("""{"toolCall":{"functionCalls":[{"id":"a","name":"flashlight","args":{"on":true}},{"id":"b","name":"device_status"}]}}""")
        val tc = calls.single() as Event.ToolCalls
        assertEquals(listOf("flashlight", "device_status"), tc.calls.map { it.name })
        assertTrue(tc.calls[0].args.getBoolean("on"))
        assertEquals(0, tc.calls[1].args.length())
        assertEquals(Event.ToolCancel(listOf("a")), LiveProtocol.parse("""{"toolCallCancellation":{"ids":["a"]}}""").single())
        assertEquals(Event.GoAway(12500), LiveProtocol.parse("""{"goAway":{"timeLeft":"12.5s"}}""").single())
        assertEquals(Event.Resumption("h2", true), LiveProtocol.parse("""{"sessionResumptionUpdate":{"newHandle":"h2","resumable":true}}""").single())
        assertEquals(Event.SetupComplete, LiveProtocol.parse("""{"setupComplete":{}}""").single())
        assertEquals(Event.Interrupted, LiveProtocol.parse("""{"serverContent":{"interrupted":true}}""").single())
        assertTrue(LiveProtocol.parse("not json").isEmpty())
        assertTrue(LiveProtocol.parse("""{"usageMetadata":{"totalTokenCount":5}}""").isEmpty())
    }

    @Test fun closeReasonsAreHonestAndKeepGooglesWording() {
        val q = LiveProtocol.explainClose(1011, "You exceeded your current quota, please check your plan")
        assertTrue(q.startsWith("Gemini kullanım sınırına ulaşıldı"))
        assertTrue(q.contains("Google: You exceeded"))
        assertTrue(LiveProtocol.isQuotaClose("RESOURCE_EXHAUSTED"))
        assertTrue(LiveProtocol.explainClose(1007, "API key not valid. Please pass a valid API key.").startsWith("Gemini anahtarı geçersiz"))
        assertFalse(LiveProtocol.isQuotaClose("API key not valid"))
    }
}

class MacRulesTest {
    @Test fun backoff() {
        assertEquals(listOf(2000L, 4000L, 8000L, 16000L, 30000L, 30000L), (0..5).map { MacRules.backoffMs(it) })
    }

    @Test fun urlsMustBeHttpsWithoutTricks() {
        assertEquals("https://abc-def.trycloudflare.com", MacRules.validateUrl("https://abc-def.trycloudflare.com/"))
        assertNull(MacRules.validateUrl("http://abc.trycloudflare.com"))
        assertNull(MacRules.validateUrl("https://user:pw@abc.trycloudflare.com"))
        assertNull(MacRules.validateUrl("https://abc.trycloudflare.com/#x"))
        assertNull(MacRules.validateUrl("https://abc.trycloudflare.com/?a=b"))
        assertNull(MacRules.validateUrl("javascript:alert(1)"))
        assertNull(MacRules.validateUrl("http://10.0.2.2:8800"))
        assertEquals("http://10.0.2.2:8800", MacRules.validateUrl("http://10.0.2.2:8800", devHttp = true))
        assertNull(MacRules.validateUrl("http://evil.example", devHttp = true))
    }

    @Test fun pairLinkFromMacQr() {
        val p = MacRules.parsePairLink("jarvis://pair?url=https%3A%2F%2Fabc.trycloudflare.com&code=12345678")
        assertEquals(MacRules.PairLink("https://abc.trycloudflare.com", "12345678"), p)
        assertNull(MacRules.parsePairLink("jarvis://pair?url=https%3A%2F%2Fabc.trycloudflare.com&code=1234"))
        assertNull(MacRules.parsePairLink("https://example.com"))
        assertEquals("wss://abc.trycloudflare.com/ws/android/link", MacRules.wsUrl("https://abc.trycloudflare.com", "/ws/android/link"))
        assertEquals("ws://10.0.2.2:8800/x", MacRules.wsUrl("http://10.0.2.2:8800", "/x"))
    }

    @Test fun revocationCodes() {
        assertTrue(MacRules.isRevoked(4401))
        assertFalse(MacRules.isRevoked(4009))
        assertTrue(MacRules.explainHttp(530).contains("tünel"))
    }
}

class ToolArgsTest {
    @Test fun alarmDays() {
        assertEquals(listOf(Calendar.MONDAY, Calendar.FRIDAY), ToolArgs.days(JSONArray(listOf("mon", "Cuma"))))
        assertEquals(listOf(Calendar.SATURDAY), ToolArgs.days(JSONArray(listOf("Cumartesi"))))
        assertTrue(ToolArgs.days(null).isEmpty())
    }

    @Test fun daysAndDurations() {
        val today = LocalDate.of(2026, 10, 1)
        assertEquals(today, ToolArgs.day("today", today))
        assertEquals(today.plusDays(1), ToolArgs.day("yarın", today))
        assertEquals(LocalDate.of(2026, 12, 31), ToolArgs.day("2026-12-31", today))
        assertNull(ToolArgs.day("bir ara", today))
        assertEquals("1 saat 30 dakika", ToolArgs.duration(5400))
        assertEquals("10 dakika", ToolArgs.duration(600))
    }

    @Test fun eventSpans() {
        val (b, e) = ToolArgs.eventSpan("2026-10-02T14:00", "", false)!!
        assertEquals(LocalDateTime.of(2026, 10, 2, 14, 0), b)
        assertEquals(LocalDateTime.of(2026, 10, 2, 15, 0), e)
        val (b2, e2) = ToolArgs.eventSpan("2026-10-02", "", true)!!
        assertEquals(LocalDateTime.of(2026, 10, 2, 0, 0), b2)
        assertEquals(LocalDateTime.of(2026, 10, 3, 0, 0), e2)
        assertEquals(LocalDateTime.of(2026, 10, 2, 16, 30), ToolArgs.eventSpan("2026-10-02T14:00:00Z", "2026-10-02T16:30", false)!!.second)
        assertNull(ToolArgs.eventSpan("yarın öğlen", "", false))
    }
}

class PromptAndLabelsTest {
    @Test fun promptStatesLimitsTimeAndMacState() {
        val turn = Memory.Turn("id", "feneri aç", "Açtım.", 1_790_000_000_000, false)
        val p = Prompt.build(LocalDateTime.of(2026, 10, 1, 18, 30), "Cemal", "samsung SM-X000", listOf("Kahveyi şekersiz içer"),
            listOf(turn), "[MAC'TEKİ KAYITLI HAFIZA]\nx\n", Prompt.Mac.ONLINE, true)
        assertTrue(p.contains("Cemal'in kişisel"))
        assertTrue(p.contains("1 Ekim 2026"))
        assertTrue(p.contains("mac_"))
        assertTrue(p.contains("Kahveyi şekersiz içer"))
        assertTrue(p.contains("feneri aç"))
        assertTrue(p.contains("tek yönlü"))
        assertTrue(p.contains("Google Arama"))
        val off = Prompt.build(LocalDateTime.of(2026, 10, 1, 18, 30), "Ayşe", "x", emptyList(), emptyList(), "", Prompt.Mac.OFFLINE, false)
        assertTrue(off.contains("Ayşe'nin kişisel"))
        assertTrue(off.contains("bağlı değil"))
        assertTrue(off.contains("Web araması kapalı"))
    }

    @Test fun labels() {
        assertEquals("Fener açılıyor", ToolLabels.label("flashlight", JSONObject().put("on", true)))
        assertEquals("Alarm kuruluyor · 07:05", ToolLabels.label("set_alarm", JSONObject().put("hour", 7).put("minute", 5)))
        assertEquals("Mac · Uygulama aç", ToolLabels.label("mac_open_app", JSONObject()))
        assertEquals("✕ Onaylanmadı — yapılmadı", ToolLabels.outcome(JSONObject().put("status", "declined")))
        assertNull(ToolLabels.outcome(JSONObject().put("status", "ok")))
        assertNull(ToolLabels.outcome("Mac sonucu"))
        assertEquals(listOf("app_name: Safari"), ToolLabels.argLines(JSONObject().put("app_name", "Safari").put("x", "")))
    }
}

class GeminiTtsTest {
    @Test fun requestAndAudioExtraction() {
        val r = GeminiTts.request("merhaba", "Charon")
        assertEquals(GeminiTts.MODEL, r.getString("model"))
        assertEquals("Charon", r.getJSONObject("generation_config").getJSONArray("speech_config").getJSONObject(0).getString("voice"))
        val data = Base64.getEncoder().encodeToString(ByteArray(10) { it.toByte() })
        val resp = JSONObject().put("steps", JSONArray().put(JSONObject().put("content", JSONArray()
            .put(JSONObject().put("type", "text").put("text", "x"))
            .put(JSONObject().put("type", "audio").put("data", data)))))
        assertEquals(10, GeminiTts.audio(resp)!!.size)
        assertNull(GeminiTts.audio(JSONObject()))
    }

    @Test fun wavDataChunkIsExtracted() {
        val pcm = ByteArray(6) { (it + 1).toByte() }
        val header = "RIFF".toByteArray() + byteArrayOf(0, 0, 0, 0) + "WAVE".toByteArray() +
            "fmt ".toByteArray() + byteArrayOf(2, 0, 0, 0, 9, 9) + "data".toByteArray() + byteArrayOf(6, 0, 0, 0)
        assertEquals(pcm.toList(), GeminiTts.pcm(header + pcm)!!.toList())
        assertNotNull(GeminiTts.pcm(pcm))
    }
}
