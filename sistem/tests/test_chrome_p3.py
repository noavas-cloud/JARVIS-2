"""Chrome ajanı 3. parça: telefondan başlatma (yerel denetim kanalı), geçmiş/devam, görünür JARVIS Chrome."""

import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from jarvis.paths import ensure_import_path  # noqa: E402

ensure_import_path()

from actions import chrome_research as cr  # noqa: E402
from jarvis import control  # noqa: E402
from jarvis.state import State  # noqa: E402
from test_chrome_research import GOOD, FakeBrowser, ScriptBrain, make_host  # noqa: E402


class ControlChannel(unittest.TestCase):
    def run_server(self, coro_fn):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            calls = []

            class Core:
                async def run_tool(self, name, args):
                    calls.append((name, dict(args)))
                    return {"status": "started", "echo": args.get("question")}

            async def go():
                server = asyncio.create_task(control.serve(Core(), Path(tmp)))
                for _ in range(50):
                    if (Path(tmp) / control.SOCKET_NAME).exists():
                        break
                    await asyncio.sleep(0.02)
                try:
                    return await coro_fn(Path(tmp))
                finally:
                    server.cancel()
            return asyncio.run(go()), calls, tmp

    def test_forwards_allowed_tool_as_phone(self):
        async def go(d):
            mode = stat.S_IMODE(os.stat(d / control.SOCKET_NAME).st_mode)
            out = await control.call("chrome_research", {"action": "start", "question": "soru", "_origin": "desktop"}, d)
            return out, mode
        (out, mode), calls, _ = self.run_server(go)
        self.assertIn("started", out)
        self.assertEqual(mode, 0o600, "soket yalnız bu kullanıcıya açık olmalı")
        self.assertEqual(calls[0][1]["_origin"], "phone", "kaynak her zaman telefon olarak işaretlenir")

    def test_rejects_other_tools(self):
        async def go(d):
            return await control.call("shell_run", {"command": "ls"}, d)
        out, calls, _ = self.run_server(go)
        self.assertIn("çalıştırılamaz", out)
        self.assertEqual(calls, [])

    def test_no_desktop_running_returns_none(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            self.assertIsNone(asyncio.run(control.call("chrome_research", {}, Path(tmp))))

    def test_agent_forwards_and_explains_when_closed(self):
        from jarvis_web import agent

        async def fake_call(name, args, *a, **k):
            return None
        with mock.patch.object(control, "call", fake_call):
            out = asyncio.run(agent.execute_tool("chrome_research", {"action": "status"}))
        self.assertIn("JARVIS 2 açık değil", out)

    def test_server_lists_tool_for_phone_but_not_web(self):
        from jarvis_web import server
        self.assertIn("chrome_research", server.ANDROID_V2_TOOLS)
        self.assertNotIn("chrome_research", server.ANDROID_APPROVAL_TOOLS)
        self.assertIn("chrome_research", server.WEB_BLOCKED_TOOLS, "tünel üzerinden web oturumu başlatamaz")


class HistoryAndContinue(unittest.TestCase):
    def write_reports(self, d):
        old = Path(d) / "2026-10-01_1000_eski.md"
        new = Path(d) / "2026-10-04_2030_yeni.md"
        old.write_text("# Eski\nAğrı 5137 m\n\n---\n**Soru:** Türkiye'nin dağları  \n**Tarih:** 01.10.2026 10:00 · 5 adım · durum: done\n", encoding="utf-8")
        new.write_text("# Yeni\n\n---\n**Soru:** Telefon karşılaştırması  \n**Tarih:** 04.10.2026 20:30 · 6 adım · durum: done\n", encoding="utf-8")
        os.utime(old, (1_000_000, 1_000_000))
        return old, new

    def test_history_newest_first_and_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_reports(tmp)
            host, *_ = make_host(tmp, ScriptBrain(GOOD))
            items = host.history()["reports"]
            self.assertEqual([i["question"] for i in items], ["Telefon karşılaştırması", "Türkiye'nin dağları"])
            self.assertEqual(items[0]["date"], "04.10.2026 20:30")
            self.assertEqual(host._find_report("son")["index"], 1)
            self.assertEqual(host._find_report("2")["question"], "Türkiye'nin dağları")
            self.assertEqual(host._find_report("dağ")["index"], 2)
            self.assertIsNone(host._find_report("yok-böyle"))

    def test_continue_feeds_previous_report_to_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_reports(tmp)
            brain = ScriptBrain(GOOD[-1:])
            host, state, told, _ = make_host(tmp, brain)
            seen = []
            orig = brain.next

            async def spy(history, only_finish=False):
                seen.append(history[0]["text"])
                return await orig(history, only_finish)
            brain.next = spy

            async def go():
                res = host.start("", "quick", continue_from="2")
                await asyncio.wait_for(host.task, 10)
                return res
            res = asyncio.run(go())
            self.assertEqual(res["status"], "started")
            self.assertIn("Türkiye'nin dağları", host.job.question)
            self.assertIn("DEVAMI", seen[0])
            self.assertIn("Ağrı 5137 m", seen[0])

    def test_continue_unknown_report_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD))

            async def go():
                return host.start("", "quick", continue_from="9")
            self.assertEqual(asyncio.run(go())["status"], "error")

    def test_phone_origin_does_not_speak_on_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, state, told, _ = make_host(tmp, ScriptBrain(GOOD))

            async def go():
                host.start("soru", "quick", origin="phone")
                await asyncio.wait_for(host.task, 10)
            asyncio.run(go())
            self.assertEqual(host.job.status, "done")
            self.assertEqual(told, [], "telefondan başlatılınca Mac sesli bildirim yapmaz")
            self.assertTrue(Path(host.job.report_path).exists())


class VisibleBrowser(unittest.TestCase):
    def test_detects_visible_profile_but_not_headless(self):
        prof = "/tmp/jarvis-profil"
        lines = [f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless=new --user-data-dir={prof}",
                 f"/Applications/Google Chrome.app/.../Google Chrome Helper --type=renderer --user-data-dir={prof}"]

        class R:
            stdout = "\n".join(lines)
        with mock.patch("subprocess.run", return_value=R()):
            self.assertFalse(cr.visible_chrome_running(Path(prof)))
        R.stdout += f"\n/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir={prof} --no-first-run"
        with mock.patch("subprocess.run", return_value=R()):
            self.assertTrue(cr.visible_chrome_running(Path(prof)))

    def test_start_refused_while_visible_chrome_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD))
            with mock.patch.object(cr, "visible_chrome_running", return_value=True):
                async def go():
                    return host.start("soru", "quick")
                res = asyncio.run(go())
            self.assertEqual(res["status"], "busy")
            self.assertIsNone(host.task)

    def test_open_browser_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD))
            host.profile_dir = Path(tmp) / "profil"
            ran = []
            with mock.patch("subprocess.run", side_effect=lambda cmd, **k: ran.append(cmd)):
                bad = asyncio.run(host.open_browser("file:///etc/passwd"))
                ok = asyncio.run(host.open_browser("https://ornek.org/giris"))
            self.assertEqual(bad["status"], "error")
            self.assertEqual(ok["status"], "ok")
            self.assertEqual(len(ran), 1)
            self.assertIn(f"--user-data-dir={host.profile_dir}", ran[0])
            self.assertNotIn("--headless=new", ran[0])
            self.assertNotIn("--remote-debugging-port=0", ran[0], "görünür Chrome denetlenmez")

    def test_phone_cannot_open_visible_browser(self):
        from jarvis import tools
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD))

            class Ctx:
                research = host
            out = asyncio.run(tools.run("chrome_research", {"action": "open_browser", "_origin": "phone"}, Ctx))
            self.assertEqual(out["status"], "error")


if __name__ == "__main__":
    unittest.main()
