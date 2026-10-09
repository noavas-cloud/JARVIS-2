"""Private Android device credentials and short-lived, single-use pairing.

The desktop master token never leaves the Mac. Only SHA-256 digests of random
device tokens are persisted. Pairing challenges live in memory and expire even
if the wall clock changes. This module has no application or network side effects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path


class PairingError(ValueError):
    pass


class AndroidPairing:
    TTL = 300
    MAX_ATTEMPTS = 5
    MAX_DEVICES = 10

    def __init__(self, path: Path, *, clock=time.monotonic):
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._code = None
        self._expires = 0.0
        self._attempts = 0
        self._devices = self._load()

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        # A malformed store fails closed. Do not silently discard existing devices.
        try:
            if self.path.stat().st_size > 64_000:
                raise ValueError("device store too large")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            devices = payload["devices"]
            if not isinstance(devices, dict) or len(devices) > self.MAX_DEVICES:
                raise ValueError("invalid device store")
            for key, item in devices.items():
                if not re.fullmatch(r"[a-f0-9]{64}", key) or not isinstance(item, dict):
                    raise ValueError("invalid device entry")
                if not isinstance(item.get("name"), str) or len(item["name"]) > 80:
                    raise ValueError("invalid device name")
            os.chmod(self.path, 0o600)
            return devices
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PairingError("Android cihaz kaydı okunamadı; masaüstünde kontrol edilmeli.") from exc

    def _save(self, devices: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".android-devices-", dir=str(self.path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "devices": devices}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def new_code(self) -> str:
        with self._lock:
            if len(self._devices) >= self.MAX_DEVICES:
                raise PairingError("En fazla 10 cihaz eşleştirilebilir. Önce eski eşleştirmeleri kaldır.")
            self._code = f"{secrets.randbelow(100_000_000):08d}"
            self._expires = self._clock() + self.TTL
            self._attempts = 0
            return self._code

    def pair(self, code: str, device_name: str) -> str:
        with self._lock:
            if not self._code or self._clock() >= self._expires or self._attempts >= self.MAX_ATTEMPTS:
                self._code = None
                raise PairingError("Kodun süresi doldu veya kod kullanıldı. Mac'ten yeni kod oluştur.")
            self._attempts += 1
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{8}", code) or not hmac.compare_digest(self._code, code):
                if self._attempts >= self.MAX_ATTEMPTS:
                    self._code = None
                raise PairingError("Eşleştirme kodu geçersiz. Mac'teki kodu kontrol et.")
            if len(self._devices) >= self.MAX_DEVICES:
                raise PairingError("Cihaz sınırına ulaşıldı. Önce eski eşleştirmeleri kaldır.")
            name = str(device_name).strip()
            if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
                raise PairingError("Cihaz adı geçersiz.")
            token = secrets.token_urlsafe(32)
            devices = dict(self._devices)
            devices[self.digest(token)] = {"name": name, "created_at": int(time.time())}
            self._save(devices)
            self._devices = devices
            self._code = None
            return token

    def authenticate(self, token: str) -> str | None:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        key = self.digest(token)
        with self._lock:
            return key if key in self._devices else None

    def has_device(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._devices

    def revoke(self, device_id: str):
        with self._lock:
            devices = dict(self._devices)
            devices.pop(device_id, None)
            self._save(devices)
            self._devices = devices

    def revoke_all(self):
        with self._lock:
            self._save({})
            self._devices = {}
            self._code = None

    @property
    def device_count(self) -> int:
        with self._lock:
            return len(self._devices)
