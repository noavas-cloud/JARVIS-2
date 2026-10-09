"""El takibi bileşeninin (izole Python ortamı + MediaPipe + model) kontrolü ve kurulumu.

MediaPipe ana JARVIS ortamına KURULMAZ: kendi bağımlılıkları (opencv-contrib, numpy
sürümü vb.) mevcut paketlerle çakışıp var olan özellikleri bozmasın diye ayrı
bir ortamda (sistem/hand_env) tutulur. Kurulum yalnızca kullanıcı KUR'a bastığında
yapılır.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from brain import BASE_DIR, settings

REQUIREMENT = "mediapipe>=0.10.14,<0.11"
TRACKER_SCRIPT = BASE_DIR / "brain" / "hand_tracker.py"

EXPLAIN = {
    "missing_env": ("El takibi bileşeni kurulu değil. Pencerede ‘EL TAKİBİNİ KUR’ düğmesiyle bir kerelik "
                    "kurulabilir (~110 MB indirme, ~320 MB disk; internet gerekir). Fare ve klavye bu sırada çalışır."),
    "missing_dependency": ("El takibi ortamı var ama MediaPipe/OpenCV yüklenemedi. ‘EL TAKİBİNİ KUR’ ile "
                           "onarılabilir. Fare ve klavye çalışmaya devam eder."),
    "model_missing": "El modeli (hand_landmarker.task) bulunamadı. ‘EL TAKİBİNİ KUR’ modeli yeniden indirir.",
    "camera_denied": ("Kamera izni yok. Sistem Ayarları › Gizlilik ve Güvenlik › Kamera bölümünde JARVIS'i "
                      "başlatan uygulamaya (ör. Terminal) izin ver, sonra el kontrolünü yeniden aç."),
    "camera_restricted": "Bu Mac'te kamera erişimi kısıtlanmış (Ekran Süresi veya kurum yönetimi).",
    "camera_unavailable": ("Kamera açılamadı: izin verilmemiş, kamera bağlı değil ya da başka bir uygulama "
                           "kullanıyor olabilir. Kontrol edip el kontrolünü yeniden aç."),
    "camera_lost": "Kamera görüntüsü kesildi; el kontrolü durduruldu.",
    "camera_frozen": ("Mac'in kendi kamerasından gelen görüntü donuk. Kamerayı kullanan başka bir uygulamayı "
                      "kapatıp el kontrolünü yeniden aç. El takibi başka bir kameraya geçmez."),
    "builtin_missing": ("Bu Mac'in kendi kamerası bulunamadı. El takibi yalnızca Mac'in yerleşik kamerasını "
                        "kullanır; sanal kamera, iPhone veya harici kameralara bağlanmaz."),
    "internal": "El takibi beklenmedik biçimde durdu; ayrıntı günlükte. Fare/klavye çalışıyor.",
    "unsupported_python": "El takibi için Python 3.9–3.13 gerekir; uygun Python bulunamadı.",
}


def env_python() -> Path:
    return settings.HAND_ENV_DIR / "bin" / "python"


def check(timeout: float = 40.0) -> dict:
    """Gerçek içe aktarma denemesiyle durum: ready | missing_env | missing_dependency | model_missing."""
    if not settings.HAND_MODEL_PATH.is_file():
        return {"status": "model_missing", "message": EXPLAIN["model_missing"]}
    py = env_python()
    if not py.exists():
        return {"status": "missing_env", "message": EXPLAIN["missing_env"]}
    code = ("import json, mediapipe, cv2, numpy; print(json.dumps({'mediapipe': mediapipe.__version__, "
            "'cv2': cv2.__version__, 'numpy': numpy.__version__}))")
    try:
        out = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "missing_dependency", "message": EXPLAIN["missing_dependency"], "detail": str(exc)}
    if out.returncode != 0:
        tail = (out.stderr or "").strip().splitlines()[-1:] or [""]
        return {"status": "missing_dependency", "message": EXPLAIN["missing_dependency"], "detail": tail[0]}
    try:
        versions = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        versions = {}
    return {"status": "ready", "message": "El takibi hazır.", "versions": versions}


def _base_python() -> str | None:
    candidates = [getattr(sys, "_base_executable", "") or "", sys.executable]
    cfg = BASE_DIR / "venv" / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.startswith("executable"):
                candidates.insert(0, line.split("=", 1)[1].strip())
    except OSError:
        pass
    candidates += [shutil.which(n) or "" for n in ("python3.12", "python3.11", "python3.13", "python3.10", "python3")]
    for c in candidates:
        if not c or not os.path.exists(c):
            continue
        try:
            out = subprocess.run([c, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                                 capture_output=True, text=True, timeout=10)
            major, minor = map(int, out.stdout.split())
            if major == 3 and 9 <= minor <= 13:
                return c
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return None


def _run(cmd: list[str], log) -> bool:
    log("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except OSError as exc:
        log(f"Başlatılamadı: {exc}")
        return False
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log(line[-160:])
    return proc.wait() == 0


def download_model(log) -> bool:
    target = settings.HAND_MODEL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    log("El modeli indiriliyor (Google MediaPipe, Apache-2.0)…")
    try:
        with urllib.request.urlopen(settings.HAND_MODEL_URL, timeout=60) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        if tmp.stat().st_size < 1_000_000:
            raise ValueError("indirilen dosya beklenenden küçük")
        os.replace(tmp, target)
        return True
    except Exception as exc:
        log(f"Model indirilemedi: {exc}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def install(log=print) -> dict:
    """İzole ortamı kurar/onarır. Uzun sürer; arka plan iş parçacığında çağrılmalı."""
    log_file = settings.BASE_DIR / "memory" / "hand_setup.log" if hasattr(settings, "BASE_DIR") else None
    ui_log = log

    def log(line):  # noqa: F811 — hem arayüze hem kalıcı günlüğe yaz (sorun giderme için)
        try:
            if log_file is not None:
                with open(log_file, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError:
            pass
        ui_log(line)

    base = _base_python()
    log(f"Kurulum başlıyor · temel Python: {base}")
    if not base:
        return {"status": "error", "message": EXPLAIN["unsupported_python"]}
    env = settings.HAND_ENV_DIR
    if not env_python().exists():
        log("İzole Python ortamı oluşturuluyor…")
        if not _run([base, "-m", "venv", str(env)], log):
            return {"status": "error", "message": "Python ortamı oluşturulamadı."}
    py = str(env_python())
    _run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], log)
    log("MediaPipe kuruluyor (bir kaç dakika sürebilir)…")
    if not _run([py, "-m", "pip", "install", REQUIREMENT, "--progress-bar", "off"], log):
        return {"status": "error", "message": "MediaPipe kurulamadı. İnternet bağlantısını kontrol edip tekrar dene."}
    if not settings.HAND_MODEL_PATH.is_file() and not download_model(log):
        return {"status": "error", "message": "El modeli indirilemedi."}
    result = check()
    if result["status"] != "ready":
        return {"status": "error", "message": result["message"], "detail": result.get("detail", "")}
    log("Kurulum tamam: " + ", ".join(f"{k} {v}" for k, v in result.get("versions", {}).items()))
    return {"status": "ready", "message": "El takibi kuruldu."}


if __name__ == "__main__":
    if "--install" in sys.argv:
        print(json.dumps(install(), ensure_ascii=False))
    else:
        print(json.dumps(check(), ensure_ascii=False))
