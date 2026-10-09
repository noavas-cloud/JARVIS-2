"""Sesli konuşma modeli seçimi: yeni (önizleme) model, bağlanamazsa eski modele düşme, ayardan zorlama."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import live  # noqa: E402


class FakeCore:
    _live_model = live.Core._live_model
    _note_live_failure = live.Core._note_live_failure

    def __init__(self):
        self.live_fallback, self._live_failures, self.resume_handle = False, 0, "eski-anahtar"


class LiveModelChoice(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.object(live, "get_app_config_value", lambda key, default=None: default)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_new_model_is_default(self):
        self.assertEqual(FakeCore()._live_model(), "models/gemini-3.1-flash-live-preview")

    def test_two_short_failures_fall_back_once(self):
        c = FakeCore()
        self.assertFalse(c._note_live_failure(live.DEFAULT_MODEL, True))
        self.assertTrue(c._note_live_failure(live.DEFAULT_MODEL, True))
        self.assertEqual(c._live_model(), live.FALLBACK_MODEL)
        self.assertIsNone(c.resume_handle)
        self.assertFalse(c._note_live_failure(live.FALLBACK_MODEL, True), "eski modelde tekrar geçiş olmaz")

    def test_long_session_resets_failure_count(self):
        c = FakeCore()
        c._note_live_failure(live.DEFAULT_MODEL, True)
        c._note_live_failure(live.DEFAULT_MODEL, False)     # uzun, sağlıklı oturum sonra koptu (ör. ağ)
        self.assertFalse(c._note_live_failure(live.DEFAULT_MODEL, True))
        self.assertEqual(c._live_model(), live.DEFAULT_MODEL)

    def test_setting_overrides(self):
        self.patch.stop()
        with mock.patch.object(live, "get_app_config_value",
                               lambda key, default=None: "models/ozel" if key == "live_model" else default):
            self.assertEqual(FakeCore()._live_model(), "models/ozel")
        self.patch.start()


class FrameSending(unittest.TestCase):
    def test_frames_use_video_field_not_deprecated_media(self):
        import asyncio
        sent = []

        class Session:
            async def send_realtime_input(self, **kw):
                sent.append(kw)

        class State:
            paused = False

        class C:
            _send_frames = live.Core._send_frames

        c = C()
        c.session, c.state = Session(), State()

        async def go():
            task = asyncio.create_task(c._send_frames(lambda: b"\xff\xd8jpeg", lambda: True))
            await asyncio.sleep(0.05)
            task.cancel()
        asyncio.run(go())
        self.assertTrue(sent)
        self.assertNotIn("media", sent[0])
        self.assertEqual(sent[0]["video"].mime_type, "image/jpeg")

    def test_no_deprecated_media_calls_left(self):
        src = (Path(__file__).resolve().parent.parent / "jarvis" / "live.py").read_text(encoding="utf-8")
        self.assertNotIn("send_realtime_input(media=", src)


class NewModelCompatibility(unittest.TestCase):
    def test_no_client_content_mid_session(self):
        root = Path(__file__).resolve().parent.parent / "jarvis"
        for name in ("live.py", "tasks.py"):
            code = "\n".join(l for l in (root / name).read_text(encoding="utf-8").splitlines()
                             if not l.strip().startswith("#") and "send_client_content yalnız" not in l)
            self.assertNotIn("send_client_content(", code, name)

    def test_send_text_uses_realtime_input(self):
        import asyncio
        sent = []

        class S:
            async def send_realtime_input(self, **kw):
                sent.append(kw)
        asyncio.run(live.send_text(S(), "merhaba"))
        self.assertEqual(sent, [{"text": "merhaba"}])

    def test_thinking_setting_per_model(self):
        new_off = live.thinking_config(live.DEFAULT_MODEL, 0)
        new_on = live.thinking_config(live.DEFAULT_MODEL, 1024)
        old_on = live.thinking_config(live.FALLBACK_MODEL, 1024)
        self.assertEqual(new_off.thinking_level, live.types.ThinkingLevel.MINIMAL)
        self.assertEqual(new_on.thinking_level, live.types.ThinkingLevel.MEDIUM)
        self.assertIsNone(new_on.thinking_budget)
        self.assertEqual(old_on.thinking_budget, 1024)
        self.assertIsNone(old_on.thinking_level)


class SessionRotation(unittest.TestCase):
    """Yeni modelin ~170 sn kopması: boştayken önceden yenileme, meşgulken beklemek, kopmayı tanımak."""

    def core(self, idle=True):
        import asyncio as aio

        class Player:
            queue = aio.Queue()

        class Tasks:
            def idle(self, need_session=True):
                return idle

        class C:
            _can_rotate = live.Core._can_rotate
            _rotate_session = live.Core._rotate_session

        c = C()
        c.state = live.State(status="LISTENING")
        c.response_pending, c.go_away, c.player, c.tasks = False, False, Player(), Tasks()
        return c

    def run_rotation(self, c, after=0.05, deadline=0.3):
        import asyncio as aio
        old = live.ROTATE_AFTER_S, live.ROTATE_DEADLINE_S
        live.ROTATE_AFTER_S, live.ROTATE_DEADLINE_S = after, deadline
        try:
            async def go():
                await c._rotate_session(live.time.monotonic())
            try:
                aio.run(go())
                return False
            except ConnectionResetError:
                return True
        finally:
            live.ROTATE_AFTER_S, live.ROTATE_DEADLINE_S = old

    def test_rotates_when_idle(self):
        c = self.core(idle=True)
        self.assertTrue(self.run_rotation(c))
        self.assertTrue(c.go_away, "devam anahtarıyla hızlı yeniden bağlanma yolu")

    def test_waits_while_busy_and_gives_up_at_deadline(self):
        c = self.core(idle=False)
        self.assertFalse(self.run_rotation(c))
        self.assertFalse(c.go_away)
        c2 = self.core(idle=True)
        c2.state.speaking = True
        self.assertFalse(self.run_rotation(c2), "JARVIS konuşurken yenilenmez")

    def test_recognizes_known_disconnect(self):
        self.assertTrue(live.is_server_recycle(Exception("1008 None. The operation was aborted.")))
        self.assertFalse(live.is_server_recycle(Exception("1007 None. media_chunks is deprecated")))
        self.assertFalse(live.is_server_recycle(Exception("1011 Internal error")))

    def test_only_new_model_rotates(self):
        src = (Path(__file__).resolve().parent.parent / "jarvis" / "live.py").read_text(encoding="utf-8")
        self.assertIn("if not is_legacy_model(model):\n                            tg.create_task(self._rotate_session(started))", src)


if __name__ == "__main__":
    unittest.main()
