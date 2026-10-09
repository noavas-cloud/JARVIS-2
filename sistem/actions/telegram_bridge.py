"""Owner-paired Telegram transport. No model or desktop tools live here.

Only the callback receives authenticated, fresh private messages. Telegram's
update offset is durably advanced BEFORE the callback: interrupted operations
are reported as uncertain, never replayed. API: https://core.telegram.org/bots/api
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time

import requests

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "telegram_private.json"
MAX_VOICE_BYTES = 10 * 1024 * 1024
MAX_AGE = 300
_LOCK = threading.RLock()
_TOKEN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,150}\Z")


class TelegramError(Exception):
    """Sanitized message only; provider errors can include the credential URL."""


def _defaults():
    return dict(enabled=False, token="", owner_id=None, offset=0, pair_hash="",
                pair_until=0, pair_after=0, inflight=None, last_status="Kurulum bekleniyor.")


def _read(path):
    if path.is_symlink():
        raise TelegramError("Telegram ayar yolu güvenli değil.")
    if not path.exists():
        return _defaults()
    try:
        if path.stat().st_size > 32_000:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("offset", 0)) is not int:
            raise ValueError()
        cfg = _defaults()
        cfg.update({key: data[key] for key in cfg if key in data})
        if type(cfg["enabled"]) is not bool or not isinstance(cfg["token"], str):
            raise ValueError()
        if cfg["owner_id"] is not None and (type(cfg["owner_id"]) is not int or cfg["owner_id"] <= 0):
            raise ValueError()
        if cfg["offset"] < 0 or not isinstance(cfg["pair_hash"], str):
            raise ValueError()
        if not isinstance(cfg["pair_until"], (int, float)) or not isinstance(cfg["pair_after"], (int, float)):
            raise ValueError()
        os.chmod(path, 0o600)
        return cfg
    except (OSError, ValueError, TypeError):
        raise TelegramError("Telegram ayarları okunamadı; güvenli biçimde durduruldu.") from None


def _write(path, cfg):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".telegram-", delete=False, encoding="utf-8") as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(cfg, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked(path=None):
    path = Path(path or CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def settings(path=None):
    try:
        with _locked(path) as file:
            return _read(file)
    except (OSError, TelegramError):
        return {**_defaults(), "config_error": True, "last_status": "Telegram ayarları okunamadı; bağlantı kapalı."}


def save_settings(updates, path=None):
    with _locked(path) as file:
        cfg = _read(file)
        token = str(updates.get("token", cfg["token"]) or "").strip()
        if token and not _TOKEN.fullmatch(token):
            raise TelegramError("Bot token biçimi geçerli değil; BotFather'dan verilen token'ı kullan.")
        if token != cfg["token"]:
            cfg = _defaults()  # A new bot must never inherit an owner's pairing or cursor.
            cfg["token"] = token
        if "enabled" in updates:
            cfg["enabled"] = updates["enabled"] is True
        if cfg["enabled"] and not token:
            raise TelegramError("Önce BotFather token'ını kaydet.")
        if not cfg["enabled"]:
            cfg.update(pair_hash="", pair_until=0, last_status="Telegram kapalı.")
        _write(file, cfg)
        return cfg


def begin_pairing(path=None):
    with _locked(path) as file:
        cfg = _read(file)
        if not cfg["token"] or not cfg["enabled"]:
            raise TelegramError("Önce token'ı kaydet ve Telegram bağlantısını etkinleştir.")
        if cfg["owner_id"]:
            raise TelegramError("Önce mevcut hesabın bağlantısını kaldır.")
        code = secrets.token_hex(8)
        cfg.update(pair_hash=hashlib.sha256(code.encode()).hexdigest(), pair_until=time.time() + 300,
                   pair_after=time.time(), last_status="Beş dakika içinde özel sohbette eşleştirme bekleniyor.")
        _write(file, cfg)
        return code


def disconnect_owner(path=None):
    with _locked(path) as file:
        cfg = _read(file)
        cfg.update(enabled=False, owner_id=None, pair_hash="", pair_until=0,
                   last_status="Hesap bağlantısı kaldırıldı; Telegram kapalı.")
        _write(file, cfg)


def _bounded(response, limit):
    data = bytearray()
    for chunk in response.iter_content(16_384):
        data.extend(chunk)
        if len(data) > limit:
            raise TelegramError("Telegram yanıtı boyut sınırını aştı.")
    return bytes(data)


class TelegramAPI:
    def __init__(self, token):
        if not _TOKEN.fullmatch(token):
            raise TelegramError("Telegram token'ı eksik veya geçersiz.")
        self.token = token

    def call(self, method, payload=None):
        if method not in {"getMe", "getUpdates", "getFile", "sendMessage"}:
            raise TelegramError("Telegram işlemi desteklenmiyor.")
        try:
            with requests.post(f"https://api.telegram.org/bot{self.token}/{method}",
                               json=payload or {}, timeout=(5, 22), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise TelegramError("Telegram bağlantısı başarısız. Token, internet veya başka bot bağlantısını kontrol et.")
                body = json.loads(_bounded(response, 2 * 1024 * 1024))
            if not isinstance(body, dict) or body.get("ok") is not True:
                raise TelegramError("Telegram işlemi doğrulanamadı.")
            return body.get("result")
        except (requests.RequestException, ValueError, TypeError):
            raise TelegramError("Telegram'a ulaşılamadı veya yanıt okunamadı.") from None

    def download_voice(self, voice):
        try:
            duration, size = float(voice.get("duration", 0)), int(voice.get("file_size", 0))
        except (ValueError, TypeError):
            raise TelegramError("Sesli mesaj bilgisi geçersiz.") from None
        mime = voice.get("mime_type") or "audio/ogg"
        if not 0 < duration <= 60 or not 0 <= size <= MAX_VOICE_BYTES or mime not in {"audio/ogg", "audio/opus", "audio/mpeg", "audio/mp4"}:
            raise TelegramError("Sesli mesaj en fazla 60 saniye ve 10 MB olmalı.")
        file_id = voice.get("file_id")
        if not isinstance(file_id, str) or not file_id or len(file_id) > 512:
            raise TelegramError("Sesli mesaj kimliği geçersiz.")
        item = self.call("getFile", {"file_id": file_id})
        path = item.get("file_path", "") if isinstance(item, dict) else ""
        if (not re.fullmatch(r"[A-Za-z0-9_/-]+\.[A-Za-z0-9]+", path) or path.startswith("/")
                or ".." in path or int(item.get("file_size", 0)) > MAX_VOICE_BYTES):
            raise TelegramError("Ses dosyasının Telegram adresi veya boyutu doğrulanamadı.")
        try:
            with requests.get(f"https://api.telegram.org/file/bot{self.token}/{path}",
                              timeout=(5, 20), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise TelegramError("Sesli mesaj indirilemedi.")
                content = _bounded(response, MAX_VOICE_BYTES)
            if not content:
                raise TelegramError("Sesli mesaj boş.")
            return content, mime
        except requests.RequestException:
            raise TelegramError("Sesli mesaj indirilemedi.") from None


def check_connection(token=None, path=None):
    try:
        result = TelegramAPI(token if token is not None else settings(path)["token"]).call("getMe")
        if not isinstance(result, dict) or not result.get("is_bot"):
            raise TelegramError("Bot hesabı doğrulanamadı.")
        username = str(result.get("username", ""))
        return {"status": "ok", "message": "Bot bağlantısı doğrulandı: @" + username[:80]}
    except TelegramError as exc:
        return {"status": "error", "message": str(exc)}


class TelegramBridge:
    def __init__(self, handler, on_status=None, config_path=None, api_factory=None, clock=time.time):
        self.handler, self.on_status = handler, on_status
        self.path = Path(config_path or CONFIG_PATH)
        self.api_factory, self.clock = api_factory or TelegramAPI, clock
        self._poll_lock = asyncio.Lock()

    def _status(self, text):
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _claim(self, update, token):
        """Atomically consume update and authenticate its owner before any action."""
        with _locked(self.path) as file:
            cfg = _read(file)
            uid = update.get("update_id")
            if not cfg["enabled"] or cfg["token"] != token or type(uid) is not int or uid < cfg["offset"]:
                return None
            cfg["offset"] = uid + 1
            message = update.get("message") or {}
            chat, sender = message.get("chat") or {}, message.get("from") or {}
            owner = sender.get("id")
            stamp = message.get("date", 0)
            valid = (chat.get("type") == "private" and type(owner) is int and owner > 0
                     and chat.get("id") == owner and not sender.get("is_bot")
                     and isinstance(stamp, (int, float)) and -30 <= self.clock() - stamp <= MAX_AGE
                     and not message.get("forward_origin") and not message.get("forward_date"))
            outcome = None
            if valid and cfg["owner_id"] == owner:
                cfg.update(inflight=uid, last_status="Telegram isteği işleniyor.")
                outcome = (owner, message, False)
            elif valid and cfg["owner_id"] is None and cfg["pair_hash"] and self.clock() <= cfg["pair_until"] and stamp >= int(cfg["pair_after"]):
                parts = str(message.get("text", "")).split()
                candidate = hashlib.sha256(parts[1].encode()).hexdigest() if len(parts) == 2 and parts[0] in {"/start", "/pair"} else ""
                if candidate and hmac.compare_digest(cfg["pair_hash"], candidate):
                    cfg.update(owner_id=owner, pair_hash="", pair_until=0, last_status="Hesap eşleştirildi.")
                    outcome = (owner, message, True)
            _write(file, cfg)
            return outcome

    def _active(self, token, owner):
        cfg = settings(self.path)
        return cfg["enabled"] and cfg["token"] == token and cfg["owner_id"] == owner

    def _finish(self, token, uid, text):
        with _locked(self.path) as file:
            cfg = _read(file)
            if cfg["token"] == token and cfg["inflight"] == uid:
                cfg.update(inflight=None, last_status=text)
                _write(file, cfg)
        self._status(text)

    async def poll_once(self):
        async with self._poll_lock:
            cfg = settings(self.path)
            if not cfg["enabled"] or not cfg["token"]:
                return False
            api = self.api_factory(cfg["token"])
            updates = await asyncio.to_thread(api.call, "getUpdates", {
                "offset": cfg["offset"], "timeout": 15, "limit": 20, "allowed_updates": ["message"]})
            if not isinstance(updates, list):
                raise TelegramError("Telegram mesaj listesi okunamadı.")
            for update in updates:
                if not isinstance(update, dict):
                    continue
                claimed = self._claim(update, cfg["token"])
                if not claimed:
                    continue
                owner, message, paired = claimed
                if paired:
                    reply = "JARVIS eşleştirildi. Mac'te JARVIS açıkken özel mesaj veya en fazla 60 saniyelik sesli not gönderebilirsin."
                else:
                    text = str(message.get("text") or "").strip()
                    try:
                        audio, mime = None, None
                        if message.get("voice"):
                            audio, mime = await asyncio.to_thread(api.download_voice, message["voice"])
                        elif not text or len(text) > 6000:
                            raise TelegramError("Kısa bir metin veya en fazla 60 saniyelik sesli not gönder.")
                        if not self._active(cfg["token"], owner):
                            self._finish(cfg["token"], update["update_id"], "Telegram kapatıldı; istek başlatılmadı.")
                            continue
                        reply = str(await self.handler(text, audio_bytes=audio, mime_type=mime) or "Yanıt üretilemedi; tamamlandığını doğrulayamıyorum.")
                        self._finish(cfg["token"], update["update_id"], "Son Telegram isteği yanıtlandı.")
                    except TelegramError as exc:
                        reply = str(exc)
                        self._finish(cfg["token"], update["update_id"], reply)
                    except asyncio.CancelledError:
                        self._status("Telegram isteği kesildi; sonucu kontrol et, otomatik tekrarlanmayacak.")
                        raise
                    except Exception:
                        reply = "İstek işlenirken sorun oluştu. Sonucu doğrulayamıyorum; işlemi otomatik tekrarlamadım."
                        self._finish(cfg["token"], update["update_id"], reply)
                if self._active(cfg["token"], owner):
                    for start in range(0, min(len(reply), 12000), 3500):
                        if not self._active(cfg["token"], owner):
                            break
                        await asyncio.to_thread(api.call, "sendMessage", {"chat_id": owner, "text": reply[start:start + 3500],
                            "link_preview_options": {"is_disabled": True}})
            return True

    async def run(self):
        if settings(self.path).get("inflight") is not None:
            self._status("Önceki Telegram isteği kesilmiş olabilir; otomatik tekrarlanmadı. Sonucu kontrol et.")
        while True:
            try:
                active = await self.poll_once()
                await asyncio.sleep(0.2 if active else 2)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._status("Telegram bağlantısı bekleniyor. Kesilmiş istekler otomatik tekrarlanmaz.")
                await asyncio.sleep(5)
