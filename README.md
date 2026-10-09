# JARVIS 2

macOS için Türkçe sesli asistan (Gemini Live) + Android uygulaması.

- `sistem/` — Mac uygulaması (Python, Tk arayüzü)
- `android/` — Android uygulaması (Kotlin + Jetpack Compose). Ayrıntı: `android/README.md`

Yalnız **macOS**'ta çalışır.

## Kurulum (Mac)

Gerekenler: Python 3, [Homebrew](https://brew.sh) ve kendi **Gemini API anahtarın**
([aistudio.google.com](https://aistudio.google.com/apikey) üzerinden ücretsiz alınır).

1. Depoyu indir (yeşil **Code** › **Download ZIP**) ve ZIP'i aç.
2. Terminalde:

   ```bash
   brew install portaudio
   cd JARVIS-2-main/sistem
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   cp config/api_keys.example.json config/api_keys.json
   ```

3. `config/api_keys.json` dosyasını aç, `gemini_api_key` satırına kendi anahtarını yaz.
4. (İsteğe bağlı) İnternetsiz komutlar ve "Hey Jarvis" için yerel ses modellerini indir (~600 MB):

   ```bash
   venv/bin/python modelleri_indir.py
   ```

5. Başlat:

   ```bash
   venv/bin/python -m jarvis.app
   ```

   İlk açılışta macOS mikrofon, ekran kaydı gibi izinleri sorar. İzin verilmezse ilgili özellikler çalışmaz.

El kontrolü (İkinci Beyin) kendi ortamını uygulama içindeki **KUR** düğmesiyle kurar.

## Testler

```bash
cd sistem && venv/bin/python -m unittest discover -s tests -t .
```

Kişisel veriler (API anahtarları, sohbet/hafıza veritabanları, eşleşmiş cihazlar, kayıtlar) depoya dahil değildir.
