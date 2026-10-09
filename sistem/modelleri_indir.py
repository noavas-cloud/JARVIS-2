"""Yerel Whisper ses modellerini indirir (internetsiz komutlar ve "Hey Jarvis" için).

Kullanım (sistem klasöründe):  venv/bin/python modelleri_indir.py
Modeller sistem/models içine iner (~600 MB); bir kez yeterli.
"""
from pathlib import Path

from faster_whisper.utils import download_model

MODEL_DIR = Path(__file__).resolve().parent / "models"

for size in ("base", "small"):
    print(f"Whisper {size} indiriliyor…", flush=True)
    download_model(size, cache_dir=str(MODEL_DIR))
print("Bitti:", MODEL_DIR)
