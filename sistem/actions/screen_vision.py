from __future__ import annotations

import io
import fcntl
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from google import genai
from google.genai import errors, types
from PIL import Image, ImageStat

from app_config import get_app_config_value


BASE_DIR = Path(__file__).resolve().parent.parent
SWIFT_CACHE_DIR = BASE_DIR / ".swift-cache"
HELPERS_DIR = BASE_DIR / "helpers"
HELPER_SOURCE = HELPERS_DIR / "jarvis_screen_helper.swift"
HELPER_PLIST = HELPERS_DIR / "jarvis_screen_helper.plist"
HELPER_APP = HELPERS_DIR / "JARVIS Screen Helper.app"
HELPER_CONTENTS_DIR = HELPER_APP / "Contents"
HELPER_MACOS_DIR = HELPER_CONTENTS_DIR / "MacOS"
HELPER_RESOURCES_DIR = HELPER_CONTENTS_DIR / "Resources"
HELPER_INFO_PLIST = HELPER_CONTENTS_DIR / "Info.plist"
HELPER_BIN = HELPER_MACOS_DIR / "jarvis-screen-helper"
COMPATIBLE_SDK = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk")
HELPER_BUNDLE_ID = "com.jarvis.screenhelper"
_BUILD_LOCK = threading.Lock()

VISION_MODELS = (
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
)
VISION_MAX_DIMENSION = 1800
VISION_MAX_INLINE_BYTES = 5_500_000


def _screen_permission_message() -> str:
    return (
        "Ekran analizi için macOS ekran kaydı izni gerekiyor. Çalışan yardımcı: JARVIS Screen Helper. "
        "Sistem Ayarları > Gizlilik ve Güvenlik > Ekran ve Sistem Sesi Kaydı bölümünde "
        "JARVIS Screen Helper iznini kontrol et. Açık görünmesine rağmen bu uyarı sürüyorsa "
        "aynı isimli eski kopyanın izni olabilir: bu kaydı kaldırıp + ile şu uygulamayı seç: "
        f"{HELPER_APP}. Ardından JARVIS'i yeniden başlat."
    )


def _ensure_helper_binary() -> tuple[bool, str]:
    # Keep one signed app at one explicit path. A linker-signed executable inside
    # an unsigned bundle does not bind Info.plist and fails strict validation.
    SWIFT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _BUILD_LOCK, (SWIFT_CACHE_DIR / "screen-helper.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare_helper_bundle()


def _prepare_helper_bundle() -> tuple[bool, str]:
    if not HELPER_SOURCE.exists():
        return False, "Screen helper kaynak dosyasi bulunamadi."
    if not HELPER_PLIST.exists():
        return False, "Screen helper plist dosyasi bulunamadi."

    try:
        digest = hashlib.sha256(HELPER_SOURCE.read_bytes() + b"\0" + HELPER_PLIST.read_bytes()).hexdigest()
        receipt = SWIFT_CACHE_DIR / "screen-helper-build.json"
        try:
            previous = json.loads(receipt.read_text()) if receipt.exists() else {}
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}
        if (previous.get("source_digest") == digest and HELPER_BIN.exists()
                and HELPER_INFO_PLIST.exists()
                and HELPER_INFO_PLIST.read_bytes() == HELPER_PLIST.read_bytes()):
            verified = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--strict", str(HELPER_APP)],
                capture_output=True, text=True, timeout=10)
            if verified.returncode == 0:
                return True, ""

        env = os.environ.copy()
        env["CLANG_MODULE_CACHE_PATH"] = str(SWIFT_CACHE_DIR)
        env["SWIFT_MODULE_CACHE_PATH"] = str(SWIFT_CACHE_DIR)
        swift_command = ["swiftc", "-module-cache-path", str(SWIFT_CACHE_DIR)]
        if COMPATIBLE_SDK.exists():
            swift_command.extend(["-sdk", str(COMPATIBLE_SDK)])
        # Build and validate off to the side; failed builds leave the installed
        # app intact. Unchanged sources never rebuild just because of timestamps.
        with tempfile.TemporaryDirectory(prefix=".screen-build-", dir=HELPERS_DIR) as temp:
            staging = Path(temp) / HELPER_APP.name
            contents = staging / "Contents"
            binary = contents / "MacOS" / HELPER_BIN.name
            binary.parent.mkdir(parents=True)
            (contents / "Resources").mkdir()
            (contents / "Info.plist").write_bytes(HELPER_PLIST.read_bytes())
            result = subprocess.run(
                [*swift_command, str(HELPER_SOURCE), "-o", str(binary)],
                capture_output=True, text=True, timeout=40, env=env)
            if result.returncode != 0:
                return False, (result.stderr or "Screen helper derlenemedi.").strip()
            binary.chmod(0o755)
            signed = subprocess.run(
                ["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", HELPER_BUNDLE_ID, str(staging)],
                capture_output=True, text=True, timeout=15)
            if signed.returncode != 0:
                return False, "Ekran yardımcısı imzalanamadı: " + (signed.stderr or "")[:500]
            verified = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--strict", str(staging)],
                capture_output=True, text=True, timeout=10)
            if verified.returncode != 0:
                return False, "Ekran yardımcısının uygulama imzası doğrulanamadı."
            old = Path(temp) / "previous.app"
            if HELPER_APP.exists():
                HELPER_APP.rename(old)
            try:
                staging.rename(HELPER_APP)
            except Exception:
                if old.exists():
                    old.rename(HELPER_APP)
                raise
            receipt.write_text(json.dumps({"source_digest": digest}))
    except FileNotFoundError:
        return False, "swiftc bulunamadi."
    except subprocess.TimeoutExpired:
        return False, "Screen helper derlenirken zaman asimina ugradi."
    except Exception as exc:
        return False, f"Screen helper derlenemedi: {exc}"

    return True, ""


def _run_helper(mode: str, timeout: int = 20) -> tuple[bool, str]:
    ok, detail = _ensure_helper_binary()
    if not ok:
        return False, detail

    output_path = None
    try:
        handle = tempfile.NamedTemporaryFile(prefix="jarvis-screen-helper-", suffix=".json", delete=False)
        output_path = Path(handle.name)
        handle.close()
        result = subprocess.run(
            ["/usr/bin/open", "-g", "-n", str(HELPER_APP), "--args", mode, str(output_path), str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=min(timeout, 8),
        )
        if result.returncode != 0:
            return False, "Screen helper açılamadı: " + (result.stderr or "")[:500]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = output_path.read_text(encoding="utf-8").strip()
            if raw:
                return True, raw
            time.sleep(.05)
        return False, "Screen helper yanıt vermedi. Açık macOS izin penceresini kontrol et."
    except subprocess.TimeoutExpired:
        return False, "Screen helper istegi zaman asimina ugradi."
    except Exception as exc:
        return False, f"Screen helper calistirilamadi: {exc}"

    finally:
        if output_path:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass


def _parse_capture_payload(raw: str) -> tuple[bool, str, dict | None]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False, "Gecersiz ekran helper yaniti alindi.", None

    if not isinstance(payload, dict):
        return False, "Ekran helper verisi beklenen formatta degil.", None

    if not payload.get("ok", False):
        detail = str(payload.get("detail") or payload.get("error") or "Ekran goruntusu alinamadi.")
        low = detail.lower()
        if "permission" in low or "not permitted" in low or "screen recording" in low:
            return False, _screen_permission_message(), None
        if str(payload.get("error") or "").lower() == "permission_denied":
            return False, _screen_permission_message(), None
        # Ortak önek: çağıranlar (ör. context_control) hatayı analiz sanmasın.
        if not detail.lower().startswith("ekran goruntusu alinamadi"):
            detail = f"Ekran goruntusu alinamadi: {detail}"
        return False, detail, None

    image_path = str(payload.get("image_path", "")).strip()
    if not image_path:
        return False, "Ekran goruntusu dosya yolu alinamadi.", None

    meta = {
        "image_path": image_path,
        "owner_name": str(payload.get("owner_name", "")).strip(),
        "window_title": str(payload.get("window_title", "")).strip(),
        "bounds": payload.get("bounds") or {},
        "detail": str(payload.get("detail", "")).strip(),
    }
    return True, "", meta


def _image_looks_blank(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as img:
            sample = img.convert("RGB")
            stat = ImageStat.Stat(sample)
            means = stat.mean
            extrema = stat.extrema
            max_seen = max(channel[1] for channel in extrema)
            mean_total = sum(means) / max(1, len(means))
            return max_seen <= 8 or mean_total <= 3
    except Exception:
        return False


def _build_image_part(image_path: Path) -> types.Part:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = "image/png"

    try:
        with Image.open(image_path) as img:
            work = img.copy()
        if work.mode not in {"RGB", "L"}:
            work = work.convert("RGB")

        if max(work.size) > VISION_MAX_DIMENSION:
            work.thumbnail((VISION_MAX_DIMENSION, VISION_MAX_DIMENSION), Image.Resampling.LANCZOS)

        png_buffer = io.BytesIO()
        work.save(png_buffer, format="PNG", optimize=True)
        png_bytes = png_buffer.getvalue()
        if len(png_bytes) <= VISION_MAX_INLINE_BYTES:
            return types.Part.from_bytes(data=png_bytes, mime_type="image/png")

        jpg_buffer = io.BytesIO()
        rgb = work.convert("RGB") if work.mode != "RGB" else work
        rgb.save(jpg_buffer, format="JPEG", quality=88, optimize=True)
        return types.Part.from_bytes(data=jpg_buffer.getvalue(), mime_type="image/jpeg")
    except Exception:
        return types.Part.from_bytes(
            data=image_path.read_bytes(),
            mime_type=mime_type,
        )


def _vision_prompt(query: str, owner_name: str, window_title: str) -> str:
    label = window_title or owner_name or "aktif pencere"
    user_query = (query or "Ekranda ne var?").strip()
    return (
        "Sen macOS uzerinde JARVIS icin ekran analizi yapan bir goruntu yorumlayicisisin.\n"
        "Asagidaki ekran goruntusu aktif pencereye ait.\n"
        f"Pencere baglami: {label}\n\n"
        "Gorevlerin:\n"
        "1. Pencerenin genel amacini 1-2 cumlede acikla.\n"
        "2. Gorunen onemli metinleri, hata mesajlarini, butonlari, basliklari ve durum etiketlerini oku.\n"
        "3. Kullanici sorusunu bu goruntuye gore dogrudan cevapla.\n"
        "4. Eger bir hata, uyari veya dikkat edilmesi gereken bir sey varsa bunu ayri ve net belirt.\n"
        "5. Uydurma yapma. Emin olmadigin kisimlarda bunu soyle.\n\n"
        f"Kullanici sorusu: {user_query}\n\n"
        "Yaniti Turkce ver. Gereksiz uzun olma, ama okunabilir detay ver."
    )


def _extract_response_text(response) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text

    candidates = getattr(response, "candidates", None) or []
    chunks: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = str(getattr(part, "text", "") or "").strip()
            if part_text:
                chunks.append(part_text)
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _is_transient_vision_error(exc: Exception) -> bool:
    if isinstance(exc, (errors.ServerError, TimeoutError)):
        return True

    message = str(exc or "").lower()
    transient_markers = (
        "503",
        "429",
        "deadline",
        "timed out",
        "timeout",
        "unavailable",
        "temporarily unavailable",
        "service unavailable",
        "internal error",
        "busy",
        "overloaded",
        "resource exhausted",
        "try again later",
        "backend error",
        "connection reset",
    )
    return any(marker in message for marker in transient_markers)


def _is_quota_vision_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    quota_markers = (
        "quota",
        "rate limit",
        "resource exhausted",
        "too many requests",
        "quota exceeded",
        "limit exceeded",
        "billing",
    )
    return any(marker in message for marker in quota_markers)


def _is_unavailable_vision_model(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 404 or any(marker in str(exc).lower() for marker in (
        "model not found", "not supported for generatecontent", "model is no longer available"))


def _friendly_vision_error(exc: Exception) -> str:
    if _is_quota_vision_error(exc):
        return "Gemini vision istegi kota veya hiz limitine takildi. Biraz bekleyip tekrar dene ya da API planini kontrol et."
    if _is_transient_vision_error(exc):
        return "Gemini vision servisi su anda yogun veya gecici olarak ulasilamiyor. Biraz sonra tekrar dene."
    return f"Gemini vision istegi basarisiz oldu: {exc}"


def _analyze_with_gemini(query: str, image_path: Path, owner_name: str, window_title: str) -> str:
    api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
    if not api_key:
        return "Gemini API anahtari eksik oldugu icin ekran analizi yapilamadi."

    prompt = _vision_prompt(query, owner_name, window_title)
    image_part = _build_image_part(image_path)
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30000))
    retry_delays = (0.9, 1.8, 3.0)
    last_error: Exception | None = None

    try:
        for model_name in VISION_MODELS:
            for attempt, delay in enumerate(retry_delays, start=1):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[
                            types.Part.from_text(text=prompt),
                            image_part,
                        ],
                        config=types.GenerateContentConfig(
                            temperature=0.2,
                        ),
                    )
                    merged = _extract_response_text(response)
                    if merged:
                        return merged
                    raise RuntimeError("Gemini gecerli bir ekran analizi metni dondurmedi.")
                except Exception as exc:
                    last_error = exc
                    if _is_unavailable_vision_model(exc):
                        break
                    if attempt < len(retry_delays) and _is_transient_vision_error(exc):
                        time.sleep(delay)
                        continue
                    if _is_transient_vision_error(exc):
                        break
                    raise RuntimeError(_friendly_vision_error(exc)) from exc

        assert last_error is not None
        raise RuntimeError(_friendly_vision_error(last_error))
    finally:
        client.close()


def _sweep_stale_captures(max_age: float = 600.0) -> None:
    """Zaman aşımında yardımcının sonradan yazdığı görüntü/yanıt dosyaları geçici klasörde birikmesin."""
    cutoff = time.time() - max_age
    try:
        folder = Path(tempfile.gettempdir())
        for pattern in ("jarvis-screen-*.png", "jarvis-screen-helper-*.json"):
            for leftover in folder.glob(pattern):
                try:
                    if leftover.is_file() and not leftover.is_symlink() and leftover.stat().st_mtime < cutoff:
                        leftover.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _front_app_pid() -> int | None:
    """Öndeki uygulamanın süreç numarası. JARVIS.app öndeyken macOS'un NSWorkspace'i -1 veriyor
    (yardımcı uygulama bu yüzden JARVIS'i tanıyamıyordu); lsappinfo doğru numarayı verir."""
    try:
        front = subprocess.run(["/usr/bin/lsappinfo", "front"], capture_output=True, text=True,
                               timeout=3).stdout.strip()
        if not front:
            return None
        info = subprocess.run(["/usr/bin/lsappinfo", "info", "-only", "pid", front],
                              capture_output=True, text=True, timeout=3).stdout
        match = re.search(r"pid\s*=\s*(\d+)", info)
        return int(match.group(1)) if match else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _activate_pid(pid: int) -> bool:
    """Uygulamayı öne getirir; tam ekran/başka masaüstü alanındaysa macOS o alana geçer.
    (JXA'da parametresiz ObjC metotları parantezsiz çağrılır.)"""
    script = (f"ObjC.import('AppKit'); var a = $.NSRunningApplication.runningApplicationWithProcessIdentifier({int(pid)}); "
              "if (a.isNil()) { 'yok' } else { a.unhide; a.activateWithOptions($.NSApplicationActivateIgnoringOtherApps); 'ok' }")
    try:
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                                capture_output=True, text=True, timeout=5)
        return result.returncode == 0 and "ok" in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _previous_app_pid() -> int | None:
    """JARVIS'ten önce kullanılan uygulama (macOS'un en son kullanılanlar sırasından)."""
    try:
        listing = subprocess.run(["/usr/bin/lsappinfo", "visibleProcessList"], capture_output=True,
                                 text=True, timeout=3).stdout
        for asn, _name in re.findall(r'(ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+)-"([^"]*)"', listing):
            info = subprocess.run(["/usr/bin/lsappinfo", "info", "-only", "pid", asn],
                                  capture_output=True, text=True, timeout=3).stdout
            match = re.search(r"pid\s*=\s*(\d+)", info)
            if match and int(match.group(1)) != os.getpid():
                return int(match.group(1))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def _capture(mode: str) -> tuple[bool, str]:
    """Bağlam penceresi istenirken JARVIS öndeyse (tam ekranda arkadaki pencere başka bir masaüstü
    alanında kalır, görüntülenemez) en son kullanılan uygulama bir anlığına öne alınır, onun penceresi
    çekilir ve JARVIS hemen geri getirilir."""
    stepped_aside = False
    if mode == "capture_context_window" and _front_app_pid() == os.getpid():
        target = _previous_app_pid()
        if target and _activate_pid(target):
            stepped_aside = True
            mode = "capture_active_window"
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and _front_app_pid() != target:
                time.sleep(0.1)
            time.sleep(0.8)   # masaüstü alanı geçiş animasyonu
    try:
        ok, raw = _run_helper(mode, timeout=20)
        # Geçiş sürerken pencere listesi boş gelebilir: kısa aralıklarla yeniden dene.
        tries = 0
        while stepped_aside and ok and "no_active_window" in raw and tries < 4:
            time.sleep(0.6)
            tries += 1
            ok, raw = _run_helper(mode, timeout=20)
        return ok, raw
    finally:
        if stepped_aside:
            _activate_pid(os.getpid())


def _is_own_window(owner_name: str, window_title: str) -> bool:
    letters = "".join(ch for ch in window_title.upper() if ch.isalpha())
    return owner_name in ("JARVIS", "Python") and letters == "JARVIS"


def analyze_screen(query: str, target: str = "context_window") -> str:
    _sweep_stale_captures()
    target = (target or "context_window").strip().lower()
    if target not in ("active_window", "context_window"):
        return "Ekran analizi yalnizca aktif veya JARVIS arkasindaki baglam penceresini destekliyor."

    ok, raw = _capture("capture_context_window" if target == "context_window" else "capture_active_window")
    if not ok:
        low = raw.lower()
        if "permission" in low or "screen recording" in low:
            return _screen_permission_message()
        return f"Ekran goruntusu alinamadi: {raw}"

    parsed_ok, detail, payload = _parse_capture_payload(raw)
    if not parsed_ok:
        if "aktif pencere bulunamadi" in detail.lower() or "okunabilecek bir pencere" in detail.lower():
            return ("Ekran goruntusu alinamadi: JARVIS'in arkasinda acik bir uygulama penceresi bulunamadi. "
                    "Bakmami istedigin uygulamayi ya da sayfayi acip tekrar sor.")
        return detail

    assert payload is not None
    image_path = Path(payload["image_path"])
    owner_name = str(payload.get("owner_name", "") or "").strip()
    window_title = str(payload.get("window_title", "") or "").strip()
    if target == "context_window" and _is_own_window(owner_name, window_title):
        # JARVIS'in kendi ekranı analiz edilmesin; kullanıcı arkadaki işi soruyor.
        try:
            image_path.unlink(missing_ok=True)
        except OSError:
            pass
        return ("Ekran goruntusu alinamadi: arkada yalnizca JARVIS'in kendi penceresi gorunuyor. "
                "Bakmami istedigin uygulamayi acip tekrar sor.")

    try:
        if not image_path.exists():
            return "Ekran goruntusu dosyasi bulunamadi. Tekrar dene."
        if image_path.stat().st_size <= 0:
            return (
                "Ekran goruntusu bos geldi. Bu genelde ekran kaydi izni olmadiginda "
                "veya korumali bir pencere acikken olur. " + _screen_permission_message()
            )
        if _image_looks_blank(image_path):
            return (
                "Ekran goruntusu siyah veya bos gorunuyor. Bu, ekran kaydi izni eksik oldugunda "
                "ya da korumali bir uygulama acikken olabilir. " + _screen_permission_message()
            )
        try:
            analysis = _analyze_with_gemini(query, image_path, owner_name, window_title)
        except Exception as exc:
            prefix = f"{owner_name} / {window_title}".strip(" /")
            if prefix:
                return (
                    f"Ekran goruntusu alindi ({prefix}) ama analiz tamamlanamadi: {exc}"
                )
            return f"Ekran goruntusu alindi ama analiz tamamlanamadi: {exc}"

        if owner_name or window_title:
            title = " / ".join(part for part in (owner_name, window_title) if part).strip()
            if title:
                return f"[Aktif pencere: {title}]\n{analysis}"
        return analysis
    finally:
        try:
            if image_path.exists():
                image_path.unlink()
        except Exception:
            pass
