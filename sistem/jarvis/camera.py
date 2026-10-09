"""Kamera akışı: en güncel kareyi bellekte tutar (asıl JARVIS main.py WebcamStreamer ile aynı)."""

import sys
import threading
import time


class WebcamStreamer:
    """
    Webcam'dan sürekli kare çeker ve en güncel JPEG'i bellekte tutar.
    Queue yerine tek bir 'latest frame' yaklaşımı — eski kare birikimi olmaz.
    """

    JPEG_QUALITY = 72
    MAX_DIM      = 640
    WARMUP       = 6

    def __init__(self):
        self._latest: bytes | None = None
        self._lock   = threading.Lock()
        self._active = False
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_status = "ok"
        self._waiting = False
        # Kamera beklenmedik biçimde kapanınca (açılamadı / kare gelmiyor) çağrılır.
        self.on_ended = None
        # macOS kamera izni hiç sorulmamışsa çağrılır (Core → arayüz): izin penceresi ANA iş parçacığında açılır.
        # OpenCV izni arka plan iş parçacığından isteyemiyor ("can not spin main run loop from other thread"), bu
        # yüzden JARVIS 2'de kamera izni hiç sorulmuyor ve kamera açılmıyordu (04.10). Dönüş: yeni izin durumu.
        self.request_auth = None

    @property
    def is_active(self) -> bool:
        return self._active

    def get_latest_frame(self) -> bytes | None:
        """Thread-safe, her zaman en güncel kareyi döner."""
        with self._lock:
            return self._latest

    def start(self, wait: float = 0.0) -> str:
        """wait>0: kameranın gerçekten açılmasını bekler; "ok", "already_active",
        "camera_unavailable" veya "opencv_missing" döner."""
        with self._lock:
            if self._active:
                return "already_active"
            self._active = True
            self._latest = None
            self._generation += 1
            generation = self._generation
            self._ready = threading.Event()
            self._start_status = "ok"
            self._waiting = wait > 0
            ready = self._ready
        t = threading.Thread(target=self._run, args=(generation, ready), daemon=True)
        self._thread = t
        t.start()
        if wait <= 0:
            return "ok"
        done = ready.wait(wait)
        with self._lock:
            self._waiting = False
            status = self._start_status if done else "ok"
        return status

    def stop(self):
        with self._lock:
            self._active = False
            self._generation += 1   # eski iş parçacığı yeni başlatılanla karışmasın
            self._latest = None

    def _current(self, generation: int) -> bool:
        with self._lock:
            return self._active and self._generation == generation

    @staticmethod
    def _camera_index() -> int:
        """El takibiyle aynı kural: Mac'in kendi kamerası (sanal/iPhone kamerası değil)."""
        try:
            from brain import settings as brain_settings
            from brain.hand_tracker import find_builtin, list_cameras
            cam = find_builtin(list_cameras(timeout=8), brain_settings.hand_camera_uid())
            if cam is not None:
                return int(cam["index"])
        except Exception as exc:
            print(f"[Webcam] Yerleşik kamera belirlenemedi: {exc}")
        return 0

    @staticmethod
    def _auth_status():
        """0=sorulmadı, 1=kısıtlı, 2=reddedildi, 3=izinli, None=bilinmiyor (JARVIS 2 adına; osascript)."""
        try:
            from brain.hand_tracker import camera_auth_status
            return camera_auth_status()
        except Exception:
            return None

    def _finish(self, generation: int, status: str, ready: threading.Event):
        with self._lock:
            mine = self._generation == generation
            if mine:
                self._active = False
                self._latest = None
                self._start_status = status
            # Başlatan hâlâ bekliyorsa sonucu o bildirir; çift hata mesajı olmasın.
            notify = mine and not self._waiting
        ready.set()
        if notify and self.on_ended:
            try:
                self.on_ended(status)
            except Exception:
                pass

    def _run(self, generation: int, ready: threading.Event):
        try:
            import cv2
        except ImportError:
            print("[Webcam] opencv-python yüklü değil.")
            self._finish(generation, "opencv_missing", ready)
            return

        index = self._camera_index()
        if sys.platform == "darwin":
            status = self._auth_status()
            if status == 0 and self.request_auth is not None:
                try:
                    status = self.request_auth(index)
                except Exception as exc:
                    print(f"[Webcam] İzin istenemedi: {exc}")
            if status in (1, 2) or (status == 0):
                print(f"[Webcam] Kamera izni yok (durum {status}).")
                self._finish(generation, "camera_denied", ready)
                return
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            print("[Webcam] Kamera açılamadı.")
            cap.release()
            self._finish(generation, "camera_unavailable", ready)
            return

        enc_params = [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY]
        ended_by_error = False
        try:
            # Isınma — sensörün otomatik pozlaması oturuncaya kadar bekle
            got_frame = False
            for _ in range(self.WARMUP):
                ok, _frame = cap.read()
                got_frame = got_frame or ok
            if not got_frame:
                ended_by_error = True
                return
            ready.set()

            while self._current(generation):
                ret, frame = cap.read()
                if not ret:
                    ended_by_error = True
                    break

                h, w = frame.shape[:2]
                if max(h, w) > self.MAX_DIM:
                    s = self.MAX_DIM / max(h, w)
                    frame = cv2.resize(frame, (int(w * s), int(h * s)))

                frame = cv2.flip(frame, 1)  # yatay ayna — hem UI hem AI tutarlı
                ok, buf = cv2.imencode(".jpg", frame, enc_params)
                if ok:
                    with self._lock:
                        if self._generation == generation:
                            self._latest = buf.tobytes()

                # ~33 FPS yakala → 24 FPS UI her zaman taze kare bulur
                time.sleep(0.03)
        finally:
            cap.release()
            if ended_by_error:
                self._finish(generation, "camera_unavailable", ready)
            else:
                ready.set()
            print("[Webcam] Kamera serbest bırakıldı.")
