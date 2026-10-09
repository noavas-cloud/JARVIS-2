"""Hafif ortak tanımlar (numpy vb. ağır içe aktarma yok; ana süreçte güvenle kullanılır)."""

from __future__ import annotations

import fcntl
from pathlib import Path

REL_TEXT = {
    "belongs_to": "proje klasöründe",
    "part_of": "üst projenin parçası",
    "links_to": "bağlantı veriyor",
    "mentions": "adını anıyor",
    "tagged": "etiketli",
    "similar_content": "benzer içerik",
    "about_topic": "konu tahmini",
}


class IndexLock:
    """Aynı anda tek indeksleyici çalışsın (ana arayüz ve pencere ikisi de başlatabilir)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self):
        if self.fh:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()
                self.fh = None


def is_indexing(lock_path: Path) -> bool:
    if not Path(lock_path).exists():
        return False
    lock = IndexLock(lock_path)
    if lock.acquire():
        lock.release()
        return False
    return True
