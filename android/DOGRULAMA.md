# JARVIS Android 2.0 — doğrulama (1 Ekim 2026)

## Otomatik testler

- **Android birim testleri** (`./gradlew :app:testDebugUnitTest`): 22/22 geçti — Türkçe ad/numara eşleştirme ("Ali" ≠ "Halil", "Cemal'in", aynı numara tek kişi), Gemini Live iletileri (kurulum, ses, metin, araç cevabı; sunucu iletilerinin ayrıştırılması; kota/anahtar hata metinleri), Mac adres/QR doğrulaması ve yeniden bağlanma aralıkları, alarm günleri ("Cumartesi" ≠ Cuma), takvim tarihleri, sistem talimatı, Gemini ses (WAV) ayrıştırma.
- **İmzalı sürüm** (`scripts/build_release.py`): derleme + lint (hata yok) + `apksigner verify`. İmza sertifikası 1.3.1 ile aynı (`dd1543…a4af`).
- **Mac testleri** (`sistem`, 228 test): yalnız önceden bilinen 2 eski hata (`test_bug_fixes2`, `/var`↔`/private/var`). Yeni 10 test: v2 araç listesi (onay işaretleri, gizli araçlar), onaysız değişiklik reddi, masaüstü araçlarının kapalı olması, bozuk/büyük istekler, bağlantı kanalı (tanınmayan cihaz 4401, hello/ping, bağlı telefon sayımı, eşleşme kaldırılınca 4401), Mac'ten arama isteğinin telefona gidip sonucun dönmesi, kopma ve aynı anda ikinci istek.

## Emülatörde uçtan uca (Android 15, `jarvis_e2e`)

Gerçek `server.py` (geçici cihaz deposu, deneme yönetici anahtarı, sahte Mac ajanı) + **sahte Gemini Live sunucusu** (gerçek Google'a ve gerçek anahtara dokunulmadı) + debug APK:

| Denenen | Sonuç |
|---|---|
| Karşılama, izinler, ayarlar | ✓ |
| Elle eşleştirme (adres + kod) → "Mac bağlı", Mac'ten anahtar aktarma | ✓ |
| Canlı ses: mikrofon sesi gidiyor, konuşma yazıya dökülüyor, JARVIS sesi çalınıyor, DİNLİYORUM'a dönüyor | ✓ (emülatör mikrofonu sessiz; konuşma sahte sunucuda taklit edildi) |
| Yazarak konuşma (mikrofon kapalı) | ✓ |
| 23 telefon aracı + 30 Mac aracı tek oturumda | ✓ |
| Telefon durumu, fener, alarm (saat uygulamasında 07:30 kuruldu), zamanlayıcı (çaldı), hafıza | ✓ |
| Mac aracı (okuma) sahte ajana kadar gidip döndü | ✓ |
| Mac'te değişiklik: ONAYLA → çalıştı; VAZGEÇ → Mac'e hiç gitmedi | ✓ |
| Takvime ekleme: onay → izin → doğru saat (5 Eki 10:00 +03) + 15 dk hatırlatıcı | ✓ (ilk denemede izin hatası bulundu, düzeltildi) |
| Rehberden arama: onay → izin → arama açıldı; arama başlayınca konuşma kapanıyor | ✓ (izin sonrası açılmama hatası bulundu, düzeltildi) |
| SMS taslağı (gönderilmedi), Wi-Fi paneli | ✓ |
| Arka planda uygulama açma → "Açmak için dokun" bildirimi → dokununca açıldı; overlay izniyle doğrudan açıldı | ✓ |
| Bağlantı kopunca / goAway → devam anahtarıyla kaldığı yerden sürdü | ✓ |
| Kendi sesiyle iki kez kesilme → "konuşurken dinleme"ye geçti ve söyledi | ✓ (uzun ses parçasında "konuşmuyor" sanma hatası bulundu, düzeltildi) |
| Kota hatasıyla kapanma → web aramasız yeniden bağlandı ve söyledi | ✓ |
| Mac'ten ara ve söyle: telefonda kart → ARA → Mac'e "dialing" döndü | ✓ |
| Arama sürerken mesaj hoparlörden 2 kez çalındı, sonuç bildirimi dürüst | ✓ (gelen arama açık tutularak; Gemini sesi sahte anahtarla alınamadı → telefon sesi, bunu da söyledi) |
| "Görüşürüz" ile kapanma, servis duruyor | ✓ |
| Widget (konuş/yaz), hızlı ayar kutucuğu, asistan tuşu (JARVIS asistan rolünü alabiliyor), "Konuş" kısayolu | ✓ |
| 1.3.1'in üstüne imzalı 2.0 kurulumu: anahtar taşındı, mesaj yalnız taşınanı söylüyor | ✓ |

## Gerçek Gemini ile kısa deneme (kullanıcı izniyle, 1 Eki 19:27)

Anahtar hiçbir alana yazılmadı/gösterilmedi: deneme köprüsünün "Mac'ten aktar" ucu üzerinden emülatördeki uygulamaya şifreli geçti;
deneme bitince uygulama emülatörden kaldırıldı (anahtar da silindi), kayıtlarda anahtar yok. Eşleşme QR bağlantısıyla (`jarvis://pair`).

| Yazılan | JARVIS (gerçek Gemini Live) |
|---|---|
| "Merhaba JARVIS" | "Merhaba, nasıl yardımcı olabilirim?" |
| "Telefonumun pili yüzde kaç" | ⚙ Telefon durumu okunuyor → "Telefonunun pili %100 ve şarj olmuyor." |
| "Feneri aç" | ⚙ Fener açılıyor → "Feneri açtım." |
| "Bugün İstanbul'da hava nasıl" | Google Arama ile: "parçalı bulutlu ve hafif yağmurlu… 20-21 derece" (kota hatası yok) |

## Denenemeyenler

- **Gerçek Gemini'yle sesli konuşma**: emülatörün mikrofonu sessiz olduğu için yalnız yazılı istek denendi (cevaplar Gemini'nin sesiyle üretildi ama dinlenmedi).
- **Gerçek telefon**: yankı gidericinin hoparlörde ne kadar iyi çalıştığı, Samsung yan tuş/asistan ayarı, gerçek aramada mesajın karşıya ulaşması.
- **Gerçek Mac uygulamasıyla**: Mac'te JARVIS TELEFON yeni kodla yeniden başlatılıp gerçek tünel üzerinden eşleşme.
