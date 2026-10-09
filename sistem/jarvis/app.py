"""JARVIS 2'yi başlatır: tek kopya kilidi, asıl JARVIS açıksa uyarı, çekirdek + arayüz.

Çalıştırma:  sistem/venv/bin/python -m jarvis.app      (ya da JARVIS 2.app)
"""

from __future__ import annotations

import fcntl
import os
import signal
import sys
import threading

from jarvis.paths import ORIGINAL_MARKERS, RUNTIME_DIR, ensure_import_path

ensure_import_path()


def acquire_instance():
    """Kilidi tutan dosya tanıtıcısı; None = JARVIS 2 zaten açık."""
    RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (RUNTIME_DIR / "main.lock").open("a+", encoding="ascii")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        try:
            pid = int(handle.read().strip() or 0)
        except ValueError:
            pid = 0
        handle.close()
        if pid > 1:
            try:
                os.kill(pid, signal.SIGCONT)        # açık pencere kendini öne getirir
            except OSError:
                pass
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def original_running() -> bool:
    """Asıl JARVIS açık mı (yalnız süreç listesine bakılır; asıl JARVIS'e dokunulmaz)."""
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline", "exe"]):
            info = " ".join(proc.info.get("cmdline") or []) + " " + str(proc.info.get("exe") or "")
            if any(marker in info for marker in ORIGINAL_MARKERS):
                return True
    except Exception:
        pass
    return False


def ask_continue_with_original() -> bool:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    ok = messagebox.askyesno(
        "Asıl JARVIS açık",
        "Asıl JARVIS şu an açık. İkisi aynı anda mikrofonu dinlerse ikisi de cevap verir ve telefon bağlantısı "
        "karışır.\n\nÖnerilen: asıl JARVIS'i kapatıp JARVIS 2'yi öyle aç.\n\nYine de JARVIS 2 açılsın mı?",
        icon="warning")
    root.destroy()
    return bool(ok)


def _preview_driver(state, path):
    """Yalnız önizleme: dosyadaki komutlarla sahte durum (status/speak/tool/muted/paused/log/heard)."""
    import math
    import time
    from pathlib import Path

    def run():
        speak = 0.0
        while True:
            try:
                p = Path(path)
                if p.exists():
                    for line in p.read_text(encoding="utf-8").splitlines():
                        cmd, _, arg = line.partition(" ")
                        if cmd == "status":
                            state.set_status(arg)
                        elif cmd == "speak":
                            speak = float(arg)
                            state.set_status("SPEAKING" if speak > 0 else "LISTENING")
                        elif cmd == "tool":
                            if arg:
                                state.tool_started(arg, arg)
                            else:
                                state.tool_finished(state.tool, True)
                        elif cmd == "muted":
                            state.muted = arg == "1"
                        elif cmd == "paused":
                            state.paused = arg == "1"
                        elif cmd == "browsing":
                            state.browsing = arg == "1"
                        elif cmd == "cloud":
                            state.cloud = arg == "1"
                        elif cmd == "settings":
                            state.post("preview_settings")
                        elif cmd == "scroll":      # ayarlar panelini kaydır (0..1)
                            state.post("preview_scroll", float(arg))
                        elif cmd == "logs":
                            state.post("preview_logs")
                        elif cmd == "phone":
                            state.post("preview_phone")
                        elif cmd == "heard":
                            state.heard = arg
                        elif cmd == "log":
                            who, _, text = arg.partition(" ")
                            state.log(who, text)
                        elif cmd == "task":        # task <durum> <yüzde|-> <ad>  ·  "task clear"
                            if arg == "clear":
                                state.set_tasks([])
                            else:
                                st, _, rest = arg.partition(" ")
                                pct, _, label = rest.partition(" ")
                                rows = [r for r in state.task_rows if r["label"] != label]
                                rows.append({"label": label, "status": st,
                                             "progress_percent": None if pct == "-" else int(pct)})
                                state.set_tasks(rows)
                        elif cmd == "done":
                            name, _, ok = arg.partition(" ")
                            state.tool_started(name, name)
                            state.tool_finished(name, ok != "0")
                    p.unlink()
                if speak > 0:
                    state.output_level = speak * (0.55 + 0.45 * math.sin(time.time() * 9))
                state.mic_level = 0.15 + 0.1 * math.sin(time.time() * 3) if not state.muted else 0.0
            except Exception as exc:
                print("[ÖNİZLEME]", exc, flush=True)
            time.sleep(0.05)
    threading.Thread(target=run, daemon=True).start()


def main():
    instance = acquire_instance()
    if instance is None:
        print("[JARVIS 2] Zaten açık; pencere öne getirildi.", flush=True)
        return
    if os.environ.get("JARVIS2_PREVIEW") != "1" and original_running() and not ask_continue_with_original():
        return
    restore = threading.Event()
    signal.signal(signal.SIGCONT, lambda *_: restore.set())
    signal.siginterrupt(signal.SIGCONT, False)

    from jarvis.live import Core, start_in_thread
    from jarvis.state import State
    from jarvis.ui.window import JarvisWindow

    state = State()
    core = Core(state)
    ui = JarvisWindow(state, core)
    ui.on_shutdown = core.shutdown
    signal.signal(signal.SIGTERM, lambda *_: ui.root.after(0, ui.shutdown))

    def check_restore():
        if restore.is_set():
            restore.clear()
            ui.bring_to_front()
        ui.root.after(150, check_restore)
    ui.root.after(150, check_restore)

    if os.environ.get("JARVIS2_PREVIEW") != "1":
        try:
            from actions.activity_history import start_activity_watcher
            start_activity_watcher()
        except Exception as exc:
            print(f"[JARVIS 2] Etkinlik geçmişi başlatılamadı: {exc}", flush=True)
    start_in_thread(core)
    if os.environ.get("JARVIS2_PREVIEW_CMD"):
        _preview_driver(state, os.environ["JARVIS2_PREVIEW_CMD"])
    try:
        ui.root.mainloop()
    finally:
        instance.close()


if __name__ == "__main__":
    sys.exit(main())
