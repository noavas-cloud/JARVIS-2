# JARVIS 2.0'ı Android telefona kurma

1. [Kurulum dosyasını](dist/JARVIS-Android.apk) telefona aktar ve aç; Android sorarsa kurulum izni verip **Yükle/Güncelle** de. Eski JARVIS'in üstüne güncellenir, silmen gerekmez. **Gemini anahtarın ve Mac eşleşmen otomatik taşınır.**
2. Karşılama ekranında: Gemini anahtarı (taşındıysa "Hazır ✓"), **Mikrofon**, **Bildirimler**, isteğe bağlı **Rehber** izinlerini ver → **BAŞLA**.
3. Küreye dokun ve konuş: "Feneri aç", "Yarın sabah 7'de alarm kur", "Pilim kaç?", "Annemi ara".

## Mac bağlantısı (bilgisayarla eşleştir)

Mac'teki JARVIS güncellendiği için **Mac'te JARVIS TELEFON'u bir kez kapatıp açman** gerekir (yeni uçlar yüklensin). Bu, tünel adresini değiştirir; bu yüzden telefonu bir kez yeniden eşleştir:

1. Mac'te JARVIS › **JARVIS TELEFON → BAŞLAT** › **ANDROID UYGULAMASINI EŞLEŞTİR** › yeni eşleştirme kodu.
2. Telefonda JARVIS › ⚙ Ayarlar › **Mac bağlantısı** › **QR KODU TARA** (ya da adresi ve 8 haneli kodu elle gir).
3. Gemini anahtarı telefonda yoksa aynı yerden **MAC'TEN AKTAR**.

Üstteki rozet: **Mac bağlı** (Mac işleri yapılabilir) · **Mac · JARVIS kapalı** (Mac'teki JARVIS ajanı çalışmıyor) · **Mac çevrimdışı** (kendiliğinden yeniden dener) · **Mac erişimi kaldırıldı** (yeniden eşleştir).

## Kolay erişim (Ayarlar › Kolay erişim)

- **Telefonun asistanı olsun** → açılan ekranda *Digital assistant app / Dijital asistan uygulaması* = JARVIS. Samsung'da ayrıca *Ayarlar › Gelişmiş özellikler › Yan tuş › Basılı tut* = *Dijital asistan* seçilirse yan tuşa basılı tutunca JARVIS açılır.
- **ANA EKRANA WIDGET** → "JARVIS · Konuş" (dokun = konuş, ⌨ = yaz).
- **HIZLI AYARLARA EKLE** → bildirim panelinde JARVIS kutucuğu.
- **Arka planda uygulama açabilsin** → *Diğer uygulamaların üzerinde göster* izni (isteğe bağlı).

## Sorun giderme

- **"Gemini kullanım sınırı"**: mesajda Google'ın gerçek gerekçesi yazar. Web araması kotasıysa JARVIS aramasız devam eder.
- **JARVIS kendi sesini duyup susuyor**: Ayarlar › "JARVIS konuşurken beni dinleme"yi aç (uygulama bunu kendisi de fark edip açar). Kulaklıkla sorun olmaz.
- **Uygulama açılmıyor, bildirim geliyor**: ekran kapalıyken Android izin vermez; bildirime dokun ya da "Arka planda uygulama açabilsin" iznini ver.
- **Mac çevrimdışı**: Mac uyanık mı, JARVIS TELEFON açık mı? JARVIS TELEFON yeniden başladıysa adres değişmiştir → yeniden eşleştir.
- **Geri dönmek istersen**: Android eski sürümü yenisinin üstüne kurmaz. Eski APK `_YEDEK_ANDROID_V2_2026-10-01_1740/android/dist/` içinde; kurmak için önce JARVIS'i telefondan kaldırman gerekir (anahtar ve eşleşme yeniden girilir).
