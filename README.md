# JARVIS 2

macOS için Türkçe sesli asistan (Gemini Live) + Android uygulaması.

- `sistem/` — Mac uygulaması (Python, Tk arayüzü). Giriş: `python -m jarvis.app`
- `android/` — Android uygulaması (Kotlin + Jetpack Compose). Ayrıntı: `android/README.md`

## Kurulum (Mac)

```bash
cd sistem
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config/api_keys.example.json config/api_keys.json   # Gemini API anahtarını yaz
venv/bin/python -m jarvis.app
```

Testler: `cd sistem && venv/bin/python -m unittest discover -s tests -t .`

Kişisel veriler (API anahtarları, sohbet/hafıza veritabanları, eşleşmiş cihazlar, kayıtlar) depoya dahil değildir.
