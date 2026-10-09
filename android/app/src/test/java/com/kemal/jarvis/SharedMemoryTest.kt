package com.kemal.jarvis

import com.kemal.jarvis.core.SharedMemory
import com.kemal.jarvis.mac.MacRules
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Telefon ↔ Mac ortak hafıza yanıtları ve Tailscale sabit adres mesajları. */
class SharedMemoryTest {
    @Test fun macItemsAreParsedAndBounded() {
        val arr = JSONArray()
            .put(JSONObject().put("category", "telefon_notlari").put("key", "park").put("text", "B2 katı"))
            .put(JSONObject().put("category", "x").put("key", "boş").put("text", "  "))
            .put("bozuk")
        assertEquals(listOf(SharedMemory.Item("telefon_notlari", "park", "B2 katı")), SharedMemory.parseItems(arr))
        val many = JSONArray().apply { repeat(250) { put(JSONObject().put("category", "c").put("key", "$it").put("text", "t$it")) } }
        val parsed = SharedMemory.parseItems(many)
        assertEquals(200, parsed.size)
        assertEquals("t249", parsed.last().text)  // en yeniler kalır
        assertEquals(emptyList<SharedMemory.Item>(), SharedMemory.parseItems(null))
    }

    @Test fun storedIdsAndForgetAnswers() {
        assertEquals(listOf("a", "b"), SharedMemory.strings(JSONArray().put("a").put(5).put("").put("b")))
        val forgotten = JSONArray().put(JSONObject().put("text", "park").put("message", "telefon_notlari/park hafizadan kaldirildi."))
        assertEquals(listOf("park" to "telefon_notlari/park hafizadan kaldirildi."), SharedMemory.forgotten(forgotten))
    }

    @Test fun macAnswersAreReadable() {
        assertEquals("Mac'teki kayıt da silindi.", SharedMemory.explainForget("telefon_notlari/park hafizadan kaldirildi."))
        assertTrue(SharedMemory.explainForget("Birden fazla hafiza kaydi eslesti; hicbiri silinmedi.").contains("hiçbiri silinmedi"))
        assertEquals("Mac'te eşleşen kayıt yoktu.", SharedMemory.explainForget("Eslestigim bir hafiza kaydi bulamadim."))
    }

    @Test fun tailscaleAddressGivesTheRightAdvice() {
        assertTrue(MacRules.isTailscale("https://my-mac.tail1a2b.ts.net"))
        assertFalse(MacRules.isTailscale("https://abc-def.trycloudflare.com"))
        assertTrue(MacRules.explainNetwork("UnknownHostException", "https://my-mac.tail1a2b.ts.net").contains("Tailscale açık mı"))
        // Geçici adreste eski öneri (yeniden eşleştir) kalır.
        assertTrue(MacRules.explainNetwork("UnknownHostException", "https://abc.trycloudflare.com").contains("yeniden eşleştir"))
        assertEquals(MacRules.explainNetwork("SSLHandshakeException"), MacRules.explainNetwork("SSLHandshakeException", "https://m.t.ts.net"))
        // Sabit adres eşleştirme bağlantısında kabul edilir.
        assertEquals("https://my-mac.tail1a2b.ts.net", MacRules.validateUrl("https://my-mac.tail1a2b.ts.net"))
    }
}
