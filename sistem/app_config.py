from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover (Windows)
    fcntl = None


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "api_keys.json"


DEFAULT_CONFIG = {
    "gemini_api_key": "",
    "voice": "Charon",
    "youtube_api_key": "",
    "youtube_channel_handle": "",
}


class ConfigFileError(OSError):
    """Ayar dosyası okunamıyor (ör. elle düzenlenip bozulmuş). Üzerine yazılmaz; anahtarlar kaybolmasın."""


def _read_existing() -> dict | None:
    """Dosya yoksa None; varsa içeriği. Bozuksa ConfigFileError."""
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigFileError(f"Ayar dosyası okunamadı: {exc}") from exc
    try:
        raw = json.loads(text) if text.strip() else None
    except ValueError as exc:
        raise ConfigFileError(f"Ayar dosyası bozuk ({CONFIG_PATH.name}): {exc}") from exc
    if raw is None:
        raise ConfigFileError(f"Ayar dosyası boş: {CONFIG_PATH.name}")
    if not isinstance(raw, dict):
        raise ConfigFileError(f"Ayar dosyası beklenen biçimde değil: {CONFIG_PATH.name}")
    return raw


def load_app_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        raw = _read_existing()
        if raw:
            config.update(raw)
    except ConfigFileError:
        pass
    return config


class _ConfigLock:
    """Ayar dosyasına yazan birden fazla süreç (ana arayüz, İkinci Beyin penceresi…) sırayla yazsın."""

    def __enter__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.fh = open(CONFIG_DIR / (CONFIG_PATH.name + ".lock"), "a+")
        if fcntl is not None:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            if fcntl is not None:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
        return False


def save_app_config(updates: dict) -> dict:
    """Güncellemeleri kilit altında okuyup-birleştirip atomik yazar.

    Eskiden dosya okunamazsa varsayılanlar yazılıyordu (Gemini anahtarı dahil her şey siliniyordu) ve
    yazma yerinde yapıldığından yarım dosya okunabiliyordu. Artık bozuk dosyanın üzerine yazılmaz.
    """
    with _ConfigLock():
        existing = _read_existing()
        config = dict(DEFAULT_CONFIG)
        if existing:
            config.update(existing)
        for key, value in (updates or {}).items():
            if value is None:
                continue
            config[key] = value
        tmp = CONFIG_PATH.with_name(f".{CONFIG_PATH.name}.{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(config, indent=4, ensure_ascii=False))
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp, 0o600)   # API anahtarı içerir
            except OSError:
                pass
            os.replace(tmp, CONFIG_PATH)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return config


def get_app_config_value(key: str, default=None):
    return load_app_config().get(key, default)


def has_gemini_api_key() -> bool:
    value = str(get_app_config_value("gemini_api_key", "") or "").strip()
    return bool(value)
