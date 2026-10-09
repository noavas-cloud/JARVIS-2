"""JARVIS 2 çekirdek testleri: araç kaydı, talimat süzgeci, ses dengeleme, döküm birleştirme, küre boyutu."""

import array
import time
import ast
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path[:0] = [str(BASE)]

from jarvis import prompt, tools  # noqa: E402
from jarvis.audio import MicLeveler, rms_int16  # noqa: E402
from jarvis.live import join_transcript, clean_output  # noqa: E402
from jarvis.ui.orb import face_size, image_size  # noqa: E402
from tool_defs import TOOL_DECLARATIONS  # noqa: E402

ORIGINAL = Path.home() / "Desktop" / "JARVIS" / "sistem"


class OldMicLeveler:
    """Asıl JARVIS main.py'deki MicLeveler'ın aynısı (karşılaştırma için)."""
    TARGET_RMS, MAX_GAIN, ATTACK = 800.0, 3.0, 0.1

    def __init__(self):
        self.gain, self.noise = 1.0, None

    def process(self, data, rms):
        if self.noise is None:
            self.noise = rms
        else:
            self.noise += (rms - self.noise) * (0.3 if rms < self.noise else 0.01)
        speech = rms > max(120.0, self.noise * 2.5)
        target = min(self.MAX_GAIN, self.TARGET_RMS / rms) if speech and rms < self.TARGET_RMS else 1.0
        new = self.gain + (target - self.gain) * (0.6 if target < self.gain else self.ATTACK)
        samples = array.array("h", data)
        start = self.gain
        peak = max((abs(x) for x in samples), default=0)
        if peak:
            limit = max(1.0, 32000.0 / peak)
            start, new = min(start, limit), max(1.0, min(new, limit))
        self.gain = new
        if abs(start - 1.0) < 1e-3 and abs(new - 1.0) < 1e-3:
            return data
        n = len(samples) or 1
        step = (new - start) / n
        return array.array("h", (max(-32768, min(32767, int(x * (start + step * (i + 1)))))
                                 for i, x in enumerate(samples))).tobytes()


class ToolRegistry(unittest.TestCase):
    def test_every_handler_has_declaration(self):
        declared = {d["name"] for d in TOOL_DECLARATIONS}
        self.assertFalse(tools.available() - declared, "bildirimi olmayan işleyici")

    def test_declarations_only_available(self):
        names = [d["name"] for d in tools.declarations()]
        self.assertEqual(set(names), tools.available())
        self.assertEqual(len(names), len(set(names)))

    def test_phase1_core_tools_present(self):
        must = {"open_app", "get_weather", "search_web", "find_file", "trash_file", "second_brain",
                "analyze_screen", "watch_screen", "send_whatsapp_message", "microphone_control", "save_memory"}
        self.assertFalse(must - tools.available())

    def test_is_error(self):
        self.assertTrue(tools.is_error(json.dumps({"status": "error"})))
        self.assertFalse(tools.is_error(json.dumps({"status": "partial"})))
        self.assertFalse(tools.is_error("Sayfada 'hata' kelimesi geçiyor"))
        self.assertTrue(tools.is_error("Hata: bağlantı yok"))
        self.assertTrue(tools.is_error("Takvim okunamadi: izin yok", "get_calendar_events"))
        self.assertTrue(tools.is_error("'Foo' bulunamadı veya açılamadı.", "open_app"))

    def test_as_text_keeps_turkish(self):
        self.assertIn("ğüşiöç", tools.as_text({"m": "ğüşiöç"}))


class Prompt(unittest.TestCase):
    def test_rules_filtered_by_tool(self):
        text = "Giriş\n# ARAÇ\n[@a,b]\n- a kuralı\n[@c]\n- c kuralı\n# SON\n- son"
        out = prompt.filtered_rules(text, {"b"})
        self.assertIn("a kuralı", out)
        self.assertNotIn("c kuralı", out)
        self.assertIn("son", out)

    def test_real_prompt_mentions_only_available_tools(self):
        built = prompt.build(tools.available())
        self.assertIn("[ŞU ANKİ ZAMAN]", built)
        for missing in ("start_workflow", "start_phone_call", "start_watch", "run_parallel_tasks"):
            self.assertNotIn(missing + "(", built)
        self.assertLess(len(built), 20000)

    def test_every_tag_names_real_tools(self):
        declared = {d["name"] for d in TOOL_DECLARATIONS}
        for line in (BASE / "core" / "prompt.txt").read_text(encoding="utf-8").splitlines():
            m = prompt._TAG.match(line.strip())
            if m:
                for name in m.group(1).split(","):
                    self.assertIn(name.strip(), declared, line)


class Audio(unittest.TestCase):
    def test_leveler_matches_original(self):
        import random
        rnd = random.Random(7)
        new, old = MicLeveler(), OldMicLeveler()
        for k in range(120):
            amp = rnd.choice([30, 200, 400, 900, 3000, 20000])
            data = array.array("h", (max(-32768, min(32767, int(rnd.gauss(0, amp)))) for _ in range(512))).tobytes()
            rms = rms_int16(data)
            a = array.array("h", new.process(data, rms))
            b = array.array("h", old.process(data, rms))
            self.assertLessEqual(max(abs(x - y) for x, y in zip(a, b)), 1, f"parça {k}")
            self.assertAlmostEqual(new.gain, old.gain, places=5)

    def test_rms(self):
        self.assertAlmostEqual(rms_int16(array.array("h", [3, -3, 3, -3]).tobytes()), 3.0)
        self.assertEqual(rms_int16(b""), 0.0)


class Transcript(unittest.TestCase):
    def test_join(self):
        self.assertEqual(join_transcript([" Ja", "rvis", " bana", " ha", "ftalık<noise>"]), "Jarvis bana haftalık")

    def test_clean_output(self):
        self.assertEqual(clean_output("Merhaba<ctrl46> dünya\x07"), "Merhaba dünya")


class Orb(unittest.TestCase):
    def test_face_matches_original_formula(self):
        for w, h in ((1710, 1112), (1540, 940), (1470, 956), (1280, 800), (1662, 982)):
            left, right = min(310, int(w * 0.22)), min(340, int(w * 0.24))
            old = min(int((h - 72 - 126 - 30 - 24) * 0.88), int((w - left - right) * 0.74), 560)
            self.assertEqual(face_size(w, h), old)
        self.assertEqual(image_size(560), 694)

    def test_hud_render_identical_to_original(self):
        orig = ORIGINAL / "hud_render.py"
        if not orig.exists():
            self.skipTest("asıl JARVIS bulunamadı")
        self.assertEqual((BASE / "hud_render.py").read_bytes(), orig.read_bytes())
        self.assertEqual((BASE / "Icon" / "jarvis_hud.webp").read_bytes(),
                         (ORIGINAL / "Icon" / "jarvis_hud.webp").read_bytes())

    def test_draw_hud_body_matches_original(self):
        """ui/orb.py'deki çizim adımları asıl ui.py _draw_hud'daki çağrılarla aynı sırada olmalı."""
        orig = ORIGINAL / "ui.py"
        if not orig.exists():
            self.skipTest("asıl JARVIS bulunamadı")

        def calls(src, cls, fn):
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == fn:
                    return [n.func.attr for n in ast.walk(node)
                            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                            and n.func.attr.startswith("create_")]
        old = calls(orig.read_text(encoding="utf-8"), "JarvisUI", "_draw_hud")
        new = calls((BASE / "jarvis" / "ui" / "orb.py").read_text(encoding="utf-8"), "OrbView", "draw")
        self.assertEqual(old, [c for c in new][:len(old)])


if __name__ == "__main__":
    unittest.main()


class PhoneAndCall(unittest.TestCase):
    """Ara ve söyle akışı (gerçek arama yapılmaz; call_relay taklit edilir) + telefon sunucusu talimatı."""

    def setUp(self):
        from actions import call_relay
        from jarvis.phone_host import PhoneHost
        from jarvis.state import State
        self.cr = call_relay
        self.saved = {k: getattr(call_relay, k) for k in
                      ("phone_connected", "resolve_contact", "call_via_phone", "call_via_mac")}
        self.calls = []
        call_relay.call_via_phone = lambda n, num, m: self.calls.append(("phone", n, num, m)) or {"status": "dialing"}
        call_relay.call_via_mac = lambda num, m: self.calls.append(("mac", num, m)) or {"status": "dialing"}
        self.host = PhoneHost(State())

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(self.cr, k, v)

    def run_call(self, args, answer=True):
        import asyncio

        async def confirm(title, text):
            self.asked = text
            return answer
        return asyncio.run(self.host.call_and_say(args, confirm))

    def test_phone_connected_goes_to_phone(self):
        self.cr.phone_connected = lambda: True
        self.cr.resolve_contact = lambda n, num: {"status": "ok", "number": "+905551112233", "name": n}
        res = self.run_call({"recipient_name": "Ali", "message": "Toplantı iptal."})
        self.assertEqual(res["status"], "dialing")
        self.assertEqual(self.calls, [("phone", "Ali", "+905551112233", "Toplantı iptal.")])

    def test_mac_needs_confirmation(self):
        self.cr.phone_connected = lambda: False
        self.cr.resolve_contact = lambda n, num: {"status": "ok", "number": "+905551112233", "name": "Ali"}
        res = self.run_call({"recipient_name": "Ali", "message": "Geç kalacağım."}, answer=False)
        self.assertEqual(res["status"], "cancelled")
        self.assertEqual(self.calls, [])
        self.assertIn("+905551112233", self.asked)
        res = self.run_call({"recipient_name": "Ali", "message": "Geç kalacağım."}, answer=True)
        self.assertEqual(res["_mac_call"], ("+905551112233", "Geç kalacağım."))

    def test_bad_input(self):
        self.cr.phone_connected = lambda: False
        self.assertEqual(self.run_call({"recipient_name": "Ali", "message": ""})["status"], "error")
        self.assertEqual(self.run_call({"message": "Merhaba"})["status"], "needs_recipient")
        self.assertEqual(self.run_call({"phone_number": "abc", "message": "Merhaba"})["status"], "invalid_number")

    def test_call_and_say_registered_and_prompted(self):
        self.assertIn("call_and_say", tools.available())
        self.assertIn("call_and_say(", prompt.build(tools.available()))

    def test_phone_server_prompt_has_no_tags(self):
        sys.path.insert(0, str(BASE / "jarvis_web"))
        import importlib
        server = importlib.import_module("jarvis_web.server")
        text = server.load_system_prompt()
        self.assertNotIn("[@", text)
        self.assertIn("search_web", text)

    def test_agent_uses_registry(self):
        src = (BASE / "jarvis_web" / "agent.py").read_text(encoding="utf-8")
        self.assertIn("registry.run(", src)
        self.assertNotIn("from actions.open_app", src)


class Stage3(unittest.TestCase):
    """3. aşama: iş akışı (kilitlenmeden), kesintiden devam, Vapi onayı, görev satırları. Kayıtlar geçici klasörde;
    gerçek araçlar çalışmaz (sys_info/open_app işleyicileri taklit edilir), arama yapılmaz."""

    def setUp(self):
        import asyncio
        import tempfile
        from jarvis.live import Core
        from jarvis.state import State
        from jarvis.tasks import TaskHost
        from actions import action_history
        self.tmp = tempfile.TemporaryDirectory()
        self.ah, self.ah_saved = action_history, action_history.record_other_change
        action_history.record_other_change = lambda *a: None     # gerçek işlem geçmişine yazma
        self.ran = []
        self.saved = dict(tools._HANDLERS)
        tools._HANDLERS["sys_info"] = (lambda a, ctx: self.ran.append(("sys_info", a)) or "Pil: %80", "thread")
        tools._HANDLERS["open_app"] = (lambda a, ctx: self.ran.append(("open_app", a)) or "Safari açıldı.", "thread")

        class FakeHistory:
            def record_tool(self, *a):
                pass

        class FakeCore:                       # Core.run_tool'un ihtiyaç duyduğu alanlar
            run_tool = Core.run_tool
            session = None
            response_pending = False
            mic_resume_at = 0.0
            player = type("P", (), {"queue": None})()

        core = FakeCore()
        core.state = State()
        core.history = FakeHistory()
        core.tool_lock = asyncio.Lock()
        core.tasks = TaskHost(core, store_dir=self.tmp.name)
        self.core, self.host = core, core.tasks

    def tearDown(self):
        self.ah.record_other_change = self.ah_saved
        tools._HANDLERS.clear()
        tools._HANDLERS.update(self.saved)
        self.tmp.cleanup()

    def arun(self, coro_fn):
        import asyncio

        async def go():
            self.core.tool_lock = asyncio.Lock()
            return await asyncio.wait_for(coro_fn(), 10)
        return asyncio.run(go())

    def test_all_declared_tools_have_handlers(self):
        from jarvis.tasks import STAGE3_TOOLS
        declared = {d["name"] for d in TOOL_DECLARATIONS}
        self.assertEqual(declared, tools.available())
        for name in STAGE3_TOOLS:
            self.assertNotEqual(tools.label(name), name.replace("_", " ").capitalize(), name)

    def test_workflow_runs_steps_without_deadlock(self):
        steps = json.dumps([{"id": "pil", "label": "Pili oku", "tool": "sys_info", "args": {"query": "battery"}},
                            {"id": "ac", "label": "Safari'yi aç", "tool": "open_app", "args": {"app_name": "Safari"}}])
        res = json.loads(self.arun(lambda: self.core.run_tool("start_workflow", {"title": "Deneme",
                                                                                  "steps_json": steps})))
        self.assertEqual(res["workflow_status"], "done", res)
        self.assertEqual([r[0] for r in self.ran], ["sys_info", "open_app"])
        rows = self.core.state.visible_tasks()
        self.assertEqual([r["status"] for r in rows], ["done", "done"])
        names = [e.name for e in self.core.state.recent_tools()]
        self.assertEqual(names, ["start_workflow", "sys_info", "open_app"])

    def test_workflow_rejects_unsafe_tool(self):
        steps = json.dumps([{"id": "k", "tool": "shell_run", "args": {"command": "ls"}}])
        res = json.loads(self.arun(lambda: self.core.run_tool("start_workflow", {"title": "x", "steps_json": steps})))
        self.assertEqual(res["status"], "error")
        self.assertEqual(self.ran, [])

    def test_checkpoint_records_and_does_not_repeat(self):
        h = self.host
        h.user_text("Safari'yi aç")
        key = h.request_id
        self.arun(lambda: h.run_checked("open_app", {"app_name": "Safari"}))
        self.assertEqual(len(self.ran), 1)
        row = h.requests.pending()[0]
        self.assertEqual(row["tools"][0]["status"], "done")
        # Bağlantı koptu, yeni oturumda kurtarma: aynı adım yeniden çalışmaz, kayıtlı sonuç döner.
        h.session_started()
        rkey, message = h.requests.recovery()
        self.assertEqual(rkey, key)
        self.assertIn("KESİNTİDEN DEVAM", message)
        h.request_id, h.request_is_recovery = rkey, True
        cached = self.arun(lambda: h.run_checked("open_app", {"app_name": "Safari"}))
        self.assertEqual(len(self.ran), 1)
        self.assertIn("Safari", str(cached))
        h.turn_done()
        self.assertEqual(h.requests.pending(), [])
        self.assertTrue(h.resume_requested)

    def test_phone_call_needs_setup_and_review(self):
        from actions.call_agent import CallManager
        h = self.host
        h.calls = CallManager(Path(self.tmp.name) / "calls.json", config_reader=lambda: {"enabled": False})
        res = self.arun(lambda: h.start_phone_call({"business_name": "Lokanta", "phone_number": "+902121112233",
                                                    "purpose": "Yer ayırt"}))
        self.assertEqual(res["status"], "needs_setup")
        self.assertIn(("open_call_agent",), self.core.state.drain())
        cfg = {"enabled": True, "max_duration_seconds": 300, "caller_name": "", "api_key": "k",
               "phone_number_id": "p", "assistant_id": "a"}
        h.calls = CallManager(Path(self.tmp.name) / "calls2.json", config_reader=lambda: cfg,
                              request=lambda *a, **k: self.fail("onaysız arama başlatıldı"))
        seen = []

        async def go():
            import asyncio
            task = asyncio.create_task(h.start_phone_call({"business_name": "Lokanta",
                                                           "phone_number": "+902121112233", "purpose": "Yer ayırt"}))
            while True:
                await asyncio.sleep(0.01)
                for ev in self.core.state.drain():
                    if ev[0] == "call_review":
                        seen.append(ev[1])
                        ev[2](False)                 # kullanıcı VAZGEÇ'e bastı
                if task.done():
                    return task.result()
        res = self.arun(go)
        self.assertEqual(seen[0]["phone_number"], "+902121112233")
        self.assertEqual(res["status"], "cancelled")

    def test_agent_blocks_stage3_tools(self):
        from jarvis.tasks import STAGE3_TOOLS
        src = (BASE / "jarvis_web" / "agent.py").read_text(encoding="utf-8")
        blocked = ast.literal_eval(src.split("AGENT_BLOCKED = ", 1)[1].split("\n\n", 1)[0].split("}", 1)[0] + "}")
        self.assertFalse(STAGE3_TOOLS - blocked)

    def test_visible_tasks_expire(self):
        from jarvis.state import State
        s = State()
        s.set_tasks([{"label": "İndirme", "status": "done"}])
        self.assertEqual(len(s.visible_tasks()), 1)
        s.task_rows_at -= 700
        self.assertEqual(s.visible_tasks(), [])
        s.set_tasks([{"label": "İndirme", "status": "running"}])
        s.task_rows_at -= 700
        self.assertEqual(len(s.visible_tasks()), 1)


class Stage4a(unittest.TestCase):
    """4a: açılışta başlatma (geçici klasörde; gerçek LaunchAgents'a yazılmaz) ve ses aygıtı değişince mikrofon +
    hoparlörün birlikte kapanıp yeniden açılması (PyAudio taklit edilir; gerçek ses aygıtı açılmaz)."""

    def test_autostart_enable_disable(self):
        import plistlib
        import tempfile
        from jarvis import autostart
        with tempfile.TemporaryDirectory() as d:
            agents, app = Path(d) / "LaunchAgents", Path(d) / "JARVIS 2.app"
            with self.assertRaises(RuntimeError):
                autostart.enable(agents, app)            # uygulama yokken kurulmaz
            (app / "Contents").mkdir(parents=True)
            (app / "Contents" / "Info.plist").write_bytes(b"")
            self.assertFalse(autostart.is_enabled(agents))
            path = Path(autostart.enable(agents, app))
            self.assertTrue(autostart.is_enabled(agents))
            data = plistlib.loads(path.read_bytes())
            self.assertEqual(data["Label"], "com.kemal.jarvis2.autostart")
            self.assertEqual(data["ProgramArguments"], ["/usr/bin/open", str(app)])
            self.assertTrue(data["RunAtLoad"])
            self.assertTrue(autostart.disable(agents))
            self.assertFalse(autostart.is_enabled(agents))
        self.assertNotIn("com.alp.jarvis", autostart.LABEL)

    def test_device_reader_works(self):
        from jarvis import audio_devices
        i, o = audio_devices.default_devices()
        self.assertTrue(i or o, "CoreAudio okunamadı")
        self.assertTrue(audio_devices.device_name(o or i))

    def test_restart_closes_all_before_reopen(self):
        import asyncio
        from jarvis import audio

        log = {"live": 0, "created_with_live": [], "writes": []}

        class FakeStream:
            def __init__(self, pa):
                self.pa = pa

            def is_active(self):
                return True

            def write(self, block):
                log["writes"].append((self.pa.n, len(block)))

            def close(self):
                pass

        class FakePA:
            count = 0

            def __init__(self):
                FakePA.count += 1
                self.n = FakePA.count
                log["created_with_live"].append(log["live"])
                log["live"] += 1

            def get_device_count(self):
                return 1

            def get_device_info_by_index(self, i):
                return {"index": 0, "name": "Taklit mikrofon", "maxInputChannels": 1, "defaultSampleRate": 16000}

            def get_default_input_device_info(self):
                return self.get_device_info_by_index(0)

            def open(self, **kw):
                return FakeStream(self)

            def terminate(self):
                log["live"] -= 1

        saved = audio.pyaudio.PyAudio
        audio.pyaudio.PyAudio = FakePA
        try:
            async def go():
                gate = audio.AudioGate()
                mic = audio.Microphone(lambda *a: None, gate)
                player = audio.Player(lambda v: None, lambda v: None, gate)
                tasks = [asyncio.create_task(mic.run()), asyncio.create_task(player.run())]
                while mic.closed or player.closed:
                    await asyncio.sleep(0.01)
                mic.last_frame = time.monotonic() + 60
                self.assertTrue(await gate.restart(mic, player))
                while mic.closed or player.closed:
                    await asyncio.sleep(0.01)
                # "sus" kuyruğu boşaltsa da yeniden açma unutulmaz; bekleyen ses yeni aygıtta çalınır
                gate.hold = True
                mic.request_restart()
                player.request_restart()
                player.interrupt()
                while not (mic.closed and player.closed):
                    await asyncio.sleep(0.01)
                gate.hold = False
                while mic.closed or player.closed:
                    await asyncio.sleep(0.01)
                player.queue.put_nowait(b"\0" * 4096)
                await asyncio.sleep(0.4)
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            asyncio.run(asyncio.wait_for(go(), 10))
        finally:
            audio.pyaudio.PyAudio = saved
        # İlk açılış: 0,1 · her yeniden açılışta biri hiç açık PyAudio yokken (PortAudio aygıtları yeniden tarar)
        self.assertEqual(log["created_with_live"], [0, 1, 0, 1, 0, 1])
        self.assertEqual(log["live"], 0)
        self.assertTrue(log["writes"] and all(n > 4 for n, _ in log["writes"]), log["writes"])


class Stage4b(unittest.TestCase):
    """4b: internetsiz komutlar (gerçek araç yerine taklit; ses okunmaz) ve "Hey Jarvis" dinleyicisinin kuralları
    (launchd'ye bir şey kurulmaz)."""

    def setUp(self):
        import asyncio
        import tempfile
        from actions import action_history
        from jarvis.live import Core
        from jarvis.state import State
        from jarvis.tasks import TaskHost
        self.tmp = tempfile.TemporaryDirectory()
        self.ah, self.ah_saved = action_history, action_history.record_other_change
        action_history.record_other_change = lambda *a: None
        self.saved = dict(tools._HANDLERS)
        self.ran, self.spoken, self.turns = [], [], []
        tools._HANDLERS["sys_info"] = (lambda a, ctx: self.ran.append(("sys_info", a)) or "Pil: %80, şarj oluyor.",
                                       "thread")
        tools._HANDLERS["open_app"] = (lambda a, ctx: self.ran.append(("open_app", a)) or "Safari açıldı.", "thread")

        class FakeHistory:
            def record_tool(self, *a):
                pass

        test = self

        class FakeCore:
            run_tool = Core.run_tool
            local_command = Core.local_command
            session = None
            response_pending = False
            mic_resume_at = 0.0
            preview = False
            player = type("P", (), {"queue": None})()

            async def _mic_command(self, text):
                return False

            async def _record_turn(self, spoken, answer):
                test.turns.append((spoken, answer))

        core = FakeCore()
        core.state = State()
        core.history = FakeHistory()
        core.offline_audio = type("O", (), {"reset": lambda self: None})()
        core.offline_busy = False
        core.tasks = TaskHost(core, store_dir=self.tmp.name)

        async def speak(text, stop=None):
            self.spoken.append(text)
        core.tasks.speak = speak
        self.core = core

    def tearDown(self):
        self.ah.record_other_change = self.ah_saved
        tools._HANDLERS.clear()
        tools._HANDLERS.update(self.saved)
        self.tmp.cleanup()

    def local(self, text):
        import asyncio

        async def go():
            self.core.tool_lock = asyncio.Lock()
            self.core.command_lock = asyncio.Lock()
            await self.core.local_command(text)
        asyncio.run(go())

    def test_local_command_runs_tool_and_speaks(self):
        self.local("Jarvis, pil durumu.")
        self.assertEqual(self.ran, [("sys_info", {"query": "battery"})])
        self.assertEqual(self.spoken, ["Pil: %80, şarj oluyor."])
        self.assertEqual(self.turns, [("Jarvis, pil durumu.", "Pil: %80, şarj oluyor.")])
        self.assertEqual(self.core.tasks.requests.pending(), [])          # kayıt kapandı
        logs = [e for e in self.core.state.drain() if e[0] == "log"]
        self.assertEqual([e[1] for e in logs], ["you", "ai"])
        self.assertFalse(self.core.offline_busy)

    def test_local_command_app_and_unknown(self):
        self.local("Safari'yi aç.")
        self.assertEqual(self.ran, [("open_app", {"app_name": "Safari"})])
        self.local("Bana bir şaka anlat.")
        self.assertEqual(len(self.ran), 1)                                 # serbest cümle araç çalıştırmaz
        self.assertIn("İnternetsiz moddayım", self.spoken[-1])

    def test_offline_prompt_covers_examples(self):
        from jarvis.live import OFFLINE_PROMPT
        from actions.offline_commands import parse_command
        for sentence in OFFLINE_PROMPT.split(": ", 1)[1].split(". "):
            if sentence.strip(". ") in ("Dosyayı bul", "Merhaba"):
                continue
            self.assertIn("tool", parse_command(sentence), sentence)

    def test_wake_phrase_and_rules(self):
        from jarvis import wake
        for text, ok in (("Hey Jarvis.", True), ("hey, jarvis!", True), ("Jarvis açıl", True),
                         ("Jarvis bugün hava nasıl?", False), ("heyecanlıyım", False), ("", False)):
            self.assertEqual(wake.is_wake_phrase(text), ok, text)
        d = wake.definition("/x/python", {})
        self.assertEqual(d["Label"], "com.kemal.jarvis2.wake")
        self.assertEqual(d["ProgramArguments"][-2:], ["-m", "jarvis.wake"])
        self.assertTrue(d["KeepAlive"])
        self.assertNotEqual(wake.LABEL, "com.jarvis.wake")                 # asıl JARVIS'in servisiyle aynı ad değil

    def test_wake_detects_running_jarvis2_by_pid(self):
        import os
        import tempfile
        from jarvis import wake
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(wake.jarvis2_running(Path(d)))
            (Path(d) / "main.lock").write_text(str(os.getpid()))
            # bu test süreci "jarvis.app" çalıştırmıyor → açık sayılmaz; kilide hiç dokunulmaz
            self.assertFalse(wake.jarvis2_running(Path(d)))
            (Path(d) / "main.lock").write_text("999999")
            self.assertFalse(wake.jarvis2_running(Path(d)))


class LiveFindings(unittest.TestCase):
    """03.10 gerçek Gemini denemesinde görülenler."""

    def test_parallel_tasks_accepts_json_strings(self):
        items = tools._json_items(['{"kind":"sys_info","query":"all"}', {"kind": "weather"}])
        self.assertEqual(items, [{"kind": "sys_info", "query": "all"}, {"kind": "weather"}])
        self.assertEqual(tools._json_items('[{"kind":"weather"}]'), [{"kind": "weather"}])

    def test_rejected_plan_is_not_uncertain(self):
        from jarvis.tasks import _nothing_started
        self.assertTrue(_nothing_started("run_parallel_tasks", '{"status": "error", "message": "x"}'))
        self.assertFalse(_nothing_started("run_parallel_tasks", '{"status": "partial", "batch_id": "b"}'))
        self.assertFalse(_nothing_started("open_app", '{"status": "error"}'))

    def test_old_finished_tasks_not_shown(self):
        import tempfile
        from jarvis.state import State
        from jarvis.tasks import TaskHost

        class C:
            pass
        c = C()
        c.state = State()
        with tempfile.TemporaryDirectory() as d:
            h = TaskHost(c, store_dir=d)
            old = [{"task_id": "b-1", "label": "Hava", "status": "done"}]
            h._show_tasks(old)                                   # açılışta eski bitmiş grup
            self.assertEqual(c.state.visible_tasks(), [])
            h._show_tasks([{"task_id": "n-1", "label": "İndirme", "status": "running"}])
            at = c.state.task_rows_at
            h._show_tasks([{"task_id": "n-1", "label": "İndirme", "status": "done"}])
            self.assertEqual(len(c.state.visible_tasks()), 1)    # bu oturumun işi bitince de görünür
            c.state.task_rows_at = at - 700
            h._show_tasks([{"task_id": "n-1", "label": "İndirme", "status": "done"}])  # 30 sn'lik yeniden bildirim
            self.assertEqual(c.state.visible_tasks(), [])        # süresi dolan satır geri gelmez

    def test_reset_clears_stuck_offline_busy(self):
        import asyncio
        from jarvis.live import Core
        src = Path(Core.__module__.replace(".", "/") + ".py")
        code = (BASE / src).read_text(encoding="utf-8")
        body = code[code.index("    def _reset_mic_buffers(self):"):code.index("    async def apply_mute")]
        self.assertIn("self.offline_busy = False", body)

        class C:
            unmute_generation = 0
            unmute_audio = offline_audio = stop_listener = type("R", (), {"reset": lambda self: None})()
            out_queue = unmute_queue = None
        c = C()
        c.offline_queue = asyncio.Queue(maxsize=1)
        c.offline_queue.put_nowait((b"", 16000))
        c.offline_busy = True
        Core._reset_mic_buffers(c)
        self.assertFalse(c.offline_busy)


class AppInstall(unittest.TestCase):
    """Applications'a kurulum: kısayol yalnız yoksa ya da bizimse yazılır; gerçek dosyanın üstüne yazılmaz."""

    def test_link_rules(self):
        import tempfile
        from jarvis import app_bundle
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            app, other = d / "A.app", d / "B.app"
            app.mkdir()
            other.mkdir()
            link = d / "kisayol.app"
            app_bundle._link(link, app)
            self.assertEqual(link.resolve(), app.resolve())
            link.unlink()
            link.symlink_to(other)
            app_bundle._link(link, app)                      # eski kısayolumuz yeni yere döner
            self.assertEqual(link.resolve(), app.resolve())
            real = d / "gercek.app"
            real.mkdir()
            app_bundle._link(real, app)                      # gerçek klasöre dokunulmaz
            self.assertFalse(real.is_symlink())

    def test_paths(self):
        from jarvis import app_bundle, autostart
        self.assertEqual(str(app_bundle.app_path()), "/Applications/JARVIS 2.app")
        self.assertEqual(autostart.contents()["ProgramArguments"][1], "/Applications/JARVIS 2.app")
        self.assertNotEqual(app_bundle.app_path().name, "JARVIS.app")   # asıl JARVIS'in yeri değil


class OrbTitle(unittest.TestCase):
    """03.10: kürenin J.A.R.V.I.S. yazısı fotoğraftaki gibi (ui/title.py); kürenin geri kalanı aynı kalmalı."""

    def test_title_only_changes_its_box(self):
        import numpy as np
        import hud_render
        from jarvis.paths import HUD_PLATE
        from jarvis.ui import title
        if not title.available():
            self.skipTest("Arial Bold yok")
        hud = hud_render.HudOrb(HUD_PLATE, bg=hud_render.BG)
        an = hud_render.HudAnimator("listening")
        an.step(0.1, "listening")
        job = an.raster_job(694)
        img = hud.render(**job)
        out = title.add_title(img, 694, job["bright"])
        a, b = np.asarray(img).astype(int), np.asarray(out).astype(int)
        mask, _, _, (x0, y0) = title._layers(694)
        diff = np.argwhere(np.abs(a - b).sum(axis=2) > 0)
        self.assertTrue(len(diff) > 0)
        self.assertTrue((diff[:, 1] >= x0).all() and (diff[:, 1] < x0 + mask.width).all())
        self.assertTrue((diff[:, 0] >= y0).all() and (diff[:, 0] < y0 + mask.height).all())
        # genişlik ≈ fotoğraftaki gibi yarıçapın ~%99'u
        cols = np.nonzero(np.asarray(mask).max(axis=0) > 128)[0]
        R = hud_render.R_FRAC * 694
        self.assertAlmostEqual((cols.max() - cols.min()) / R, 0.99, delta=0.05)
        # duraklatınca (düşük parlaklık) yazı daha sönük
        dim = np.asarray(title.add_title(img, 694, 0.3)).astype(int)
        cy, cx = y0 + mask.height // 2, x0 + mask.width // 2
        self.assertLess(dim[y0:y0 + mask.height, x0:x0 + mask.width].sum(), b[y0:y0 + mask.height, x0:x0 + mask.width].sum())


class CameraPermission(unittest.TestCase):
    """04.10: kamera izni hiç sorulmamışsa ana iş parçacığından istenir; reddedilmişse kamera hiç açılmaz ve
    ne yapılacağı söylenir. Gerçek kamera açılmaz (izin durumu taklit edilir)."""

    def make(self, status, after=None):
        import sys as _sys
        from jarvis.camera import WebcamStreamer
        w = WebcamStreamer()
        w._camera_index = staticmethod(lambda: 0)
        w._auth_status = staticmethod(lambda: status)
        self.asked = []
        if after is not None:
            w.request_auth = lambda index: self.asked.append(index) or after
        self.opened = []
        import cv2
        self.saved = cv2.VideoCapture
        class _NoCam:                                     # gerçek kamera ASLA açılmaz
            def isOpened(self):
                return False

            def release(self):
                pass
        cv2.VideoCapture = lambda *a, **k: self.opened.append(a) or _NoCam()
        self.addCleanup(lambda: setattr(cv2, "VideoCapture", self.saved))
        return w

    def test_denied_never_opens_camera(self):
        w = self.make(2)
        self.assertEqual(w.start(wait=5), "camera_denied")
        self.assertEqual(self.opened, [])

    def test_not_determined_asks_on_main_thread_then_respects_answer(self):
        w = self.make(0, after=2)
        self.assertEqual(w.start(wait=5), "camera_denied")
        self.assertEqual(self.asked, [0])
        self.assertEqual(self.opened, [])

    def test_granted_proceeds_to_open(self):
        w = self.make(0, after=3)
        w.start(wait=5)
        self.assertEqual(self.asked, [0])
        self.assertTrue(self.opened)                      # izin verilince kamera açılmaya çalışılır

    def test_messages_mention_settings(self):
        from jarvis.live import CAMERA_DENIED
        self.assertIn("Privacy & Security › Camera", CAMERA_DENIED)
        src = (BASE / "jarvis" / "tools.py").read_text(encoding="utf-8")
        self.assertIn('"camera_denied"', src)
