#!/usr/bin/env python3
"""
JARVIS Web — Mac Ajanı
──────────────────────
Backend sunucusuna bağlanır ve sistem araçlarını (uygulama açma, takvim,
anımsatıcı, shell, ekran analizi, WhatsApp, medya...) bu Mac'te çalıştırır.

Çalıştırma:
    python3 agent.py                          # localhost'taki sunucuya
    python3 agent.py --server ws://1.2.3.4:8765 --token <token>

Token verilmezse aynı klasördeki web_config.json'dan okunur.
"""

from __future__ import annotations

import sys

import asyncio
import argparse
import json
import ssl
import traceback
from pathlib import Path

import websockets

# ── Ana proje modüllerine erişim ─────────────────────────────────────────────
WEB_DIR  = Path(__file__).resolve().parent
BASE_DIR = WEB_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from actions.activity_history import start_activity_watcher  # noqa: E402
from memory.conversation_history import ConversationHistory  # noqa: E402
from jarvis import tools as registry  # noqa: E402  (JARVIS 2: masaüstüyle aynı araç kaydı)

RECONNECT_DELAY = 3.0
# Bu araçlar masaüstü JARVIS 2'nin canlı durumuna (mikrofon, kamera, ekran, İkinci Beyin penceresi) bağlıdır.
AGENT_BLOCKED = {"microphone_control", "toggle_webcam", "watch_screen", "second_brain", "call_and_say",
                 # 3. aşama: kayıtları yalnız masaüstü JARVIS 2 yönetir (jarvis/tasks.py STAGE3_TOOLS)
                 "start_workflow", "resume_workflow", "get_workflow", "cancel_workflow", "run_parallel_tasks",
                 "get_parallel_tasks", "resume_tasks", "start_watch", "list_watches", "cancel_watch",
                 "start_phone_call", "get_phone_call", "list_phone_calls"}
# Bu araçlar masaüstü JARVIS 2'nin içinde çalışır; ajan isteği yerel denetim kanalından iletir (jarvis/control.py).
DESKTOP_FORWARD = {"chrome_research"}


class _NullState:
    """Ajan sürecinde arayüz yok: araçların arayüze bildirimleri (ör. işlem geçmişi penceresi) yok sayılır."""
    paused = muted = False

    def post(self, *args):
        pass

    def log(self, *args):
        pass


class AgentContext:
    def __init__(self):
        self.state = _NullState()
        self.history = ConversationHistory()
        self.brain = None


CTX = AgentContext()


async def execute_tool(name: str, args: dict) -> str:
    """JARVIS 2: araçlar masaüstündekiyle aynı kayıttan (jarvis/tools.py) çalışır; ayrı kopya yok."""
    if name in AGENT_BLOCKED:
        return "Bu araç yalnızca Mac'teki JARVIS 2 penceresinden kullanılabilir."
    if name in DESKTOP_FORWARD:
        from jarvis import control
        out = await control.call(name, args)
        return out if out is not None else "Mac'te JARVIS 2 açık değil; bu iş için JARVIS 2'nin açık olması gerekiyor."
    try:
        result = await registry.run(name, args, CTX)
        failed = registry.is_error(result, name)
        try:
            from actions.action_history import OTHER_CHANGES, record_other_change
            if name in OTHER_CHANGES:
                await asyncio.to_thread(record_other_change, name, args, failed)
        except Exception:
            pass
        return str(registry.as_text(result))
    except Exception as e:
        traceback.print_exc()
        return f"Hata: {e}"


async def handle_connection(ws):
    print("[Ajan] ✅ Sunucuya bağlandı — araç çağrıları bekleniyor.")
    async def run_call(obj: dict):
        name = obj.get("name", "")
        args = obj.get("args", {}) or {}
        print(f"[Ajan] 🔧 {name} {args}")
        result = await execute_tool(name, args)
        print(f"[Ajan] 📤 {name} → {str(result)[:80]}")
        await ws.send(json.dumps(
            {"type": "tool_result", "id": obj.get("id", ""), "result": result},
            ensure_ascii=False,
        ))

    async for message in ws:
        try:
            obj = json.loads(message)
        except Exception:
            continue
        if obj.get("type") == "tool_call":
            # Eşzamanlı çağrılar birbirini bloklamasın
            asyncio.create_task(run_call(obj))


def load_token_from_config() -> str:
    try:
        cfg = json.loads((WEB_DIR / "web_config.json").read_text(encoding="utf-8"))
        # Uzaktaki sunucuya bağlanan ajan ayrı 'agent_token' kullanır (bkz. server._agent_allowed)
        return str(cfg.get("agent_token", "") or cfg.get("token", "") or "")
    except Exception:
        return ""


async def main():
    start_activity_watcher()
    ap = argparse.ArgumentParser(description="JARVIS Mac ajanı")
    ap.add_argument("--server", default="ws://127.0.0.1:8765",
                    help="Sunucu adresi, örn. wss://1.2.3.4:8765")
    ap.add_argument("--token", default="",
                    help="Erişim token'ı (boşsa web_config.json'dan okunur)")
    args = ap.parse_args()

    token = args.token or load_token_from_config()
    if not token:
        print("[Ajan] ❌ Token bulunamadı. --token verin veya önce sunucuyu çalıştırın.")
        return

    url = f"{args.server.rstrip('/')}/ws/agent?token={token}"

    ssl_ctx = None
    if url.startswith("wss://"):
        # Kendinden imzalı sertifika kabul edilir (v1)
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    print(f"[Ajan] 🔌 Bağlanılıyor: {args.server}")
    while True:
        try:
            async with websockets.connect(url, ssl=ssl_ctx,
                                          ping_interval=20) as ws:
                await handle_connection(ws)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Ajan] ⚠️  Bağlantı sorunu: {e} — {RECONNECT_DELAY:.0f}s sonra tekrar denenecek.")
        await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Ajan] 👋 Kapatıldı.")
