"""İkinci beyin ve el kontrolü ayarları.

Ayarlar mevcut JARVIS ayar dosyasında (config/api_keys.json) saklanır; böylece
yedekleme/geri alma ve diğer ayarlarla aynı yerde kalır.
"""

from __future__ import annotations

import os
from pathlib import Path

from brain import BASE_DIR  # noqa: F401 (hand_setup günlük yolu için de kullanılır)

DB_PATH = Path(os.environ.get("JARVIS_BRAIN_DB", str(BASE_DIR / "memory" / "second_brain.sqlite3")))
LOCK_PATH = DB_PATH.with_suffix(".lock")
HAND_ENV_DIR = Path(os.environ.get("JARVIS_HAND_ENV", str(BASE_DIR / "hand_env")))
HAND_MODEL_PATH = BASE_DIR / "brain" / "models" / "hand_landmarker.task"
HAND_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                  "hand_landmarker/float16/latest/hand_landmarker.task")
LOG_PATH = BASE_DIR / "memory" / "second_brain.log"

KEY_SOURCES = "brain_sources"
KEY_HAND = "hand_control_enabled"
KEY_HAND_CAMERA_UID = "hand_camera_uid"   # Mac'in kendi kamerasının kimliği (bulunduktan sonra sabitlenir)
KEY_VIEW_3D = "brain_view_3d"             # grafik 3B mi gösterilsin (varsayılan: evet)

# Tüm bilgisayarı taramayı önlemek için kök/ana klasör seçimi reddedilir.
_FORBIDDEN_EXACT = {"/", "/Users", "/System", "/Library", "/Applications", "/Volumes",
                    "/private", "/usr", "/bin", "/sbin", "/etc", "/var", "/opt"}


def _load() -> dict:
    from app_config import load_app_config
    return load_app_config()


def _save(updates: dict) -> None:
    from app_config import save_app_config
    save_app_config(updates)


def normalize_source(path: str) -> str:
    return str(Path(os.path.expanduser(str(path or "").strip())).resolve())


def _forbidden_paths() -> set[str]:
    out = set(_FORBIDDEN_EXACT)
    for item in _FORBIDDEN_EXACT:   # macOS: /var → /private/var, /etc → /private/etc
        try:
            out.add(str(Path(item).resolve()))
        except (OSError, RuntimeError):
            pass
    return out


def validate_source(path: str, require_dir: bool = True) -> tuple[bool, str]:
    """Kaynak klasörün kabul edilip edilmeyeceğini ve nedenini döndürür.
    require_dir=False: zaten kayıtlı bir kaynak şu an takılı değilse (harici disk) silinmesin."""
    raw = str(path or "").strip()
    if not raw:
        return False, "Klasör yolu boş."
    p = Path(normalize_source(raw))
    if require_dir and not p.is_dir():
        return False, f"Klasör bulunamadı: {p}"
    s = str(p)
    if s in _forbidden_paths():
        return False, "Sistem veya kök klasörü seçilemez; belirli not/proje klasörlerini seç."
    home = Path.home().resolve()
    # Ana klasörü içeren her yol (/, /Users, /System/Volumes/Data/Users/…) tüm diski taramak demektir.
    if p == home or home.is_relative_to(p):
        return False, ("Ana klasörün tamamı seçilemez (tüm bilgisayarı taramamak için). "
                       "Belgeler içindeki belirli not/proje klasörlerini seç.")
    if Path("/System") in (p, *p.parents):
        return False, "Sistem veya kök klasörü seçilemez; belirli not/proje klasörlerini seç."
    if (home / "Library") in (p, *p.parents):
        return False, "Library klasörü uygulama verisidir; not/proje klasörü seç."
    return True, "ok"


def get_sources(config: dict | None = None) -> list[str]:
    cfg = config if config is not None else _load()
    raw = cfg.get(KEY_SOURCES) or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        norm = normalize_source(item)
        if norm not in out:
            out.append(norm)
    return out


def set_sources(paths: list[str]) -> list[str]:
    clean: list[str] = []
    for item in paths:
        # Yeni klasör add_source'ta doğrulanır; burada yalnızca güvenlik kuralı uygulanır ki
        # o an takılı olmayan kayıtlı kaynaklar sessizce silinmesin.
        ok, _ = validate_source(item, require_dir=False)
        if ok:
            norm = normalize_source(item)
            # İç içe seçimlerde üst klasör yeterli; alt klasörü ayrıca tutma.
            if any(Path(norm).is_relative_to(Path(c)) for c in clean):
                continue
            clean = [c for c in clean if not Path(c).is_relative_to(Path(norm))]
            clean.append(norm)
    _save({KEY_SOURCES: clean})
    return clean


def add_source(path: str) -> tuple[bool, str, list[str]]:
    ok, reason = validate_source(path)
    current = get_sources()
    if not ok:
        return False, reason, current
    norm = normalize_source(path)
    if norm in current:
        return True, "Bu klasör zaten ekli.", current
    if any(Path(norm).is_relative_to(Path(c)) for c in current):
        return True, "Bu klasör zaten ekli bir klasörün içinde; ayrıca eklenmedi.", current
    updated = set_sources(current + [norm])
    return True, "Klasör eklendi.", updated


def remove_source(path: str) -> list[str]:
    norm = normalize_source(path)
    return set_sources([p for p in get_sources() if p != norm])


def hand_control_enabled(config: dict | None = None) -> bool:
    cfg = config if config is not None else _load()
    return bool(cfg.get(KEY_HAND, False))


def set_hand_control(enabled: bool) -> bool:
    _save({KEY_HAND: bool(enabled)})
    return bool(enabled)


def hand_camera_uid(config: dict | None = None) -> str:
    """El takibi yalnızca Mac'in kendi kamerasını kullanır; bulunan kameranın kimliği burada tutulur."""
    cfg = config if config is not None else _load()
    value = cfg.get(KEY_HAND_CAMERA_UID, "")
    return value if isinstance(value, str) and 0 < len(value) <= 128 else ""


def set_hand_camera_uid(uid: str) -> None:
    if isinstance(uid, str) and 0 < len(uid) <= 128 and uid != hand_camera_uid():
        _save({KEY_HAND_CAMERA_UID: uid})


def view_3d_enabled(config: dict | None = None) -> bool:
    try:
        cfg = config if config is not None else _load()
    except Exception:
        return True
    value = cfg.get(KEY_VIEW_3D, True)
    return value if isinstance(value, bool) else True


def set_view_3d(enabled: bool) -> bool:
    try:
        _save({KEY_VIEW_3D: bool(enabled)})
    except OSError:
        pass
    return bool(enabled)
