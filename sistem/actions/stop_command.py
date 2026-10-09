"""Offline stop-command recognition on the existing microphone stream."""
import asyncio
from collections import deque
from pathlib import Path
import re
import threading
import time


def is_stop_command(text):
    words = re.findall(r"[^\W_]+", str(text).casefold(), re.UNICODE)
    return len(words) >= 2 and words[-2:] in (["jarvis", "sus"], ["sus", "jarvis"])


class StopCommandListener:
    WINDOW_SECONDS = 3.0
    MIN_SECONDS = 0.9
    CHECK_INTERVAL = 0.30

    def __init__(self, key_provider=None):
        # key_provider retained for compatibility; no key or network is used.
        self.frames = deque()
        self.bytes = 0
        self.rate = None
        self.generation = 0
        self.version = 0
        self.checked_version = -1
        self.ready = False
        self.model = None
        self.lock = threading.Lock()
        self.model_dir = Path(__file__).resolve().parents[1] / "models"

    def reset(self):
        self.frames.clear()
        self.bytes = 0
        self.rate = None
        self.generation += 1

    def feed(self, data, rate):
        if self.rate is not None and self.rate != rate:
            self.reset()
        self.rate = rate
        self.frames.append(data)
        self.bytes += len(data)
        limit = int(rate * 2 * self.WINDOW_SECONDS)
        while self.frames and self.bytes - len(self.frames[0]) >= limit:
            self.bytes -= len(self.frames.popleft())
        self.version += 1

    def load(self):
        with self.lock:
            if self.model is None:
                from faster_whisper import WhisperModel
                self.model = WhisperModel(
                    "small", device="cpu", compute_type="int8", cpu_threads=4,
                    download_root=str(self.model_dir), local_files_only=True,
                )

    def recognize(self, data, rate, prompt="Jarvis. Sus."):
        import numpy as np
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if len(samples) == 0 or float(np.sqrt(np.mean(samples * samples))) < .001:
            return ""
        if rate != 16000:
            count = round(len(samples) * 16000 / rate)
            samples = np.interp(np.arange(count) * rate / 16000, np.arange(len(samples)), samples).astype(np.float32)
        with self.lock:
            segments, _ = self.model.transcribe(
                samples, language="tr", beam_size=3, temperature=0,
                condition_on_previous_text=False, initial_prompt=prompt,
                vad_filter=False,
            )
            # Reject uncertain guesses and silence hallucinations.
            return " ".join(s.text for s in segments if s.no_speech_prob < .65 and s.avg_logprob > -1.0).strip()

    async def run(self, active, on_stop, on_error, on_ready=None, on_command=None):
        last_error = -float("inf")
        try:
            while self.model is None:
                try:
                    await asyncio.to_thread(self.load)
                except Exception as exc:
                    on_error("Yerel ses modeli yüklenemedi: " + type(exc).__name__)
                    await asyncio.sleep(30)
            self.ready = True
            if on_ready:
                on_ready()
            while True:
                await asyncio.sleep(self.CHECK_INTERVAL)
                if not active() or not self.rate:
                    continue
                if self.bytes < self.rate * 2 * self.MIN_SECONDS or self.version == self.checked_version:
                    continue
                generation = self.generation
                self.checked_version = self.version
                data, rate = b"".join(self.frames), self.rate
                try:
                    text = await asyncio.to_thread(self.recognize, data, rate,
                        "Jarvis sus. Sus Jarvis. Mikrofon kapan. Mikrofon kapat."
                        if on_command else "Jarvis. Sus.")
                except Exception as exc:
                    if time.monotonic() - last_error >= 30:
                        on_error("Yerel ses tanıma hatası: " + type(exc).__name__)
                        last_error = time.monotonic()
                    continue
                # A result from an earlier answer must never stop the next answer.
                if generation == self.generation and active():
                    if on_command and await on_command(text):
                        self.reset()
                    elif is_stop_command(text):
                        self.reset()
                        await on_stop()
        finally:
            self.ready = False
            self.reset()
