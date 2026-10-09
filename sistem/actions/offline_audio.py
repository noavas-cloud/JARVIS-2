"""Utterance endpointing on the existing PCM stream; no second microphone."""
from collections import deque


class OfflineUtterances:
    def __init__(self):
        self.noise = 50.0
        self.reset()

    def reset(self):
        self.frames = []
        self.preroll = deque(maxlen=8)
        self.rate = None
        self.seconds = self.silence = self.voiced = 0.0
        self.discarding = False

    @property
    def active(self):
        return bool(self.frames)

    def feed(self, data, rate, rms):
        if self.rate not in (None, rate):
            self.reset()
        self.rate = rate
        duration = len(data) / (2 * rate)
        speech = rms > max(100, self.noise * 2.5)
        if self.discarding:
            self.silence = 0.0 if speech else self.silence + duration
            if self.silence >= .6:
                self.reset()
            return None
        if not self.frames:
            if not speech:
                self.noise = .98 * self.noise + .02 * min(rms, 400)
                self.preroll.append(data)
                return None
            self.frames = list(self.preroll)
            self.preroll.clear()
        self.frames.append(data)
        self.seconds += duration
        self.voiced += duration if speech else 0
        self.silence = 0.0 if speech else self.silence + duration
        if self.seconds >= 12:
            self.reset()  # Discard the entire overlong utterance, including its tail.
            self.discarding = True
            return None
        if self.silence < .6:
            return None
        result = (b''.join(self.frames), rate) if self.voiced >= .25 else None
        self.reset()
        return result
