#!/usr/bin/env python3
"""JARVIS el takip süreci (izole ortamda çalışır: sistem/hand_env).

Kameradan kare alır, MediaPipe HandLandmarker ile elleri YEREL olarak bulur ve
stdout'a satır başına bir JSON yazar. Kamera görüntüsü hiçbir yere gönderilmez,
diske yazılmaz; yalnızca 21 noktalık el iskeleti koordinatları çıkar.

stdin komutları (JSON satırı): {"cmd": "pause"|"resume"|"stop"}
stdin kapanırsa (ana pencere kapandı/çöktü) süreç kamerayı bırakıp hemen çıkar.

Hata kodları (type=error): missing_dependency, model_missing, camera_denied,
camera_restricted, camera_unavailable, camera_frozen, camera_lost, internal.

Kamera seçimi (--camera auto): sırayla kameralar denenir ve görüntüsü gerçekten DEĞİŞEN
ilk kamera seçilir. Sanal kameralar (ör. LinkMyMac) çoğu zaman hep aynı kareyi verir; bunlar
atlanır. Çalışırken görüntü 4 sn boyunca tıpatıp aynı kalırsa sıradaki kameraya geçilir.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

EXIT_OK, EXIT_DEP, EXIT_CAMERA, EXIT_MODEL, EXIT_INTERNAL = 0, 3, 4, 5, 6


def emit(obj: dict) -> None:
    try:
        sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        os._exit(0)


def camera_auth_status(timeout: float = 4.0):
    """macOS kamera izni: 0=sorulmadı, 1=kısıtlı, 2=reddedildi, 3=izinli, None=bilinmiyor."""
    if sys.platform != "darwin" or not os.path.exists("/usr/bin/osascript"):
        return None
    scripts = (
        # Yeni macOS sürümlerinde $.AVCaptureDevice köprüsü boş gelebiliyor; sınıfı adıyla yükle.
        ("ObjC.import('Foundation');"
         "$.NSBundle.bundleWithPath('/System/Library/Frameworks/AVFoundation.framework').load;"
         "$.NSClassFromString('AVCaptureDevice').authorizationStatusForMediaType('vide')"),
        ("ObjC.import('AVFoundation');"
         "$.AVCaptureDevice.authorizationStatusForMediaType($.AVMediaTypeVideo)"),
    )
    for script in scripts:
        try:
            out = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                                 capture_output=True, text=True, timeout=timeout)
            value = out.stdout.strip()
            if value.isdigit():
                return int(value)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    return None


# Mac'in kendi (yerleşik) kamerasını tanıma. OpenCV'nin AVFoundation arka ucu kameraları
# video (+muxed) cihaz listesini uniqueID'ye göre sıralayarak numaralar; aynı sırayı burada kurup
# yerleşik kameranın numarasını buluruz. Sanal kameralar, iPhone/Süreklilik kamerası ve harici
# kameralar asla seçilmez.
BUILTIN_HINT = re.compile(r"macbook|imac|mac mini|mac studio|facetime|built-?in|yerleşik", re.I)
NOT_BUILTIN = re.compile(r"virtual|sanal|iphone|ipad|continuity|süreklilik|desk view|masa görünümü|obs|camo|"
                         r"linkmymac|snap ?camera|zoom|teams|ndi|droidcam|epoccam|mmhmm|elgato|display|"
                         r"ekran|usb|logitech|webcam", re.I)


def list_cameras(timeout: float = 12.0) -> list:
    """[{index, name, model, uid}] — index, OpenCV'nin kamera numarasıdır."""
    fake = os.environ.get("JARVIS_FAKE_CAMERA_LIST")
    if fake is not None:
        raw = json.loads(fake)
    else:
        try:
            out = subprocess.run(["/usr/sbin/system_profiler", "SPCameraDataType", "-json"],
                                 capture_output=True, text=True, timeout=timeout)
            raw = json.loads(out.stdout or "{}").get("SPCameraDataType", [])
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raw = []
    cams = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("spcamera_unique-id") or item.get("uid") or "")
        if not uid:
            continue
        cams.append({"name": str(item.get("_name") or item.get("name") or ""),
                     "model": str(item.get("spcamera_model-id") or item.get("model") or ""), "uid": uid})
    cams.sort(key=lambda c: c["uid"])   # OpenCV: [d1.uniqueID compare:d2.uniqueID]
    for i, c in enumerate(cams):
        c["index"] = i
    return cams


def find_builtin(cams: list, pinned_uid: str = ""):
    if pinned_uid:
        for c in cams:
            if c["uid"] == pinned_uid and not NOT_BUILTIN.search(f"{c['name']} {c['model']}"):
                return c
    cands = []
    for c in cams:
        text = f"{c['name']} {c['model']}"
        if BUILTIN_HINT.search(text) and not NOT_BUILTIN.search(text):
            cands.append(c)
    apple = [c for c in cands if "-05AC-" in c["uid"].upper() or "05AC" in c["uid"].upper()]
    cands = apple or cands
    return cands[0] if cands else None


class Control:
    def __init__(self):
        self.stop = threading.Event()
        self.paused = threading.Event()

    def watch_stdin(self):
        def run():
            try:
                for line in sys.stdin:
                    try:
                        cmd = json.loads(line).get("cmd")
                    except (ValueError, AttributeError):
                        continue
                    if cmd == "stop":
                        break
                    if cmd == "pause":
                        self.paused.set()
                    elif cmd == "resume":
                        self.paused.clear()
            except (OSError, ValueError):
                pass
            self.stop.set()  # EOF: ebeveyn gitti → kamerayı bırak
        threading.Thread(target=run, daemon=True).start()


class ImageSource:
    """Test kaynağı: görüntü dosyalarını kamera gibi döndürür (gerçek kamera açılmaz)."""

    def __init__(self, pattern: str, cv2, loops: int = 1):
        self.cv2 = cv2
        self.files = sorted(glob.glob(pattern))
        self.frames = [cv2.imread(f) for f in self.files]
        self.frames = [f for f in self.frames if f is not None] * max(1, loops)
        self.i = 0

    def isOpened(self):
        return bool(self.frames)

    def read(self):
        if self.i >= len(self.frames):
            return False, None
        frame = self.frames[self.i]
        self.i += 1
        return True, frame.copy()

    def release(self):
        self.frames = []


class FakeCamera:
    """Yalnızca testler için (JARVIS_FAKE_CAMERAS="static,live,freeze:40"): gerçek kamera açılmaz.
    static = hep aynı kare (sanal kamera gibi), live = her kare farklı, freeze:N = N kareden sonra donar."""

    def __init__(self, kind: str, index: int):
        import numpy as np
        log = os.environ.get("JARVIS_FAKE_CAMERA_LOG")
        if log and kind != "none":
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"{index}\n")
        self.np, self.kind, self.index, self.n = np, kind, index, 0
        self.rng = np.random.default_rng(index + 1)
        self.still = self.rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)

    def isOpened(self):
        return self.kind != "none"

    def set(self, *a):
        return False

    def read(self):
        self.n += 1
        limit = int(self.kind.split(":")[1]) if self.kind.startswith("freeze:") else None
        if self.kind == "static" or (limit is not None and self.n > limit):
            return True, self.still.copy()
        return True, self.rng.integers(0, 255, (480, 640, 3), dtype=self.np.uint8)

    def release(self):
        pass


def open_camera(cv2, index: int, width: int, height: int):
    fake = os.environ.get("JARVIS_FAKE_CAMERAS")
    if fake is not None:
        kinds = [k.strip() for k in fake.split(",") if k.strip()]
        return FakeCamera(kinds[index] if index < len(kinds) else "none", index)
    backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if cap is not None and cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
    return cap


MAX_CAMERAS = 5
FROZEN_SECONDS = 4.0


def frame_sig(frame):
    """Karenin ham piksellerinden seyrek örnek (en yakın komşu). Gerçek kamerada sensör gürültüsü
    yüzünden art arda iki kare asla tıpatıp aynı olmaz; donuk/sanal kamerada hep aynıdır."""
    h, w = frame.shape[:2]
    return frame[:: max(1, h // 90), :: max(1, w // 160)].copy()


def probe_live(cap, np, stop, max_seconds: float = 2.5, need: int = 2):
    """Kamera canlı mı? (canlı, okunan kare, boyut)"""
    prev, changes, got, size = None, 0, 0, None
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_seconds and not stop.is_set():
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.05)
            continue
        got += 1
        size = f"{frame.shape[1]}x{frame.shape[0]}"
        sig = frame_sig(frame)
        if prev is not None and (sig.shape != prev.shape or not np.array_equal(sig, prev)):
            changes += 1
            if changes >= need:
                return True, got, size
        prev = sig
        time.sleep(0.04)
    return False, got, size


def parse_camera(value: str):
    """builtin (varsayılan: yalnızca Mac'in kendi kamerası) | auto | kamera sırası."""
    value = (value or "builtin").strip().lower()
    if value.isdigit() and int(value) < 10:
        return int(value)
    return "auto" if value == "auto" else "builtin"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--camera", default="builtin",
                    help="builtin (yalnızca Mac'in kendi kamerası) | auto | kamera sırası (0, 1, …)")
    ap.add_argument("--camera-uid", default="", help="builtin: sabitlenmiş yerleşik kamera kimliği")
    ap.add_argument("--prefer", type=int, default=-1, help="auto: önce bu kamerayı dene (son çalışan)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--idle-fps", type=float, default=6.0)
    ap.add_argument("--source", default="", help="test: images:<glob>")
    ap.add_argument("--loops", type=int, default=1)
    ap.add_argument("--max-seconds", type=float, default=0.0)
    ap.add_argument("--no-stdin", action="store_true")
    ap.add_argument("--open-timeout", type=float, default=0.0)
    ap.add_argument("--pace", action="store_true", help="test kaynağında da kamera gibi FPS'e göre bekle")
    args = ap.parse_args(argv)

    try:
        import cv2
        import mediapipe as mp
        import numpy as np
        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision
    except Exception as exc:
        emit({"type": "error", "code": "missing_dependency",
              "message": f"El takibi bileşeni eksik: {type(exc).__name__}: {exc}"})
        return EXIT_DEP
    if not os.path.isfile(args.model):
        emit({"type": "error", "code": "model_missing", "message": f"Model dosyası yok: {args.model}"})
        return EXIT_MODEL

    control = Control()
    if not args.no_stdin:
        control.watch_stdin()

    test_mode = args.source.startswith("images:")
    status = None
    if not test_mode:
        status = camera_auth_status()
        if status == 2:
            emit({"type": "error", "code": "camera_denied",
                  "message": "macOS kamera izni reddedilmiş."})
            return EXIT_CAMERA
        if status == 1:
            emit({"type": "error", "code": "camera_restricted",
                  "message": "Kamera erişimi bu Mac'te kısıtlanmış (ekran süresi/MDM)."})
            return EXIT_CAMERA
        if status == 0:
            emit({"type": "status", "state": "permission_prompt",
                  "message": "macOS kamera izni soruyor; izin verince el takibi başlar."})

    options = vision.HandLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=args.model),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        # 04.10: 0,6 → 0,5 (MediaPipe varsayılanı): ikinci el (yan dönük/kenarda) daha kolay yakalansın
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    try:
        landmarker = vision.HandLandmarker.create_from_options(options)
    except Exception as exc:
        emit({"type": "error", "code": "internal", "message": f"Model yüklenemedi: {exc}"})
        return EXIT_INTERNAL

    cap = None
    exit_code = EXIT_OK
    try:
        camera = parse_camera(args.camera)
        auto = camera == "auto"
        builtin = camera == "builtin"
        state = {"prefer": args.prefer, "index": -1, "size": None, "cameras": [], "frozen_only": False,
                 "builtin": None}
        if builtin and not test_mode:
            cams = list_cameras()
            state["cameras"] = cams
            found = find_builtin(cams, args.camera_uid)
            if found is None:
                emit({"type": "error", "code": "builtin_missing", "cameras": cams,
                      "message": "Bu Mac'in kendi kamerası bulunamadı; başka kameraya bağlanılmadı."})
                return EXIT_CAMERA
            state["builtin"] = found
            camera = found["index"]

        def acquire(exclude=frozenset()):
            """Kamerayı aç. auto: görüntüsü değişen ilk kamerayı seç (donuk/sanal olanları atla)."""
            state["frozen_only"] = False
            if test_mode:
                return ImageSource(args.source.split(":", 1)[1], cv2, args.loops)
            wait = args.open_timeout or (30.0 if status in (0, None) else 6.0)
            deadline = time.monotonic() + wait
            if builtin:
                # Yalnızca Mac'in kendi kamerası: aç, görüntünün canlı olduğunu doğrula; başka kameraya geçme.
                passes = 0
                while not control.stop.is_set():
                    c = open_camera(cv2, camera, args.width, args.height)
                    if c is not None and c.isOpened():
                        passes += 1
                        live, got, size = probe_live(c, np, control.stop, max_seconds=4.0)
                        if live:
                            state.update(index=camera, size=size)
                            return c
                        c.release()
                        if got and (passes >= 2 or time.monotonic() > deadline):
                            state["frozen_only"] = True   # kamera açılıyor ama görüntüsü değişmiyor
                            return None
                    elif c is not None:
                        c.release()
                    if time.monotonic() > deadline:
                        return None
                    time.sleep(1.0)
                return None
            if not auto:
                while not control.stop.is_set():
                    c = open_camera(cv2, camera, args.width, args.height)
                    if c is not None and c.isOpened():
                        ok, first = c.read()
                        if ok and first is not None:
                            state.update(index=camera, size=f"{first.shape[1]}x{first.shape[0]}")
                            return c
                        c.release()
                    if time.monotonic() > deadline:
                        return None
                    time.sleep(1.0)
                return None
            prefer = state["prefer"]
            order = ([prefer] if 0 <= prefer < MAX_CAMERAS else []) + [i for i in range(MAX_CAMERAS) if i != prefer]
            order = [i for i in order if i not in exclude]
            passes = 0
            while not control.stop.is_set():
                passes += 1
                report, opened = [], 0
                for idx in order:
                    if control.stop.is_set():
                        return None
                    c = open_camera(cv2, idx, args.width, args.height)
                    if c is None or not c.isOpened():
                        if c is not None:
                            c.release()
                        report.append({"index": idx, "opened": False})
                        continue
                    opened += 1
                    live, got, size = probe_live(c, np, control.stop)
                    report.append({"index": idx, "opened": True, "live": live, "frames": got, "size": size})
                    if live:
                        state.update(index=idx, size=size, cameras=report, prefer=idx)
                        emit({"type": "status", "state": "camera_probe", "chosen": idx, "cameras": report})
                        return c
                    c.release()
                state["cameras"] = report
                emit({"type": "status", "state": "camera_probe", "chosen": None, "cameras": report})
                if opened and (passes >= 2 or time.monotonic() > deadline):
                    state["frozen_only"] = True   # kamera açılıyor ama hiçbirinin görüntüsü değişmiyor
                    return None
                if time.monotonic() > deadline:
                    return None
                time.sleep(1.0)
            return None

        def fail_no_camera():
            if state["frozen_only"]:
                emit({"type": "error", "code": "camera_frozen", "cameras": state["cameras"],
                      "message": "Kameralar açılıyor ama görüntüleri donuk (sanal kamera olabilir)."})
                return EXIT_CAMERA
            again = camera_auth_status() if not test_mode else None
            code = "camera_denied" if again == 2 else "camera_unavailable"
            emit({"type": "error", "code": code,
                  "message": ("Kamera açılamadı: izin verilmemiş olabilir ya da kamera başka bir "
                              "uygulama tarafından kilitli/bağlı değil.")})
            return EXIT_CAMERA

        cap = acquire()
        if cap is None or not cap.isOpened():
            if control.stop.is_set():
                return EXIT_OK
            return fail_no_camera()

        emit({"type": "ready", "backend": "mediapipe", "version": getattr(mp, "__version__", "?"),
              "camera": state["index"], "size": state["size"], "auto": auto, "builtin": state["builtin"],
              "cameras": state["cameras"], "test": test_mode})
        bad_cameras: set = set()
        last_sig = None
        same_since = None
        t_start = time.monotonic()
        last_ts = -1
        last_hand = time.monotonic()
        frames = 0
        cpu0, wall0 = time.process_time(), time.monotonic()
        proc_ms = 0.0
        brightness = -1.0     # ortalama parlaklık (0-255); arayüzde "ortam karanlık" uyarısı için
        read_fail = 0
        while not control.stop.is_set():
            if args.max_seconds and time.monotonic() - t_start > args.max_seconds:
                break
            if control.paused.is_set():
                if not test_mode and cap is not None:
                    cap.release()  # duraklatınca kamera ışığı söner
                    cap = None
                    emit({"type": "status", "state": "paused"})
                while control.paused.is_set() and not control.stop.is_set():
                    time.sleep(0.1)
                if control.stop.is_set():
                    break
                if cap is None:
                    cap = acquire(bad_cameras)
                    if cap is None:
                        if control.stop.is_set():
                            break
                        return fail_no_camera()
                    last_sig, same_since = None, None
                    emit({"type": "status", "state": "resumed", "camera": state["index"]})
            loop_start = time.monotonic()
            ok, frame = cap.read()
            if not ok or frame is None:
                if test_mode:
                    break
                read_fail += 1
                if read_fail > 30:
                    emit({"type": "error", "code": "camera_lost", "message": "Kamera görüntüsü kesildi."})
                    return EXIT_CAMERA
                time.sleep(0.05)
                continue
            read_fail = 0
            if not test_mode:
                # Donuk görüntü denetimi: gerçek kamerada kareler asla tıpatıp aynı kalmaz.
                sig = frame_sig(frame)
                if last_sig is not None and sig.shape == last_sig.shape and np.array_equal(sig, last_sig):
                    same_since = same_since or loop_start
                else:
                    same_since = None
                last_sig = sig
                if same_since is not None and loop_start - same_since > FROZEN_SECONDS:
                    frozen = state["index"]
                    cap.release()
                    cap = None
                    last_sig, same_since = None, None
                    if builtin:
                        # Aynı (yerleşik) kamerayı bir kez yeniden aç; yine donuksa dur. Başka kameraya geçilmez.
                        emit({"type": "status", "state": "frozen", "camera": frozen,
                              "message": "Mac kamerasının görüntüsü dondu; kamera yeniden açılıyor…"})
                        cap = acquire()
                        if cap is None:
                            if control.stop.is_set():
                                break
                            return fail_no_camera()
                        emit({"type": "status", "state": "resumed", "camera": state["index"]})
                        continue
                    if not auto:
                        emit({"type": "error", "code": "camera_frozen", "camera": frozen,
                              "message": f"Kamera {frozen} görüntüsü donuk."})
                        return EXIT_CAMERA
                    bad_cameras.add(frozen)
                    emit({"type": "status", "state": "frozen", "camera": frozen,
                          "message": f"Kamera {frozen} görüntüsü donuk; başka kamera deneniyor…"})
                    cap = acquire(bad_cameras)
                    if cap is None:
                        if control.stop.is_set():
                            break
                        return fail_no_camera()
                    emit({"type": "status", "state": "camera_switched", "camera": state["index"],
                          "size": state["size"]})
                    continue
            h, w = frame.shape[:2]
            if w > args.width * 1.2:
                scale = args.width / float(w)
                frame = cv2.resize(frame, (args.width, int(h * scale)), interpolation=cv2.INTER_AREA)
                h, w = frame.shape[:2]
            t_cap = time.monotonic()    # kare zamanı (hız hesabı işleme süresinden etkilenmesin)
            if frames % 5 == 0:
                small = cv2.resize(frame, (16, 12), interpolation=cv2.INTER_AREA)
                value = float(small.mean())
                brightness = value if brightness < 0 else 0.7 * brightness + 0.3 * value
            frame = cv2.flip(frame, 1)  # ayna: kullanıcı ekranda kendi yönünü görür
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts = int((time.monotonic() - t_start) * 1000)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts
            t0 = time.perf_counter()
            result = landmarker.detect_for_video(image, ts)
            proc_ms = 0.8 * proc_ms + 0.2 * (time.perf_counter() - t0) * 1000
            hands = []
            for k, lms in enumerate(result.hand_landmarks or []):
                score = 1.0
                label = ""
                if result.handedness and k < len(result.handedness) and result.handedness[k]:
                    score = float(result.handedness[k][0].score)
                    label = result.handedness[k][0].category_name
                hands.append({"score": round(score, 3), "label": label,
                              "lm": [[round(p.x, 4), round(p.y, 4), round(p.z, 4)] for p in lms]})
            now = time.monotonic()
            if hands:
                last_hand = now
            emit({"type": "hands", "t": round(t_cap, 4), "w": w, "h": h, "hands": hands})
            frames += 1
            if now - wall0 >= 2.0:
                cpu = (time.process_time() - cpu0) / (now - wall0) * 100
                emit({"type": "stats", "fps": round(frames / (now - wall0), 1),
                      "proc_ms": round(proc_ms, 1), "cpu_percent": round(cpu, 1),
                      "idle": now - last_hand > 2.0, "brightness": round(brightness, 1)})
                frames, cpu0, wall0 = 0, time.process_time(), now
            # El yoksa düşük hız: pil ve işlemci tasarrufu.
            target = args.fps if (now - last_hand) <= 2.0 else args.idle_fps
            if not test_mode or args.pace:
                spare = 1.0 / max(1.0, target) - (time.monotonic() - loop_start)
                if spare > 0:
                    control.stop.wait(spare)
        emit({"type": "stopped"})
    except Exception as exc:  # beklenmeyen hata: açıklama gönder, çökme
        emit({"type": "error", "code": "internal", "message": f"{type(exc).__name__}: {exc}"})
        exit_code = EXIT_INTERNAL
    finally:
        if cap is not None:
            cap.release()
        try:
            landmarker.close()
        except Exception:
            pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
