"""JARVIS 2 yerel denetim kanalı (05.10.2026, Chrome ajanı 3. parça).

Telefon ajanı (jarvis_web/agent.py) ayrı bir süreçtir; masaüstü JARVIS 2'nin canlı nesnelerine (ör. Chrome araştırma
ajanı, JARVIS Chrome penceresi) erişemez. Bu kanal o tür araçları JARVIS 2'nin içinde çalıştırır:
* Unix soketi RUNTIME_DIR içinde (klasör 0700, soket 0600) → yalnız bu Mac'teki bu kullanıcı; ağdan erişilemez.
* Yalnız ALLOWED listesindeki araçlar (şu an yalnız chrome_research); istek en çok 16 KB.
* JARVIS 2 kapalıysa ajan "JARVIS 2 açık değil" der (call → None).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from jarvis.paths import RUNTIME_DIR

SOCKET_NAME = "control.sock"
ALLOWED = {"chrome_research"}
MAX_REQUEST = 16_384


async def serve(core, runtime_dir: Path = RUNTIME_DIR):
    from jarvis import tools
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = runtime_dir / SOCKET_NAME
    try:
        path.unlink()
    except OSError:
        pass

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            req = json.loads(line) if line else {}
            name, args = req.get("tool"), req.get("args") or {}
            if name not in ALLOWED or not isinstance(args, dict):
                resp = {"result": "Bu araç bu kanaldan çalıştırılamaz."}
            else:
                args = dict(args)
                args["_origin"] = "phone"
                resp = {"result": tools.as_text(await core.run_tool(name, args))}
        except (ValueError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            resp = {"result": "İstek geçersiz."}
        except Exception as exc:                       # kanal JARVIS'i asla düşürmesin
            resp = {"result": f"Hata: {type(exc).__name__}"}
        try:
            writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=str(path), limit=MAX_REQUEST)
    os.chmod(path, 0o600)
    async with server:
        await server.serve_forever()


async def call(name: str, args: dict, runtime_dir: Path = RUNTIME_DIR, timeout: float = 60.0) -> str | None:
    """Ajan tarafı: aracı masaüstü JARVIS 2'de çalıştırır. None = JARVIS 2 açık değil (soket yok/yanıt yok)."""
    path = Path(runtime_dir) / SOCKET_NAME
    if not path.exists():
        return None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path), limit=1 << 20), 3)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        writer.write((json.dumps({"tool": name, "args": args}, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return json.loads(line).get("result", "") if line else None
    except (OSError, ValueError, asyncio.TimeoutError):
        return None
    finally:
        writer.close()
