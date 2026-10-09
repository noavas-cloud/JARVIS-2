"""JARVIS 2.app'i kurar: /Applications/JARVIS 2.app (Launchpad/Spotlight'ta görünür) + masaüstünde ve JARVIS 2
klasöründe kısayol. Asıl /Applications/JARVIS.app'e ve masaüstündeki JARVIS.app kısayoluna hiç dokunmaz.

03.10 akşamına kadar paket JARVIS 2 klasöründeydi: ilk kurulumda oradan Applications'a TAŞINIR (yeniden üretilmez,
imza aynı kalır → verilmiş macOS izinleri korunur).

Paketin içindeki başlatıcı, Python'un uygulama ikili dosyasının kopyasını (jarvis-python) çalıştırır: Dock'ta ve
menüde "JARVIS 2" yazar, Terminal açılmaz, macOS izinleri (mikrofon, kamera…) "JARVIS 2" adına verilir.
İçerik değişmediyse yeniden imzalanmaz (ad-hoc imza değişirse macOS izinleri yeniden sorar).

Komut satırı:  sistem/venv/bin/python -m jarvis.app_bundle
"""

from __future__ import annotations

import hashlib
import plistlib
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from jarvis import VERSION
from jarvis.paths import APP_NAME, BASE_DIR, BUNDLE_ID, ICON_DIR, LOG_DIR, ROOT_DIR

EXE = "JARVIS2"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
              "/Support/lsregister")
FINGERPRINT = "jarvis2_build.sha256"


APPLICATIONS = Path("/Applications")
LEGACY_PATH = ROOT_DIR / f"{APP_NAME}.app"                     # eski yer; artık kısayol
DESKTOP_LINK = Path.home() / "Desktop" / f"{APP_NAME}.app"


def app_path() -> Path:
    return APPLICATIONS / f"{APP_NAME}.app"


def _migrate_legacy(dest: Path) -> bool:
    """Eski yerdeki gerçek paketi (kısayol değilse) yeni yere taşır."""
    if LEGACY_PATH.is_dir() and not LEGACY_PATH.is_symlink() and not dest.exists():
        shutil.move(str(LEGACY_PATH), str(dest))
        return True
    return False


def _link(link: Path, dest: Path) -> None:
    """Kısayol yoksa oluşturur; yalnız kendi kısayolumuzu günceller (gerçek dosya/klasörün üstüne yazmaz)."""
    if link.is_symlink():
        if link.resolve() == dest.resolve():
            return
        link.unlink()
    elif link.exists():
        return
    link.symlink_to(dest)


def install_links(dest: Path | None = None) -> None:
    dest = dest or app_path()
    for link in (LEGACY_PATH, DESKTOP_LINK):
        try:
            _link(link, dest)
        except OSError as exc:
            print(f"[UYGULAMA] Kısayol oluşturulamadı ({link}): {exc}", flush=True)


def _python_app_binary() -> Path | None:
    real = (BASE_DIR / "venv" / "bin" / "python").resolve()
    candidate = real.parent.parent / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    return candidate if candidate.exists() else None


def _info_plist() -> dict:
    return {
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": EXE, "CFBundleIconFile": "JARVIS2", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION, "CFBundleVersion": "1", "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": "12.0", "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "JARVIS 2 seni duyabilmek için mikrofonu kullanır.",
        "NSCameraUsageDescription": "JARVIS 2 kamera görüntüsü ve İkinci Beyin el kontrolü için kamerayı kullanır.",
        "NSAppleEventsUsageDescription": "JARVIS 2 istediğinde diğer uygulamaları (WhatsApp, Müzik, Anımsatıcılar…) kontrol eder.",
        "NSRemindersUsageDescription": "JARVIS 2 istediğinde anımsatıcı ekler ve okur.",
        "NSCalendarsUsageDescription": "JARVIS 2 istediğinde takvimini okur ve etkinlik ekler.",
        "NSContactsUsageDescription": "JARVIS 2 mesaj ve arama için rehberdeki kişiyi bulur.",
        "NSDesktopFolderUsageDescription": "JARVIS 2'nin kendi dosyaları Masaüstü klasöründe durur.",
        "NSDocumentsFolderUsageDescription": "JARVIS 2 istediğinde Belgeler klasöründeki dosyalarla çalışır.",
        "NSDownloadsFolderUsageDescription": "JARVIS 2 istediğinde İndirilenler klasöründeki dosyalarla çalışır.",
    }


def _launcher() -> str:
    app_dir = shlex.quote(str(BASE_DIR))
    log_dir = shlex.quote(str(LOG_DIR))
    return f"""#!/bin/bash
# JARVIS 2.app başlatıcısı (sistem/jarvis/app_bundle.py üretir; elle düzenleme).
APP_DIR={app_dir}
VENV_PY="$APP_DIR/venv/bin/python"
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR={log_dir}
LOG="$LOG_DIR/jarvis2.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1
/bin/mkdir -p "$LOG_DIR"
alert() {{
  /usr/bin/osascript -e "display alert \\"JARVIS 2 açılamadı\\" message \\"$1\\" as critical" >/dev/null 2>&1
}}
cd "$APP_DIR" 2>/dev/null || {{ alert "JARVIS 2 klasörü bulunamadı: $APP_DIR"; exit 1; }}
[ -x "$VENV_PY" ] || {{ alert "Python ortamı bulunamadı: $VENV_PY"; exit 1; }}
[ -f "$LOG" ] && /bin/mv -f "$LOG" "$LOG.1"
echo "=== JARVIS 2 $(date '+%Y-%m-%d %H:%M:%S') ===" >>"$LOG"
export __PYVENV_LAUNCHER__="$VENV_PY"
if [ -x "$HERE/jarvis-python" ] && "$HERE/jarvis-python" -c "import encodings" >/dev/null 2>&1; then
  exec "$HERE/jarvis-python" -u -m jarvis.app >>"$LOG" 2>&1
fi
unset __PYVENV_LAUNCHER__
echo "Uyarı: uygulama içi Python çalışmadı; venv Python'u kullanılıyor." >>"$LOG"
exec "$VENV_PY" -u -m jarvis.app >>"$LOG" 2>&1
"""


def _icns(dest: Path):
    from PIL import Image
    # 04.10: kullanıcının HUD fotoğrafından yuvarlak köşeli simge (Apple ızgarası: 1024 tuval, 824 içerik)
    src = next((ICON_DIR / n for n in ("jarvis2_icon.png", "jarvis_logo_app.png", "jarvis_logo.png")
                if (ICON_DIR / n).exists()), ICON_DIR / "jarvis_logo.png")
    with Image.open(src) as img:
        big = img.convert("RGBA").resize((1024, 1024), Image.LANCZOS)
    # macOS'un kendi aracıyla tüm boylar (16…1024); Pillow'un ICNS'inde 16 ve 32'lik boylar eksik kalıyordu.
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "JARVIS2.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            big.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            big.resize((2 * s, 2 * s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        res = subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(dest / "JARVIS2.icns")],
                             capture_output=True)
        if res.returncode != 0:
            big.save(dest / "JARVIS2.icns", format="ICNS")


def _build(target: Path):
    contents = target / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    with open(contents / "Info.plist", "wb") as fh:
        plistlib.dump(_info_plist(), fh)
    launcher = contents / "MacOS" / EXE
    launcher.write_text(_launcher(), encoding="utf-8")
    launcher.chmod(0o755)
    py = _python_app_binary()
    if py is not None:
        shutil.copy2(py, contents / "MacOS" / "jarvis-python")
    _icns(contents / "Resources")


def _fingerprint(app: Path) -> str:
    digest = hashlib.sha256()
    for name in ("Contents/Info.plist", f"Contents/MacOS/{EXE}", "Contents/MacOS/jarvis-python",
                 "Contents/Resources/JARVIS2.icns"):
        p = app / name
        digest.update(name.encode() + b"\0" + (p.read_bytes() if p.exists() else b"-") + b"\0")
    return digest.hexdigest()


def create_app() -> Path:
    dest = app_path()
    _migrate_legacy(dest)
    _build_or_keep(dest)
    install_links(dest)
    return dest


def _build_or_keep(dest: Path) -> None:
    with tempfile.TemporaryDirectory(prefix=".jarvis2-app-") as tmp:
        built = Path(tmp) / dest.name
        _build(built)
        fp = _fingerprint(built)
        stamp = dest / "Contents" / "Resources" / FINGERPRINT
        if stamp.exists() and stamp.read_text(encoding="ascii").strip() == fp \
                and (dest / "Contents" / "_CodeSignature").exists():
            subprocess.run([LSREGISTER, "-f", str(dest)], capture_output=True)
            return
        (built / "Contents" / "Resources" / FINGERPRINT).write_text(fp + "\n", encoding="ascii")
        res = subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(built)],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("Uygulama imzalanamadı: " + (res.stderr.strip() or "codesign hatası"))
        old = Path(tmp) / "old.app"
        if dest.exists():
            shutil.move(str(dest), str(old))
        try:
            shutil.move(str(built), str(dest))
        except Exception:
            if old.exists() and not dest.exists():
                shutil.move(str(old), str(dest))
            raise
    subprocess.run([LSREGISTER, "-f", str(dest)], capture_output=True)


if __name__ == "__main__":
    print(f"Hazır: {create_app()}")
