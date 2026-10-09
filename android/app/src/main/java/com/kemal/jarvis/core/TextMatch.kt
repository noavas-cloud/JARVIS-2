package com.kemal.jarvis.core

import java.text.Normalizer
import java.util.Locale

/** Türkçe ad/uygulama eşleştirme ve numara biçimi. Android sınıfı kullanmaz; JVM'de test edilir. */
object TextMatch {
    private val TR = Locale("tr", "TR")
    private val PALATAL_NAMES = setOf("kemal", "cemal", "celal", "hilal", "bilal", "meral", "iclal", "ikbal", "resul", "rasul", "kemalettin")

    data class Contact(val name: String, val number: String)

    /** Tek kesin kişi ya da (belirsizse) en çok 5 aday ad. */
    data class ContactMatch(val contact: Contact?, val candidates: List<String>)

    /** "Ayşe Gül" → "ayse gul"; Türkçe harfler ve işaretler sadeleştirilir. */
    fun fold(s: String): String =
        Normalizer.normalize(s.lowercase(TR).replace('ı', 'i'), Normalizer.Form.NFD)
            .replace(Regex("\\p{M}"), "")
            .replace(Regex("[^a-z0-9 ]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()

    /** "Ali'yi" → "Ali", "annemi" → "annem", "Ayşe’ye" → "Ayşe"; numaraya dokunmaz. */
    fun stripSuffix(name: String): String {
        var s = name.trim().replace(Regex("(?iu)^(?:telefondan|telefonla|telefonumdan)\\s+"), "").trim()
        val apostrophe = s.contains('\'') || s.contains('’')
        s = s.replace(Regex("(?u)['’](?:yi|yı|yu|yü|i|ı|u|ü|nı|ni|nu|nü|ye|ya|e|a|ne|na|le|la|yle|yla)$"), "")
        if (s.matches(Regex("[+0-9 ()-]{7,}"))) return s.trim()
        if (!apostrophe && s.length > 3) {
            val lower = s.lowercase(TR)
            if (lower.endsWith("mi") || lower.endsWith("mı") || lower.endsWith("mu") || lower.endsWith("mü")) {
                s = s.substring(0, s.length - 1)      // annemi → annem, babamı → babam
            }
        }
        return s.trim()
    }

    /** Türkiye biçimleri E.164'e: 0532… / 532… / +90 532… → +90532…; geçersizse null. */
    fun normalizeNumber(raw: String?): String? {
        val text = raw?.trim().orEmpty()
        if (text.isEmpty()) return null
        val plus = text.startsWith("+") || text.startsWith("00")
        var digits = text.replace(Regex("\\D"), "")
        if (text.startsWith("00")) digits = digits.substring(2)
        if (!plus) {
            if (digits.length == 11 && digits.startsWith("0")) digits = "90" + digits.substring(1)
            else if (digits.length == 10 && digits.startsWith("5")) digits = "90$digits"
        }
        if (digits.length < 8 || digits.length > 15) return null
        return "+$digits"
    }

    fun looksLikeNumber(s: String): Boolean = s.trim().matches(Regex("[+0-9 ()-]{7,}"))

    /** Sözcük düzeyinde puan: tam ad 300, tüm sözcükler 200, sözcük başları (≥3 harf) 120, yoksa 0. "Ali" → "Halil" OLMAZ. */
    fun score(needleRaw: String, candidate: String): Int {
        val needle = fold(needleRaw)
        val c = fold(candidate)
        if (c.isEmpty() || needle.isEmpty()) return 0
        if (c == needle) return 300
        val need = needle.split(" ")
        val words = c.split(" ")
        if (need.all { w -> words.any { it == w } }) return 200
        if (need.all { w -> w.length >= 3 && words.any { it.startsWith(w) } }) return 120
        return 0
    }

    /** Aynı numara tek kişi sayılır. */
    fun matchContact(name: String, contacts: List<Contact>): ContactMatch {
        var best = 0
        val winners = LinkedHashMap<String, Contact>()
        for (c in contacts) {
            val number = normalizeNumber(c.number) ?: continue
            val s = score(stripSuffix(name), c.name)
            if (s == 0 || s < best) continue
            if (s > best) { best = s; winners.clear() }
            winners.putIfAbsent(number, Contact(c.name, number))
        }
        if (winners.size == 1) return ContactMatch(winners.values.first(), emptyList())
        return ContactMatch(null, winners.values.map { it.name }.distinct().take(5))
    }

    /** Uygulama adı eşleştirme: tam ad > sözcük > baş harfler > içerir. En iyi puanlı etiket(ler). */
    fun <T> bestApps(query: String, apps: List<T>, label: (T) -> String): List<T> {
        val q = fold(query).removeSuffix(" uygulamasi").removeSuffix(" uygulamasini").trim()
        if (q.isEmpty()) return emptyList()
        var best = 0
        val out = ArrayList<T>()
        for (a in apps) {
            val l = fold(label(a))
            val s = when {
                l == q -> 300
                l.split(" ").contains(q) || l.replace(" ", "") == q.replace(" ", "") -> 220
                l.startsWith(q) -> 180
                q.length >= 3 && l.contains(q) -> 100
                else -> 0
            }
            if (s == 0 || s < best) continue
            if (s > best) { best = s; out.clear() }
            out.add(a)
        }
        return out
    }

    /** Türkçe tamlayan eki ünlü uyumuyla: Kemal'in, Ayşe'nin, Uğur'un, Gül'ün, Ali'nin. */
    fun genitive(name: String): String {
        if (name.isBlank()) return name
        val lower = name.lowercase(TR)
        val vowels = "aeıioöuü"
        val last = lower.lastOrNull { vowels.indexOf(it) >= 0 } ?: 'e'
        // Ünlü uyumunun istisnası olan adlar (ince "l"): Kemal'in, Cemal'in, Hilal'in…
        val palatal = fold(name.trim().split(" ").last()) in PALATAL_NAMES
        val v = if (palatal) "i" else when (last) { 'a', 'ı' -> "ı"; 'e', 'i' -> "i"; 'o', 'u' -> "u"; else -> "ü" }
        val endsVowel = vowels.indexOf(lower.last()) >= 0
        return name + "'" + (if (endsVowel) "n" else "") + v + "n"
    }

    /** Karşı tarafa okunacak metin: kimin adına arandığı açıkça söylenir. */
    fun spokenCallMessage(message: String, ownerGenitive: String?): String {
        val m = message.trim()
        val who = if (ownerGenitive.isNullOrBlank()) "telefon sahibinin" else ownerGenitive.trim()
        val f = fold(m)
        if (f.startsWith("merhaba") || f.contains("jarvis")) return m
        return "Merhaba, ben JARVIS, $who asistanıyım. İletmemi istediği mesaj şu: $m"
    }

    /** Numarayı ekranda/sesli onayda okunur yapar: +905321234567 → 0532 123 45 67. */
    fun prettyNumber(e164: String): String {
        val d = e164.removePrefix("+")
        if (d.startsWith("90") && d.length == 12) {
            val n = "0" + d.substring(2)
            return "${n.substring(0, 4)} ${n.substring(4, 7)} ${n.substring(7, 9)} ${n.substring(9)}"
        }
        return e164
    }
}
