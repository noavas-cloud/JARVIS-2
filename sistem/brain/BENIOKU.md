# İkinci Beyin + El Kontrolü

JARVIS'e eklenen iki bağlantılı özellik: seçtiğin not/proje klasörlerinden oluşan
**etkileşimli bilgi grafiği** ve bu grafiği Mac kamerasıyla yönetmeni sağlayan
**el hareketi kontrolü**.

## Nasıl kullanılır

**Basit görünüm (01.10.2026):** Pencere açılınca solda yalnızca **ARA** kutusu ve 4 maddelik **NASIL KULLANILIR**
kartı, üstte **KAYNAK EKLE · SIFIRLA · EL KONTROLÜ · KAPAT** düğmeleri görünür. Filtreler (görünüm, türler,
ilişkiler, güven, kaynak), tüm kısayollar, kamera bilgisi ve **YENİDEN TARA** soldaki **⚙ GELİŞMİŞ** düğmesinin
altındadır. Klasör seçilmemişse grafiğin ortasında büyük **＋ KLASÖR EKLE** düğmesi çıkar. Tarama, tüm `_YEDEK_…`
yedek klasörlerini atlar. Pencere Dock'ta ayrı simge açmaz (`brain/dockless.py`); öne getirmek için JARVIS'te BEYİN / ⌘B.

| Ne | Nasıl |
|---|---|
| Kaynak klasör seç | SYSTEM SETTINGS › **İKİNCİ BEYİN · KAYNAKLAR** › + KLASÖR EKLE (veya grafikte `+ KAYNAK EKLE`) |
| Grafiği aç | Alt sıradaki **◈ BEYİN** düğmesi, `⌘B`, ya da sesle: “bilgi grafiğimi aç” |
| Sesle sor | “Güneş Paneli projesiyle ilgili notları göster”, “bütçe konusundaki notlarım neler?” |
| El kontrolünü aç/kapat | SYSTEM SETTINGS › **EL KONTROLÜ · KAMERA** anahtarı, ya da grafikte ✋ düğmesi / `H` |

### Grafikte
- **Galaksi görünümü (04.10.2026, HOLO'nun "THE GALAXY" fikri):** her not/dosya bir yıldız, her klasör kendi renginde
  bir **takımyıldız** (düğüm, içinde durduğu en yakın klasörün rengini alır; konular bağlı oldukları klasörlerin çoğunluğuna
  katılır). Klasörlerin çevresinde hafif **bulutsu**, en bağlantılı yıldızlarda **ışıma**, arkada soluk yıldız alanı.
  Tür artık renkle değil boyut/şekille: klasör büyük yıldız + bulutsu, not orta, dosya küçük, konu halka. Etiketler
  üst üste binmez (önce seçili/odaktaki, sonra klasörler, sonra en bağlantılılar). Seçince diğer yıldızlar ve
  bulutsular söner. Çizgi renkleri aynı: düz teal = açık, kesik amber = tahmini.
- **Düz teal çizgi = açık bağlantı**: dosyada gerçekten yazan bağ — `[[wiki]]` veya Markdown
  bağlantısı, `#etiket`, metinde dosya/proje adının geçmesi, dosyanın proje klasöründe durması.
- **Kesik amber çizgi = tahmini ilişki**: yerel modelin önerisi (TF‑IDF içerik benzerliği,
  anahtar terim). Güven yüzdesiyle gösterilir; kesin değildir.
- Bir düğüme tıklayınca sağ panelde her bağlantının **nedeni** ve **kaynağı** (dosya:satır — alıntı) görünür.
- Arama (ad, etiket, metin), tür/ilişki/güven/kaynak filtreleri, yakınlaştırma, `KOMŞULAR` odağı,
  `DOSYAYI AÇ` / `FINDER'DA` (yalnızca fare/klavye ile).
- Fare: sürükle = düğüm taşı / boş alanı kaydır · tekerlek = yakınlaştır · çift tık = dosyayı aç.
- Klavye: `+ −` yakınlaştır, oklar kaydır, `0` sıfırla, `⌘F` ara, `⌘O` aç, `H` el kontrolü, `Esc` seçimi kaldır.

### JARVIS ile sesli kullanım
| Söylediğin | Ne olur |
|---|---|
| “Güneş Paneli projesiyle ilgili notları göster” | Grafik o projeye odaklanır; JARVIS açık/tahmini bağlantıları anlatır |
| “Güneş Paneli projesinde bütçe ne kadardı?” | JARVIS notların içinden ilgili parçaları okur, cevabı **yalnızca** onlardan verir ve dosya adını söyler; notlarda yoksa “yok” der |
| “Bütçe notunu seç” | Düğüm seçilir, ortalanır, bağlantıları anlatılır |
| “Sadece kesin bağlantıları göster” / “Tahminleri de göster” | İlişki filtresi |
| “Sadece notları ve projeleri göster”, “Güveni %40'ın altındaki tahminleri gizle” | Tür ve güven filtresi |
| “Yakınlaştır” / “Biraz uzaklaştır” / “Hepsini ekrana sığdır” | Görünüm (sığdırmak taşınan düğümleri yerine döndürmez) |
| “Komşularını göster” / “Seçimi temizle” | Komşu odağı / temizle |
| Grafikte bir klasöre/nota **tıkla**, sonra “Bunun içinde ne var?”, “Bu ne?”, “Bunu özetle” | JARVIS tıkladığın öğeyi bilir: klasörse içindeki dosyaları, not/dosyaysa metninin başını ve bağlantılarını söyler. Hiçbir şey seçili değilse neyi kastettiğini sorar, tahmin etmez. |
| “School klasörüne odaklan” / “Odaklan” (seçiliye) / “Odaktan çık” | Sesle odak: yalnızca o klasör/not ve bağlı olanlar yakınlaştırılarak gösterilir; çıkınca tüm grafik geri gelir (sıfırlamaz). |
| “Hesap notunu aç” / “Finder'da göster” | Seçili klasörlerdeki not/dosya açılır |
| “El kontrolünü aç/kapat”, “Grafiği kapat” | El kontrolü / pencere |

Görünümü **sıfırlamak** sesle yapılmaz; klavyede `0`, ⟲ SIFIRLA ya da elle ✌ (1 sn sabit tut).

**JARVIS neyi seçtiğini nasıl bilir?** Grafik penceresi, seçim değiştiğinde (fare, el ya da ses) JARVIS'e yalnızca
seçilen öğenin adını, türünü, yolunu ve odakta olup olmadığını bildirir. Dosya içeriği bu bildirimle gönderilmez;
sorduğunda JARVIS yerel indeksten okur. Pencere kapanınca seçim unutulur.

### El hareketleri (yalnızca grafik penceresini yönetir)
| Hareket | Sonuç |
|---|---|
| Başparmak + işaret parmağını birleştir (sıkıştır) düğümün üstünde | Düğümü tut, taşı, bırak |
| Boş alanda sıkıştır | Grafiği kaydır |
| **Yumruk yap ve elini gezdir** | Grafiği kaydır — hiçbir dosyaya dokunmaz, tutmaz, seçmez (2B ve 3B'de; 3B'de döndürmez, kaydırır). Çok yakınlaşınca her yer dosyayla dolu olduğunda bunu kullan. Elini açınca biter (04.10) |
| İki elle sıkıştır, açıp kapat | Yakınlaştır / uzaklaştır |
| Tek elle **bir kez yumruk** (bir dosyaya tek dokunup seçtikten sonra) | Seçimden çık: o dosyanın ve bağlı olanların vurgusu kalkar, tüm grafik görünür (04.10). Odaktayken (iki kez dokunarak) bir şey yapmaz; odaktan çıkmak için çift yumruk |
| **✌** (V işareti) ~1 sn sabit tut | Görünümü sıfırla (taşınanlar yerine döner, her şey ekrana sığar) |
| Tek elle **iki kez hızlı yumruk** (yumruk → aç → yumruk, ~1 sn içinde) | Yakınlaştırmadan / odaktan çık: tüm grafik yeniden ekrana sığar. **Sıfırlamaz** — taşınan düğümler, 3B açı, filtreler ve seçim korunur. Zaten uzaktaysan bir şey olmaz. |

**Nişan alma:** İmleç açık elle hedeflenir; sıkıştırınca ve bırakınca yerinden kaymaz (avuç merkezinden
sürülür). Hedeflenen düğüm önceden kesikli halkayla vurgulanır; sıkıştırınca o tutulur. İmleç dururken
titremez, hızlı harekette geride kalmaz (One Euro filtresi + küçük ölü bölge; takip 30 FPS).

**Ekrandaki geri bildirim (sol alt panel):**
- Kamera görünümü: elinin iskeleti, başparmak/işaret uçları ve **kesikli “etkin alan”** çerçevesi
  (bu çerçevenin içi tüm ekrana karşılık gelir).
- Her el için dikey **sıkıştırma göstergesi**: parmaklar yaklaştıkça dolar, tutunca altın olur.
  Grafikteki imleç halkası da parmaklar yaklaştıkça küçülür.
- **Algılama kalitesi** (İYİ / ORTA / ZAYIF) ve nedeni: elin uzak/çok yakın, kadrajın kenarında,
  etkin alanın dışında, ortam karanlık, algılama güveni düşük, takip yavaş.
- Duruma göre **ipucu**: “elini sabit tut”, “‘X’ · tutmak için birleştir”, “bırakmak için parmaklarını aç”…

**Çift yumruk yanlışlıkla tetiklenmesin diye:** her yumruk ve aradaki açılış birkaç kare sürmeli; tek yumruk, yavaş (>1,2 sn) ya da uzun tutulan yumruk bir şey yapmaz; tetiklendikten sonra 1,5 sn bekleme vardır; yakınlaştırma veya tutuş sürerken çalışmaz. Yumruk dizisi sürerken ve açıldıktan sonra kısa süre o elde sıkıştırma başlamaz (açılırken yanlışlıkla tutuş/odak olmasın) — yumruktan hemen sonra tutmak istersen ~1 sn bekle.

**✌ ile sıfırlama (04.10.2026, kullanıcı isteği; önceden kapalıydı):** V işaretini ~1 sn sabit tut → görünüm
sıfırlanır (ipucunda "✌ Sıfırlanıyor… %" ilerlemesi). El kıpırdarsa ya da işaret bir an bozulursa sayaç baştan
başlar; tutmaya devam etmek ikinci kez sıfırlamaz, ardından 2,5 sn bekleme. Klavyede `0` ve ⟲ SIFIRLA da sıfırlar.

**Kamera:** El takibi **yalnızca bu Mac'in kendi kamerasını** (bu Mac'te “MacBook Air Camera”) kullanır.
Sanal kameralar (ör. LinkMyMac), iPhone/Süreklilik kamerası ve harici kameralar hiçbir zaman açılmaz.
Kamera `system_profiler` listesinden adıyla bulunur, kimliği hatırlanır; görüntü donarsa aynı kamera bir
kez yeniden açılır, yine donuksa el takibi açıklamayla durur (başka kameraya geçmez). Grafikteki
`◉ KAMERA: MAC` yalnızca bilgi verir.

Yanlışlıkla tetiklenmeye karşı: el önce birkaç kare boyunca kararlı görünmeli, sıkıştırma art arda
karelerle “kazanılmalı”, bırakma için daha yüksek eşik (histerezis) var, yumruk sıkıştırma sayılmaz,
uzaktaki/küçük eller yok sayılır ve yakınlaştırmada ölü bölge var. Takip kaybolursa tutulan düğüm olduğu yerde bırakılır. El hareketleri **hiçbir
zaman** dosya açmaz, tıklamaz veya Mac'te başka işlem yapmaz.

## Gizlilik ve kapsam
- JARVIS yalnızca **seçtiğin klasörleri** tarar; kök/ana klasör ve sistem klasörleri seçilemez,
  sembolik bağlantılar izlenmez, gizli klasörler, `node_modules`, `venv` vb. atlanır.
- Tarama, indeks ve dosya metinleri yerelde: `sistem/memory/second_brain.sqlite3`.
- JARVIS'e sesle sorduğunda cevabı üretmek için sonuçlar sesli asistana (Gemini) gider: `query`/`show`
  için dosya adları ve kaynak satırları, `read` için en fazla 6 kısa alıntı (toplam ≤ 6000 karakter).
  Bütün not veya klasör gönderilmez.
- Kamera görüntüsü yalnızca el takip sürecinde, bellekte işlenir; kaydedilmez, gönderilmez.
  Grafik başlığında kamera açıkken **● KAMERA AÇIK** yazar.
- El takibi olayları (kamera seçimi, hatalar, 30 sn'de bir kare sayısı) `sistem/memory/second_brain.log`
  dosyasına yazılır; görüntü yazılmaz.
- Kamera yalnızca: grafik penceresi açık **ve** el kontrolü AÇIK iken çalışır. Pencere simge
  durumuna alınınca veya 5 dk el görülmezse kamera kapanır.

## Mimari (akıcılık ve pil için)
```
JARVIS ana arayüz (Tk, 25 FPS)  ──◈ BEYİN──▶  brain.window  (ayrı süreç, Tk grafik)
      │ yalnızca düğmeler + süreç başlatma          │ stdin/stdout JSON
      │                                              ├──▶ brain.hand_tracker (ayrı süreç, hand_env: MediaPipe)
      └──▶ brain.indexer (ayrı süreç, nice 10) ──▶ memory/second_brain.sqlite3 ◀── brain.query (sesli araç)
```
- Grafik çizimi ve el takibi ana arayüzün süreci dışında; özellik kapalıyken ana döngüye ek iş yok.
- Grafik boşta iken döngü çalışmaz (olay güdümlü); büyük grafikte en bağlantılı 1200 düğüm gösterilir.
- El takibi el görünürken 30 FPS, el yokken 6 FPS çalışır.

## Bağımlılıklar
- İkinci Beyin: yeni paket gerektirmez (mevcut Python + numpy).
- El takibi: MediaPipe, ana ortamı bozmamak için **ayrı** bir ortamda (`sistem/hand_env`).
  Grafikte ✋ **EL TAKİBİNİ KUR** düğmesi bir kerelik kurar (~110 MB indirme, ~320 MB disk; internet gerekir).
  Eksikse uygulama çökmez; açıklama gösterir, fare/klavye çalışır.
- El modeli: `brain/models/hand_landmarker.task` (Google MediaPipe, Apache‑2.0).

## Ölçülen performans (bu Mac, 29.09.2026)
| Durum | Ana arayüz FPS | kare aralığı p95 | ana süreç CPU | ek süreçler CPU |
|---|---|---|---|---|
| Eski sürüm (yedek) | 23,36 | 43,5 ms | %51,5 | — |
| Yeni sürüm, İkinci Beyin kapalı | 23,46 | 43,3 ms | %51,0 | — |
| Yeni sürüm, grafik + el takibi açık (el yok) | 23,43 | 43,4 ms | %52,9 | %5,6 |

- El görünürken takip süreci: ~21 FPS, kare başı ~13,5 ms, tek çekirdeğin ~%33'ü.
- Grafik (1200 düğüm / 2485 kenar, gerçek Tk): kaydırma ~9 ms/kare, yakınlaştırma ~11 ms/kare,
  düğüm taşıma ~1,3 ms/kare, seçim ~16 ms. macOS'ta ince çizgilerin kenar yumuşatması
  büyük grafikte kapatılır (ölçüldü: 239 ms → 9 ms/kare).
- Not: Ana döngü `after(40)` + çizim süresiyle eskiden de ~23,4 FPS çalışıyordu; değişmedi.

## Testler
```
cd sistem && venv/bin/python -m unittest discover -s tests -t . -v
```
Grafik verisi, açık/tahmini ilişki doğruluğu, kapsam dışı dosyalara erişmeme, sentetik ve gerçek
(MediaPipe referans) el iskeletleriyle hareketler, kamera izni/erişim hataları, aç/kapa ve kameranın
bırakılması, ekransız pencere/arayüz duman testleri ve yedeğe göre gerileme denetimi.
Ana arayüz FPS ölçümü: `JARVIS_FPS_PROBE=1 venv/bin/python main.py` → `memory/fps_probe.log`.

## Kaynak ve lisans notları
- El hareketi yaklaşımı için teknik referans: Zubair Trabzada, *holo-gestures* (MIT). Kod kopyalanmadı;
  sıkıştırma eşiği/histerezis, ✌ ile sıfırlama, el kimliği eşleştirme ve One Euro filtresi gibi fikirler
  burada Python ile baştan yazıldı.
- MediaPipe ve `hand_landmarker.task`: Google, Apache License 2.0.
- One Euro filtresi: Casiez, Roussel, Vogel (CHI 2012).

**İki el (04.10):** MediaPipe'ın sağ/sol el güveni düşük olan ikinci el (yan dönük, avuç tersi, diğer ele yakın) artık atılmıyor (eşik 0,55 → 0,20; algılama/varlık eşikleri 0,6 → 0,5). Tek eli iki el sanan "hayalet" kopya ayıklanır. Günlükte 30 sn'de bir `iki_el_ham / iki_el_geçerli / hayalet` sayıları yazılır (eşik ayarı için).

**El algısı (04.10):** Kamera 640×480'de kalır. 1760×1328 denendi (18:40) ama Mac kamerasında bu biçim daha GENİŞ açılı: eller görüntüde küçüldü (el boyu 0,25→0,22, eli görülen kare %94→%86), ikinci el zor yakalandı ve köşelerde parmaklar kadrajdan taştı → geri alındı. Kalan iyileştirme: parmaklar açıkça kapalıysa (oran ≤ 0,22, art arda 4 kare) el hareket hâlindeyken de tutulur (günlükte `tutus:hızlı_net`); kararsız oranda ve savrulmada (>6 el boyu/sn) tutulmaz. Sıkıştırma eşikleri değişmedi.

**Sıkıştırma ölçüsü (04.10):** Parmak uçları arasındaki derinlik farkının küçük kısmı (el boyunun %25'ine kadar, MediaPipe'ın derinlik gürültüsü) yok sayılır → pratikte 2B ölçüm (holo-gestures gibi); başparmak işaretin açıkça arkasındaysa (ekranda üst üste ama değmiyor) yine tutuş sayılmaz. Eşikler aynı. Günlükteki teşhis satırlarında artık imlecin kaba konumu (`imleç=x,y`, 0-1) ve elin kadraj kenarına uzaklığı (`kenar=`) da var: köşelerdeki kaçırmalar ayırt edilebilsin.
