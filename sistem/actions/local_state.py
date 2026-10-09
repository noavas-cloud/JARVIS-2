"""Small durable local checkpoints; replace only after data reaches disk."""
import json
import os
from pathlib import Path
import tempfile
import threading


class LocalState:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def read(self, default):
        with self.lock:
            if not self.path.exists():
                return default
            return json.loads(self.path.read_text(encoding='utf-8'))

    def write(self, data):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, raw = tempfile.mkstemp(prefix='.' + self.path.name, dir=self.path.parent)
            temporary = Path(raw)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump(data, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
