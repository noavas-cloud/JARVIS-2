"""JARVIS İkinci Beyin: yerel bilgi grafiği ve el hareketiyle kontrol.

Bu paket ana arayüzden bağımsız süreçlerde çalışacak şekilde tasarlandı:
  * brain.indexer  — seçilen klasörleri tarar, grafiği SQLite'a yazar (düşük öncelik)
  * brain.window   — grafiği ayrı bir Tk penceresinde çizer (ayrı süreç)
  * brain.hand_tracker — kamera + MediaPipe el takibi (ayrı süreç, izole ortam)
Ana arayüz yalnızca brain.launcher ve brain.query'yi kullanır.
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
