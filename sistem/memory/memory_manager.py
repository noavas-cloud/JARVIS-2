"""
Kalıcı bellek — JSON dosyasına kaydedilir.
"""

import json
import os
import re
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Other platforms still use the in-process lock.
    fcntl = None

BASE_DIR    = Path(__file__).resolve().parent.parent
MEMORY_FILE = BASE_DIR / "memory" / "memory.json"
_MEMORY_LOCK = threading.RLock()


@contextmanager
def _locked_memory():
    """Serialize the full read/merge/write across desktop and phone processes."""
    with _MEMORY_LOCK:
        path = Path(MEMORY_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise OSError("Hafıza yolu sembolik bağlantı olamaz.")
        lock_path = path.with_name(path.name + ".lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _load_memory_unlocked(*, strict: bool = False) -> dict:
    path = Path(MEMORY_FILE)
    if not path.exists():
        return {}
    try:
        if path.is_symlink():
            raise OSError("Hafıza yolu sembolik bağlantı olamaz.")
        with path.open("r", encoding="utf-8") as stream:
            memory = json.load(stream)
        if not isinstance(memory, dict):
            raise ValueError("Hafıza dosyası bir JSON nesnesi olmalı.")
        return memory
    except (OSError, ValueError):
        # A damaged store must never be silently replaced during a write.
        if strict:
            raise
        return {}


def load_memory() -> dict:
    try:
        with _locked_memory():
            return _load_memory_unlocked()
    except (OSError, ValueError):
        pass
    return {}


def update_memory(data: dict):
    if not isinstance(data, dict):
        raise TypeError("Hafıza güncellemesi bir sözlük olmalı.")
    with _locked_memory():
        mem = _load_memory_unlocked(strict=True)
        _deep_merge(mem, data)
        # güncellenen kayıtları kategorisinin sonuna taşı: istem sığmadığında en yeniler korunur
        for cat, items in data.items():
            if isinstance(items, dict) and isinstance(mem.get(cat), dict):
                for key in list(items):
                    if key in mem[cat]:
                        mem[cat][key] = mem[cat].pop(key)
        _write_memory_unlocked(mem)


def _write_memory(mem: dict):
    with _locked_memory():
        _write_memory_unlocked(mem)


def _write_memory_unlocked(mem: dict):
    path = Path(MEMORY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(mem, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _deep_merge(base: dict, update: dict):
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _normalize_text(text: str) -> str:
    text = (text or "").strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("ı", "i")
    return " ".join(text.split())


def _entry_value_text(value) -> str:
    if isinstance(value, dict):
        base = value.get("value")
        if base is not None:
            return str(base)
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _tokenize_text(text: str) -> list[str]:
    normalized = _normalize_text(text)
    return [token for token in re.split(r"[^a-z0-9]+", normalized) if token]


def _match_score(needle: str, item_key: str, item_value) -> float:
    """0 = eşleşmez. Tam ifade anahtarda/değerde geçerse yüksek; aksi hâlde sözcük eşleşme oranı.

    Eskiden 1-2 harfli sözcükler ("a", "i") her aramanın içinde bulunduğu için yanlış kayıt
    siliniyordu ve kategori adı da aranıyordu. Artık yalnızca ≥3 harfli, tam ya da ek almış
    (ortak kök ≥4 harf) sözcükler sayılır.
    """
    key_n = _normalize_text(item_key)
    val_n = _normalize_text(_entry_value_text(item_value))
    if needle and (needle == key_n or needle in val_n or (len(needle) >= 4 and needle in key_n)):
        return 10.0
    tokens = [tok for tok in _tokenize_text(needle) if len(tok) >= 3]
    if not tokens:
        return 0.0
    entry_tokens = [t for t in _tokenize_text(key_n) + _tokenize_text(val_n) if len(t) >= 3]

    def same(a: str, b: str) -> bool:
        if a == b:
            return True
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        return len(short) >= 4 and long_.startswith(short)   # Türkçe ekler: "toplantı" ~ "toplantısı"

    matched = sum(1 for tok in tokens if any(same(tok, et) for et in entry_tokens))
    need = 1 if len(tokens) == 1 else min(2, len(tokens))
    if matched < need:
        return 0.0
    return matched / len(tokens)


def delete_memory(category: str = "", key: str = "", match_text: str = "") -> str:
    with _locked_memory():
        return _delete_memory_unlocked(category, key, match_text)


def _delete_memory_unlocked(category: str, key: str, match_text: str) -> str:
    mem = _load_memory_unlocked(strict=True)
    if not mem:
        return "Hafizada silinecek bir kayit yok."

    category = (category or "").strip()
    key = (key or "").strip()
    match_text = (match_text or "").strip()

    if category and key:
        bucket = mem.get(category)
        if isinstance(bucket, dict) and key in bucket:
            del bucket[key]
            if not bucket:
                mem.pop(category, None)
            _write_memory_unlocked(mem)
            return f"{category}/{key} hafizadan kaldirildi."
        return "Bu hafiza kaydini bulamadim."

    needle = _normalize_text(match_text or key)
    if not needle:
        return "Silmek icin category/key veya match_text gerekli."

    candidates = []   # (puan, kategori, anahtar | None)
    for cat, bucket in mem.items():
        if category and cat != category:
            continue
        if not isinstance(bucket, dict):
            score = _match_score(needle, cat, bucket)
            if score:
                candidates.append((score, cat, None))
            continue
        for item_key, item_value in bucket.items():
            score = _match_score(needle, item_key, item_value)
            if score:
                candidates.append((score, cat, item_key))
    if not candidates:
        return "Eslestigim bir hafiza kaydi bulamadim."
    candidates.sort(key=lambda c: -c[0])
    best = candidates[0][0]
    top = [c for c in candidates if c[0] >= best - 1e-9]
    if len(top) > 1:
        names = ", ".join(f"{c}/{k}" if k else c for _, c, k in top[:5])
        return ("Birden fazla hafiza kaydi eslesti; hicbiri silinmedi. Hangisi oldugunu category/key ile "
                f"belirt: {names}")
    _, cat, item_key = top[0]
    if item_key is None:
        del mem[cat]
        _write_memory_unlocked(mem)
        return f"{cat} hafizadan kaldirildi."
    bucket = mem[cat]
    del bucket[item_key]
    if not bucket:
        mem.pop(cat, None)
    _write_memory_unlocked(mem)
    return f"{cat}/{item_key} hafizadan kaldirildi."


def format_memory_for_prompt(memory: dict, max_chars: int = 12000) -> str:
    if not memory:
        return ""
    lines = ["[KULLANICI HAKKINDA BİLGİLER]"]
    for category, items in memory.items():
        if isinstance(items, dict):
            for key, val in items.items():
                if category == "whatsapp_contacts" and isinstance(val, dict):
                    display_name = val.get("display_name", key)
                    value = val.get("value", "")
                    aliases = val.get("aliases", [])
                    alias_str = ""
                    if isinstance(aliases, list) and aliases:
                        alias_str = f" aliases={', '.join(str(a) for a in aliases)}"
                    lines.append(f"  {category}/{display_name}: {value}{alias_str}")
                else:
                    value = val.get("value", val) if isinstance(val, dict) else val
                    lines.append(f"  {category}/{key}: {value}")
        else:
            lines.append(f"  {category}: {items}")
    limit = max(512, min(24000, int(max_chars)))
    text = "\n".join(lines)
    if len(text) > limit:
        # Sığmıyorsa en YENİ kayıtlar korunur (eskiden baştan kesildiği için son eklenenler düşüyordu).
        # Yeni/güncellenen kayıtlar kategorilerinin sonunda durur (bkz. update_memory).
        note = "\n[Daha eski bazı hafıza kayıtları sığmadığı için bu bağlama eklenmedi.]"
        budget = limit - len(note) - len(lines[0]) - 1
        keep: list[int] = []
        for idx in range(len(lines) - 1, 0, -1):
            cost = len(lines[idx]) + 1
            if cost > budget:
                continue
            budget -= cost
            keep.append(idx)
        text = "\n".join([lines[0]] + [lines[i] for i in sorted(keep)]) + note
    return text
