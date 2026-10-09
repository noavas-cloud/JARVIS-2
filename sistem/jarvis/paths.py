"""JARVIS 2'nin klasörleri ve kimliği. Asıl JARVIS'ten ayrı tutulan her şey burada tanımlıdır."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # .../JARVIS 2/sistem
ROOT_DIR = BASE_DIR.parent                                  # .../JARVIS 2
ICON_DIR = BASE_DIR / "Icon"
SFX_DIR = BASE_DIR / "SFX"
FONT_DIR = BASE_DIR / "Fonts"
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"
HUD_PLATE = ICON_DIR / "jarvis_hud.webp"

APP_NAME = "JARVIS 2"
BUNDLE_ID = "com.kemal.jarvis2"
LOG_DIR = Path.home() / "Library" / "Logs" / "JARVIS 2"
# Tek kopya kilidi: asıl JARVIS /private/tmp/jarvis-runtime-<uid> kullanır; JARVIS 2 ayrı klasör kullanır.
RUNTIME_DIR = Path(os.environ.get("JARVIS2_RUNTIME_DIR", f"/private/tmp/jarvis2-runtime-{os.getuid()}"))

# Asıl JARVIS'in tanınması (yalnız okunur: açık mı diye bakmak için)
ORIGINAL_MARKERS = ("/Desktop/JARVIS/sistem/main.py", "/Applications/JARVIS.app/")


def ensure_import_path() -> None:
    """Taşınan özellik modülleri (actions, brain, memory, jarvis_web…) sistem/ kökünden içe aktarılır."""
    base = str(BASE_DIR)
    if base not in sys.path:
        sys.path.insert(0, base)
