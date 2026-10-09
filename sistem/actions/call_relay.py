"""Ara ve söyle: kullanıcının istediği kişiyi arayıp onun söylemek istediğini sesli okur.

Öncelik bağlı Android telefondadır (JARVIS uygulaması açık ve Mac'e bağlıysa): arama telefonun kendi
Telefon uygulamasıyla, kullanıcının numarasından yapılır; telefonda onay ekranı çıkar. Telefon bağlı değilse
Mac'in Telefon uygulaması açılır (iPhone olmadan yalnızca FaceTime sesli arama yapabilir) ve mesaj Mac
hoparlöründen okunur. Hiçbir dış arama servisi kullanılmaz.

Sınırlar (işletim sistemleri izin vermez): mesaj görüşme hattına doğrudan verilemez, hoparlörden okunur ve
mikrofonla iletilir; karşı tarafın cevabı duyulamaz; karşı tarafın açtığı an bilinemez, mesaj bekleme süresi
sonunda okunur. Sonuçlar bu yüzden "okundu" der, "duyuldu" demez.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SERVER = "http://127.0.0.1:8765"
MAX_MESSAGE = 600
SPEAK_DELAY_S = 8          # arama başladıktan sonra mesaj okunmadan önce beklenen süre (çalma + açma)
REPEATS = 2
MAC_VOICE = "Yelda"


def normalize_number(raw: str) -> str | None:
    """Türkiye biçimlerini E.164'e çevirir: 0532… / 532… / +90 532… → +90532…; geçersizse None."""
    text = str(raw or "").strip()
    if not text:
        return None
    plus = text.startswith("+") or text.startswith("00")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    if not plus:
        if len(digits) == 11 and digits.startswith("0"):
            digits = "90" + digits[1:]
        elif len(digits) == 10 and digits.startswith("5"):
            digits = "90" + digits
    if not 8 <= len(digits) <= 15:
        return None
    return "+" + digits


def clean_message(message: str) -> str | None:
    text = " ".join(str(message or "").split())
    if not text or len(text) > MAX_MESSAGE:
        return None
    return text


def resolve_contact(name: str, number: str) -> dict:
    """Mac'teki kayıtlı kişilerden (WhatsApp hızlı kişiler + rehber) numara bulur."""
    if number:
        normalized = normalize_number(number)
        return {"status": "ok", "number": normalized} if normalized else {"status": "invalid_number"}
    if not name:
        return {"status": "needs_recipient"}
    try:
        from actions.whatsapp import _match_contacts
        entry, _score, candidates = _match_contacts(name)
    except Exception:
        return {"status": "not_found"}
    if entry is None:
        return {"status": "ambiguous", "candidates": candidates} if candidates else {"status": "not_found"}
    normalized = normalize_number(entry.get("value", ""))
    if not normalized:
        return {"status": "not_found"}
    return {"status": "ok", "number": normalized, "name": entry.get("display_name") or name}


def _admin_token() -> str:
    try:
        cfg = json.loads((BASE / "jarvis_web" / "web_config.json").read_text(encoding="utf-8"))
        return str(cfg.get("token", "") or "")
    except (OSError, ValueError):
        return ""


def _admin(path: str, payload: dict | None = None, timeout: float = 3.0) -> dict:
    token = _admin_token()
    if not token:
        raise OSError("telefon sunucusu kapalı")
    req = urllib.request.Request(SERVER + path, method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # yerel yönetim proxy'den geçmez
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read(65536))
        except ValueError:
            raise OSError(f"telefon sunucusu isteği reddetti ({exc.code})") from None


def phone_connected() -> bool:
    try:
        return int(_admin("/api/android/admin/status").get("phones_connected", 0)) > 0
    except (OSError, ValueError, TypeError):
        return False


def call_via_phone(name: str, number: str, message: str) -> dict:
    """Bağlı telefona gönderir; telefonda kullanıcı onaylayıp arama başlayınca (ya da vazgeçince) döner."""
    try:
        result = _admin("/api/android/admin/call", {"name": name, "number": number, "message": message},
                        timeout=150)
    except (OSError, ValueError) as exc:
        return {"status": "phone_unreachable", "message": f"Telefona ulaşılamadı: {exc}"}
    result.setdefault("via", "phone")
    return result


def call_via_mac(number: str, message: str, *, runner=subprocess.run, sleeper=time.sleep,
                 delay: float = SPEAK_DELAY_S, repeats: int = REPEATS) -> dict:
    """Mac'in Telefon uygulamasıyla arar ve mesajı hoparlörden okur. Onay çağırandan ÖNCE alınmış olmalı."""
    opened = runner(["/usr/bin/open", "tel:" + number], capture_output=True, timeout=15)
    if getattr(opened, "returncode", 1) != 0:
        return {"status": "error", "via": "mac",
                "message": "Mac'in Telefon uygulaması açılamadı; arama yapılmadı."}
    sleeper(delay)
    for i in range(repeats):
        runner(["/usr/bin/say", "-v", MAC_VOICE, message], capture_output=True, timeout=120)
        if i + 1 < repeats:
            sleeper(1.5)
    return {"status": "spoken", "via": "mac", "number": number, "repeats": repeats,
            "message": ("Mac'in Telefon uygulaması açıldı ve mesaj Mac hoparlöründen okundu. Bu Mac'e bağlı "
                        "iPhone olmadığı için yalnızca FaceTime sesli arama yapılabilir; karşı taraf FaceTime "
                        "kullanmıyorsa ya da aramayı onaylamadıysan arama gerçekleşmemiş olabilir. Karşı tarafın "
                        "duyduğu doğrulanamaz.")}
