# JARVIS Android 2.0

Kişisel kullanım için baştan yazılmış Android JARVIS (Kotlin + Jetpack Compose). 1.x'in yerine geçer; aynı imzayla onun üstüne güncellenir.

## Ne değişti (1.x → 2.0)

| | 1.x | 2.0 |
|---|---|---|
| Konuşma | Bas-konuş, telefonun robotik sesi, her mesaj ayrı istek | **Canlı sesli konuşma** (Gemini Live, `gemini-2.5-flash-native-audio`, Mac'teki ses: Charon). Konuşurken sözünü kesebilirsin; uzun konuşmalar kesintisiz sürer (bağlantı yenilenince kaldığı yerden devam). |
| Modlar | TELEFON / MAC sekmeleri | **Tek konuşma**: Mac bağlıysa Mac'in araçları da aynı konuşmaya eklenir ("Mac'te Safari'yi aç", "Apple takvimimde ne var"). |
| Telefon işleri | Pil, ses, uygulama/ayar açma | + fener, alarm, zamanlayıcı, telefon takvimi (oku/ekle), yol tarifi, müzik (oynat/duraklat/sonraki), rehberden arama, SMS/WhatsApp **taslağı**, kamera, ayar panelleri (Wi-Fi, Bluetooth…), kalıcı hafıza ("bunu hatırla"), web araması |
| Erişim | Uygulamayı açmak | + **ana ekran widget'ı**, **hızlı ayar kutucuğu**, **telefonun dijital asistanı** (yan tuş / güç ya da ana ekran tuşuna uzun basınca; 2.1.1'den beri açık uygulamanın üstünde ChatGPT gibi yalnız küre), simgeye uzun basınca "Konuş"/"Yaz" kısayolları, ekran kapalıyken de süren konuşma (bildirimden mikrofon/bitir) |
| 429 / kota | Google'ın gerekçesi gizleniyordu | Kota dolarsa web aramasız bir kez yeniden bağlanır; hata mesajında Google'ın gerçek gerekçesi görünür |

Korunanlar: **ara ve söyle** (telefondan ya da Mac'ten istek; onay kartı; Gemini doğal sesi; 1.x ses testi sonucu = kanal seçimi), Mac eşleştirme biçimi (aynı QR), Gemini anahtarı ve Mac eşleşmesi (1.x'ten otomatik taşınır), ana ekrandaki JARVIS simgesi.

## Kullanım

- **Küreye dokun** → dinler. Konuş; JARVIS sesli cevap verir. Konuşurken susturmak için küreye dokun, bitirmek için küreye uzun bas ya da ■. "Tamam teşekkürler / görüşürüz" deyince kendisi kapanır; sessizlikte 2 dk sonra kapanır (Ayarlar'dan değişir).
- **Yazarak**: alttaki alana yaz; mikrofon kapalı kalır.
- **Onay kartı**: arama, takvime ekleme ve Mac'te değişiklik yapan her iş ekranda onay ister (60 sn; süre dolarsa yapılmaz). Onay sesle verilemez.
- **Mesajlar asla kendiliğinden gönderilmez**: SMS/WhatsApp taslağı açılır, Gönder'e sen basarsın.
- **Arka planda**: JARVIS bir uygulamayı açmak isterken ekran kapalıysa ya da başka uygulamadaysan Android buna izin vermez; "Açmak için dokun" bildirimi gelir. Ayarlar › Kolay erişim › "Arka planda uygulama açabilsin" izni verilirse doğrudan açar.
- **Yankı**: Hoparlörden JARVIS kendi sesini duyup sözünü keserse uygulama bunu fark eder ve "JARVIS konuşurken dinleme"ye geçer (Ayarlar'dan elle de açılır).

## Sınırlar (Android izin vermez)

Wi-Fi/Bluetooth/mobil veri/uçak modunu doğrudan açıp kapatmak (yalnız ayar paneli açılır), başka uygulamaların ekranını/bildirimlerini okumak, mesaj/e-posta göndermek, fotoğraf çekmek, parlaklığı değiştirmek. Ara ve söyle **tek yönlüdür**: mesaj hoparlörden okunur, karşı tarafın cevabı duyulamaz, duyduğu doğrulanamaz. Mac işleri için Mac'in açık, JARVIS ve JARVIS TELEFON'un çalışıyor olması gerekir.

## Kod

`app/src/main/java/com/kemal/jarvis/`

- `AssistActivity.kt` + `ui/AssistOverlay.kt` — yan tuş / dijital asistan (2.1.1): saydam, kendi görev yığını; yalnız küre (onay kartı/hata yalnız gerekince); boşluk/geri/küreye uzun bas = kapat ve konuşmayı bitir; konuşma kendiliğinden biterse kapanır.
- `MainActivity.kt` — giriş noktası (simge, widget, kutucuk, `jarvis://pair` bağlantısı), izinler, ekranlar arası geçiş. Sınıf adı 1.x ile aynı (ana ekran simgesi bozulmasın).
- `JarvisApp.kt` — uygulama nesneleri, bildirim kanalları, `launch()` (ekran kapalıysa bildirime düşer).
- `conv/Conversation.kt` — konuşma motoru: Gemini Live + ses + araçlar + onaylar + Mac. `conv/Prompt.kt` sistem talimatı, `conv/ToolLabels.kt` sohbetteki işlem satırları.
- `live/LiveProtocol.kt`, `live/LiveClient.kt` — Gemini Live iletileri (Mac'teki google-genai 2.24 ile aynı biçim) ve WebSocket. Anahtar URL'de değil `x-goog-api-key` başlığında.
- `audio/Audio.kt` — mikrofon (16 kHz, yankı giderici), hoparlör (24 kHz), iletişim ses yolu.
- `tools/PhoneTools.kt` — telefon araçları ve Android'den bağımsız yorumlama (`ToolArgs`).
- `mac/MacLink.kt`, `mac/MacRules.kt` — eşleştirme, Mac araçları, ortak geçmiş, bağlantı kanalı, yeniden bağlanma (2→30 sn).
- `call/CallSpeaker.kt` — ara ve söyle: Gemini sesi (`GeminiTts`) ve arama sırasında mesajı çalan servis.
- `core/` — `TextMatch` (Türkçe ad/numara eşleştirme), `Stores` (Keystore şifreli depo, ayarlar, hafıza; 1.x taşıması).
- `service/JarvisService.kt` — konuşma sürerken ön plan servisi. `access/Access.kt` — widget ve hızlı ayar kutucuğu.
- `ui/` — Compose ekranları (küre, sohbet, onay kartı, ayarlar, karşılama).
- `src/debug/` — yalnız debug derlemesinde: sahte Gemini sunucusu adresi (`DevActivity`), emülatörden `http://10.0.2.2`.

Mac tarafı (`sistem/jarvis_web/server.py`): 2.0 için `GET /api/android/v2/tools`, `POST /api/android/v2/tool` (değişiklik yapan araçlar yalnız `approved=true` ile), `WS /ws/android/link` (Mac ajan durumu + Mac'ten arama isteği). Eski uçlar aynen duruyor.

## Derleme ve test

Araçlar `~/Library/Caches/jarvis-android-toolchain` (JDK 17, SDK 35, Gradle 8.11.1, Kotlin 2.1.20, Compose BOM 2025.04.01).

```bash
./gradlew :app:testDebugUnitTest      # 22 birim testi
python3 scripts/build_release.py      # imzalı sürüm → dist/JARVIS-Android.apk (lint dahil)
```

Ayrıntılı doğrulama: [DOGRULAMA.md](DOGRULAMA.md). Kurulum: [KURULUM.md](KURULUM.md).
