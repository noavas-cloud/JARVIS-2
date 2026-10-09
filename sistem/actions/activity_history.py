"""Small, local, opt-out foreground activity history for contextual recall."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app_config import get_app_config_value

ROOT = Path(__file__).resolve().parents[1]
HELPER_SOURCE = ROOT / "helpers/jarvis_activity_helper.swift"
HELPER_BIN = ROOT / "helpers/bin/jarvis-activity-helper"
DATABASE = ROOT / "config/activity_history.sqlite3"
RETENTION_DAYS = 7
MAX_ROWS = 5000
PRIVATE_WORDS = ("incognito", "private browsing", "gizli sekme", "gizli pencere")
SENSITIVE_WORDS = ("password", "parola", "şifre", "sifre", "one-time code", "doğrulama kodu")
SKIP_APPS = {"JARVIS", "ChatGPT", "System Settings", "Keychain Access"}
SKIP_BUNDLE_IDS = {"com.openai.codex", "com.apple.systempreferences", "com.apple.keychainaccess"}
SECRET_QUERY_KEYS = {"token", "access_token", "refresh_token", "auth", "authorization",
                     "password", "passwd", "secret", "api_key", "apikey", "code", "session"}
_BROWSER_IDS = {"com.google.Chrome", "com.microsoft.edgemac"}
_LOCK = threading.Lock()
_STARTED = False


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("ı", "i"))
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value).strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return ""
        # Fragments and common authentication parameters may contain credentials.
        if parsed.hostname.lower() in ("youtube.com", "www.youtube.com", "m.youtube.com") and parsed.path == "/watch":
            video = re.search(r"(?:^|&)v=([A-Za-z0-9_-]{11})(?:&|$)", parsed.query)
            return f"https://www.youtube.com/watch?v={video.group(1)}" if video else ""
        params = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                  if key.casefold() not in SECRET_QUERY_KEYS]
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/",
                           urlencode(params), ""))
    except ValueError:
        return ""


def _is_sensitive(app: str, title: str) -> bool:
    haystack = _fold(app + " " + title)
    return (app in SKIP_APPS or _fold(title).strip() in {"j.a.r.v.i.s", "jarvis"}
            or any(word in haystack for word in PRIVATE_WORDS + SENSITIVE_WORDS))


def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    db = sqlite3.connect(str(path), timeout=3)
    if new:
        os.chmod(path, 0o600)
    db.execute("""CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
        app TEXT NOT NULL, bundle_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_activity_last ON activity(last_seen)")
    return db


def record(app: str, bundle_id: str, title: str, url: str = "", now: float | None = None,
           path: Path = DATABASE) -> bool:
    if not app or bundle_id in SKIP_BUNDLE_IDS or _is_sensitive(app, title):
        return False
    url = _safe_url(url)
    title = title.strip()[:240]
    stamp = time.time() if now is None else now
    with _connect(path) as db:
        row = db.execute(
            "SELECT id, last_seen, app, bundle_id, title, url FROM activity ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and (app, bundle_id, title, url) == tuple(row[2:]) and stamp - row[1] < 90:
            db.execute("UPDATE activity SET last_seen=? WHERE id=?", (stamp, row[0]))
        else:
            db.execute("INSERT INTO activity(first_seen,last_seen,app,bundle_id,title,url) VALUES (?,?,?,?,?,?)",
                       (stamp, stamp, app[:80], bundle_id[:120], title, url))
        db.execute("DELETE FROM activity WHERE last_seen < ?", (stamp - RETENTION_DAYS * 86400,))
        db.execute("DELETE FROM activity WHERE id IN (SELECT id FROM activity ORDER BY id DESC LIMIT -1 OFFSET ?)",
                   (MAX_ROWS,))
    return True


_BUILD_RETRY_AFTER = 0.0


def _ensure_helper() -> bool:
    global _BUILD_RETRY_AFTER
    if HELPER_BIN.exists() and HELPER_BIN.stat().st_mtime >= HELPER_SOURCE.stat().st_mtime:
        return True
    with _LOCK:
        if HELPER_BIN.exists() and HELPER_BIN.stat().st_mtime >= HELPER_SOURCE.stat().st_mtime:
            return True
        # Derleme başarısızsa (ör. Xcode araçları yok) 4 sn'de bir swiftc çalıştırıp CPU yakmasın.
        if time.monotonic() < _BUILD_RETRY_AFTER:
            return False
        _BUILD_RETRY_AFTER = time.monotonic() + 600
        HELPER_BIN.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["/usr/bin/swiftc", "-module-cache-path", str(HELPER_BIN.parent / "swift-cache"),
                 str(HELPER_SOURCE), "-o", str(HELPER_BIN)],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode == 0:
            _BUILD_RETRY_AFTER = 0.0
        return result.returncode == 0


def active_snapshot() -> dict:
    try:
        if not _ensure_helper():
            return {}
        result = subprocess.run([str(HELPER_BIN)], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


def _browser_url(bundle_id: str) -> tuple[str, bool]:
    app_name = "Google Chrome" if bundle_id == "com.google.Chrome" else "Microsoft Edge"
    script = (
        f'tell application "{app_name}"\n'
        'if (mode of front window) is not "normal" then return "PRIVATE"\n'
        'return URL of active tab of front window\n'
        'end tell'
    )
    result = subprocess.run(["/usr/bin/osascript", "-e", script],
                            capture_output=True, text=True, timeout=2)
    if result.returncode != 0:
        return "", True
    value = result.stdout.strip()
    return _safe_url(value), value == "PRIVATE"


def _watch():
    browser_retry_after = 0.0
    next_prune = 0.0
    while True:
        try:
            if time.monotonic() >= next_prune:
                next_prune = time.monotonic() + 3600
                if DATABASE.exists():
                    with _connect(DATABASE) as db:
                        db.execute("DELETE FROM activity WHERE last_seen < ?",
                                   (time.time() - RETENTION_DAYS * 86400,))
            if get_app_config_value("activity_history_enabled", True):
                snapshot = active_snapshot()
                app = str(snapshot.get("app", "")).strip()
                title = str(snapshot.get("title", "")).strip()
                bundle_id = str(snapshot.get("bundle_id", "")).strip()
                if app and not _is_sensitive(app, title):
                    url = ""
                    private = False
                    if bundle_id in _BROWSER_IDS and time.monotonic() >= browser_retry_after:
                        try:
                            url, private = _browser_url(bundle_id)
                            if not url:
                                browser_retry_after = time.monotonic() + 60
                        except (OSError, subprocess.TimeoutExpired):
                            private = True
                            browser_retry_after = time.monotonic() + 60
                    elif bundle_id in _BROWSER_IDS:
                        private = True
                    if not private:
                        record(app, bundle_id, title, url)
        except Exception:
            pass
        time.sleep(4)


def start_activity_watcher() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_watch, name="JARVIS activity history", daemon=True).start()


def _period_bounds(period: str, now: dt.datetime) -> tuple[float, float]:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    value = _fold(period or "recent").strip()
    if value in ("yesterday", "dun"):
        start = today - dt.timedelta(days=1)
        return start.timestamp(), today.timestamp()
    if value in ("today", "bugun"):
        return today.timestamp(), now.timestamp()
    if value in ("last_week", "gecen hafta"):
        start = today - dt.timedelta(days=today.weekday() + 7)
        return start.timestamp(), (start + dt.timedelta(days=7)).timestamp()
    if value in ("last_7_days", "son 7 gun"):
        return (now - dt.timedelta(days=7)).timestamp(), now.timestamp()
    return (now - dt.timedelta(hours=2)).timestamp(), now.timestamp()


def get_activity_history(query: str = "", period: str = "recent", kind: str = "all",
                         limit: int = 8, path: Path = DATABASE, now: dt.datetime | None = None) -> str:
    if not get_app_config_value("activity_history_enabled", True):
        return json.dumps({"status": "disabled", "entries": [],
                           "message": "Etkinlik geçmişi ayarlardan kapatılmış."}, ensure_ascii=False)
    now = now or dt.datetime.now()
    start, end = _period_bounds(period, now)
    try:
        with _connect(path) as db:
            rows = db.execute(
                "SELECT first_seen,last_seen,app,bundle_id,title,url FROM activity "
                "WHERE first_seen < ? AND last_seen >= ? ORDER BY last_seen DESC LIMIT 500",
                (end + 1, start),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return json.dumps({"status": "error", "message": "Etkinlik geçmişi okunamadı."}, ensure_ascii=False)
    words = [w for w in re.findall(r"\w+", _fold(query)) if w not in
             {"az", "once", "baktigim", "baktim", "dun", "uzerinde", "calistigim",
              "bul", "ac", "tekrar", "video", "videoyu", "site", "siteyi", "sunum",
              "sunumu", "icin", "olan", "en", "son", "bir"}]
    wanted = _fold(kind or "all")
    scored = []
    for first, last, app, bundle, title, url in rows:
        is_browser = bundle in _BROWSER_IDS or bundle == "com.apple.Safari"
        if wanted in ("website", "site", "video") and not is_browser:
            continue
        if wanted in ("presentation", "sunum") and not (
            any(name in _fold(app) for name in ("keynote", "powerpoint", "impress"))
            or any(ext in _fold(title) for ext in (".ppt", ".key"))
        ):
            continue
        if wanted == "video" and "youtube" not in _fold(url + " " + title):
            continue
        haystack = _fold(title + " " + url + " " + app)
        if words and not all(word in haystack for word in words):
            continue
        scored.append((last, {
            "first_seen": dt.datetime.fromtimestamp(first).isoformat(timespec="minutes"),
            "last_seen": dt.datetime.fromtimestamp(last).isoformat(timespec="minutes"),
            "app": app, "title": title, "url": url or None,
        }))
    if wanted in ("website", "site", "video"):
        scored.sort(key=lambda item: (bool(item[1]["url"]), item[0]), reverse=True)
    entries = [entry for _, entry in scored[:max(1, min(20, int(limit or 8)))]]
    return json.dumps({
        "status": "ok", "entries": entries, "count": len(scored),
        "note": ("Kayıt yok. İzleme yalnızca JARVIS açıkken ve etkinlik geçmişi ayarı açıkken çalışır."
                 if not entries else "URL yoksa bağlantı tahmin etme; başlıkla dosya arayabilir veya kullanıcıya sorabilirsin."),
    }, ensure_ascii=False)
