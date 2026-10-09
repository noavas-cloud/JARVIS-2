"""Bilgisayar açılınca (oturum açılınca) JARVIS 2'yi başlatma. Varsayılan kapalı; Ayarlar › UYGULAMA'dan açılır.

~/Library/LaunchAgents/com.kemal.jarvis2.autostart.plist yazılır: oturum açılınca bir kez `open "JARVIS 2.app"` çalışır
(uygulama zaten açıksa öne gelir). launchctl ile hemen yüklenmez — yüklenirse JARVIS 2 o an ikinci kez açılmaya
çalışırdı; kayıt bir sonraki oturum açılışında geçerli olur. Asıl JARVIS'in kayıtlarına dokunulmaz (ayrı ad).
"""

from __future__ import annotations

import plistlib
from pathlib import Path

from jarvis.paths import BUNDLE_ID, LOG_DIR

LABEL = BUNDLE_ID + ".autostart"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def plist_path(agents_dir: Path | None = None) -> Path:
    return Path(agents_dir or AGENTS_DIR) / f"{LABEL}.plist"


def _app() -> Path:
    from jarvis.app_bundle import app_path
    return app_path()


def contents(app: Path | None = None) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/open", str(app or _app())],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "StandardErrorPath": str(LOG_DIR / "autostart.log"),
    }


def is_enabled(agents_dir: Path | None = None) -> bool:
    return plist_path(agents_dir).is_file()


def problem(app: Path | None = None) -> str:
    """Açılabilir mi? Boş metin = sorun yok."""
    app = app or _app()
    if not (app / "Contents" / "Info.plist").is_file():
        return "JARVIS 2.app bulunamadı; önce Ayarlar › UYGULAMA › Masaüstü uygulamasını kur."
    return ""


def enable(agents_dir: Path | None = None, app: Path | None = None) -> str:
    err = problem(app)
    if err:
        raise RuntimeError(err)
    path = plist_path(agents_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        plistlib.dump(contents(app), fh)
    tmp.replace(path)
    return str(path)


def disable(agents_dir: Path | None = None) -> bool:
    path = plist_path(agents_dir)
    if path.is_file():
        path.unlink()
        return True
    return False
