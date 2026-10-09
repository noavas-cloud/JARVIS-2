"""Chrome araştırma ajanı: sahte Chrome + sahte Gemini ile (gerçek tarayıcı/anahtar kullanılmaz)."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent)]

from jarvis.paths import ensure_import_path  # noqa: E402

ensure_import_path()

from actions import chrome_research as cr  # noqa: E402
from actions.chrome_cdp import ChromeGone  # noqa: E402
from jarvis.state import State  # noqa: E402

PAGES = {
    "https://ornek.org/a": {"title": "Kaynak A", "text": "Giriş. " + "x" * 7000 + " Fiyat 1200 TL. Son.",
                            "links": [{"t": "Ayrıntılar sayfası", "u": "https://ornek.org/b"}]},
    "https://ornek.org/b": {"title": "Kaynak B", "text": "B sayfası metni.", "links": []},
}


class FakeBrowser:
    def __init__(self, google_blocked=False, gone_after=None):
        self.visible = False
        self.gone = False
        self.connected = False
        self.closed = False
        self.urls = []
        self.google_blocked = google_blocked
        self.gone_after = gone_after
        self.clicks, self.typed, self.keys, self.scrolled = [], [], [], []

    async def start(self, visible=False):
        self.connected = True

    async def navigate(self, url):
        self.urls.append(url)
        if self.gone_after is not None and len(self.urls) > self.gone_after:
            self.gone = True
            raise ChromeGone("kapandı")
        if "google.com/search" in url:
            return {"title": "Google", "url": url, "text": "", "links": [], "blocked": self.google_blocked}
        if "duckduckgo" in url or "bing" in url:
            return {"title": "Sonuçlar", "url": url, "text": "", "links": []}
        page = dict(PAGES.get(url, {"title": "?", "text": "boş", "links": []}))
        page["url"] = url
        return page

    async def serp(self):
        return [{"title": "Kaynak A", "url": "https://ornek.org/a", "snippet": "fiyat"}]

    async def back(self):
        return dict(PAGES["https://ornek.org/a"], url="https://ornek.org/a")

    # 2. parça: etkileşim
    ELEMENTS = {
        1: {"id": 1, "tag": "input", "type": "search", "label": "Ara", "x": 10, "y": 10, "in_form": True,
            "search": True, "editable": True, "in_view": True},
        2: {"id": 2, "tag": "button", "type": "submit", "label": "Satın al", "x": 20, "y": 20, "in_form": True,
            "form_fields": 3, "in_view": True},
        3: {"id": 3, "tag": "input", "type": "password", "label": "Şifre", "x": 30, "y": 30, "in_form": True,
            "sensitive": True, "editable": True, "in_view": True},
        4: {"id": 4, "tag": "button", "type": "", "label": "Tümünü reddet", "x": 40, "y": 40, "in_view": True},
        5: {"id": 5, "tag": "input", "type": "text", "label": "Adınız", "x": 50, "y": 50, "in_form": True,
            "form_fields": 3, "editable": True, "in_view": True},
        6: {"id": 6, "tag": "a", "type": "", "label": "Devamını oku", "href": "https://ornek.org/b", "x": 60, "y": 60,
            "in_view": True},
    }

    async def elements(self):
        return [dict(v) for v in self.ELEMENTS.values()]

    async def target_info(self, element_id):
        return dict(self.ELEMENTS[element_id]) if element_id in self.ELEMENTS else None

    async def evaluate(self, expr):
        if expr == "location.href":
            return self.urls[-1] if self.urls else "about:blank"
        return [0, 2000, 900]

    async def read(self):
        url = self.urls[-1] if self.urls else "about:blank"
        return dict(PAGES.get(url, {"title": "?", "text": "boş", "links": []}), url=url)

    async def click_xy(self, x, y, wait=6.0):
        self.clicks.append((x, y))
        if (x, y) == (60, 60):
            self.urls.append("https://ornek.org/b")

    async def clear_focused(self):
        pass

    async def insert_text(self, text):
        self.typed.append(text)

    async def press(self, key, wait=6.0):
        self.keys.append(key)

    async def scroll(self, pages=1.0):
        self.scrolled.append(pages)

    async def screenshot(self, quality=60):
        return b"\xff\xd8JPEG"

    async def set_visible(self, on):
        self.visible = on

    async def close(self):
        self.closed = True
        self.connected = False


class ScriptBrain:
    """Sıradaki adımları sırayla verir; only_finish istenirse finish döner."""

    def __init__(self, steps, delay=0.0):
        self.steps = list(steps)
        self.delay = delay
        self.calls = []

    async def next(self, history, only_finish=False):
        self.calls.append((len(history), only_finish))
        if self.delay:
            await asyncio.sleep(self.delay)
        if only_finish:
            return "finish", {"report": "# Zorla bitti", "spoken_summary": "Süre doldu."}, ""
        if self.steps:
            return self.steps.pop(0)
        return "search", {"query": "yine ara"}, ""


GOOD = [("search", {"query": "ürün fiyatı"}, ""), ("open", {"url": "https://ornek.org/a"}, ""),
        ("find", {"text": "fiyat"}, ""), ("note", {"fact": "Fiyat 1200 TL", "source_url": "https://ornek.org/a"}, ""),
        ("finish", {"report": "# Rapor\n- Fiyat 1200 TL ([A](https://ornek.org/a))",
                    "spoken_summary": "Ürün 1200 lira."}, "")]


def make_host(tmp, brain, browser=None, key="anahtar"):
    state = State()
    told = []

    async def inform(text, spoken):
        told.append((text, spoken))
    browser = browser or FakeBrowser()
    host = cr.ResearchHost(state, inform, api_key=lambda: key, browser_factory=lambda: browser,
                           brain_factory=lambda k: brain, report_dir=Path(tmp))
    return host, state, told, browser


class AgentFlow(unittest.TestCase):
    def run_host(self, brain, browser=None, depth="deep", before_wait=None):
        with tempfile.TemporaryDirectory() as tmp:
            host, state, told, browser = make_host(tmp, brain, browser)

            async def go():
                res = host.start("Ürün fiyatı ne kadar?", depth)
                self.assertEqual(res["status"], "started")
                self.assertTrue(state.browsing)
                if before_wait:
                    await before_wait(host)
                await asyncio.wait_for(host.task, 10)
            asyncio.run(go())
            report = Path(host.job.report_path).read_text(encoding="utf-8") if host.job.report_path else ""
            return host, state, told, browser, report

    def test_full_research_writes_report_and_informs(self):
        host, state, told, browser, report = self.run_host(ScriptBrain(GOOD))
        job = host.job
        self.assertEqual(job.status, "done")
        self.assertEqual(job.notes, [("Fiyat 1200 TL", "https://ornek.org/a")])
        self.assertEqual(job.visited, [("Kaynak A", "https://ornek.org/a")])
        self.assertIn("Fiyat 1200 TL", report)
        self.assertIn("Ziyaret edilen sayfalar", report)
        self.assertEqual(len(told), 1)
        self.assertIn("[ARAŞTIRMA BİTTİ]", told[0][0])
        self.assertEqual(told[0][1], "Ürün 1200 lira.")
        self.assertFalse(state.browsing)
        self.assertTrue(browser.closed, "gizli Chrome araştırma bitince kapanmalı")
        self.assertEqual(state.task_rows[0]["status"], "done")

    def test_visible_browser_stays_open(self):
        browser = FakeBrowser()

        async def show(host):
            await asyncio.sleep(0)
            await host.set_visible(True)
        _, state, _, browser, _ = self.run_host(ScriptBrain(GOOD), browser, before_wait=show)
        self.assertFalse(browser.closed, "kullanıcı izliyorsa Chrome açık kalmalı")
        self.assertTrue(state.browser_visible)

    def test_google_block_falls_back_to_duckduckgo(self):
        _, _, _, browser, _ = self.run_host(ScriptBrain(GOOD), FakeBrowser(google_blocked=True))
        self.assertTrue(any("duckduckgo" in u for u in browser.urls))

    def test_google_is_skipped_after_one_block(self):
        steps = [GOOD[0], ("search", {"query": "ikinci arama"}, "")] + GOOD[-1:]
        _, _, _, browser, _ = self.run_host(ScriptBrain(steps), FakeBrowser(google_blocked=True))
        self.assertEqual(sum("google.com" in u for u in browser.urls), 1, "Google yalnız bir kez denenmeli")

    def test_local_and_file_urls_are_refused(self):
        steps = [("open", {"url": u}, "") for u in ("file:///etc/passwd", "http://127.0.0.1:8765/x",
                                                    "http://192.168.1.1/", "javascript:alert(1)")] + GOOD[-1:]
        _, _, _, browser, _ = self.run_host(ScriptBrain(steps))
        self.assertEqual([u for u in browser.urls if not u.startswith("data:")], [])
        self.assertTrue(browser.urls[0].startswith("data:text/html"), "önce bekleme sayfası açılmalı")

    def test_step_limit_forces_finish(self):
        brain = ScriptBrain([])                    # hep arar, kendiliğinden bitirmez
        host, _, _, _, report = self.run_host(brain, depth="quick")
        self.assertEqual(host.job.status, "done")
        self.assertTrue(brain.calls[-1][1], "sınırda yalnız finish istenmeli")
        self.assertLessEqual(host.job.step, cr.LIMITS["quick"][0])
        self.assertIn("Zorla bitti", report)

    def test_chrome_closed_by_user_stops_with_partial_report(self):
        steps = [GOOD[0], ("note", {"fact": "Ara bulgu", "source_url": "https://ornek.org/a"}, ""),
                 ("open", {"url": "https://ornek.org/a"}, "")]
        host, state, told, _, report = self.run_host(ScriptBrain(steps), FakeBrowser(gone_after=2))   # 1. açılış bekleme sayfası
        self.assertEqual(host.job.status, "cancelled")
        self.assertIn("Chrome kapatıldığı", report)
        self.assertIn("Ara bulgu", report)
        self.assertIn("DURDU", told[0][0])
        self.assertFalse(state.browsing)

    def test_stop_cancels_and_keeps_notes(self):
        steps = [("note", {"fact": "Erken bulgu", "source_url": "https://ornek.org/a"}, "")]

        async def stop_soon(host):
            await asyncio.sleep(0.15)
            self.assertEqual(host.stop()["status"], "stopping")
        host, _, _, _, report = self.run_host(ScriptBrain(steps, delay=0.05), before_wait=stop_soon)
        self.assertEqual(host.job.status, "cancelled")
        self.assertIn("Erken bulgu", report)

    def test_page_text_is_chunked_and_read_more_continues(self):
        browser = FakeBrowser()
        job = cr.ResearchJob("soru")
        agent = cr.ResearchAgent(job, browser, None)

        async def go():
            first = await agent._tool("open", {"url": "https://ornek.org/a"})
            second = await agent._tool("read_more", {})
            return first, second
        first, second = asyncio.run(go())
        self.assertEqual(len(first["text"]), cr.CHUNK)
        self.assertTrue(first["more"])
        self.assertIn("1200 TL", second["text"])
        self.assertFalse(second["more"])

    def test_old_page_text_is_trimmed(self):
        agent = cr.ResearchAgent(cr.ResearchJob("soru"), None, None)
        agent.history = [{"role": "tool", "name": "open", "result": {"text": "y" * 5000}} for _ in range(7)]
        agent._trim_history()
        sizes = [len(h["result"]["text"]) for h in agent.history]
        self.assertTrue(all(s < 900 for s in sizes[:3]))
        self.assertTrue(all(s == 5000 for s in sizes[-4:]))


class FlakyClient:
    """GeminiBrain için sahte istemci: önce verilen hataları fırlatır, sonra bir araç çağrısı döner."""

    def __init__(self, fails):
        self.fails = list(fails)
        self.models_seen = []
        outer = self

        class Models:
            async def generate_content(self, model, contents, config):
                outer.models_seen.append(model)
                outer.contents = contents
                if outer.fails:
                    raise outer.fails.pop(0)

                class Call:
                    name, args = "search", {"query": "x"}

                class Resp:
                    function_calls = [Call()]
                    candidates = []
                    text = ""
                return Resp()

        class Aio:
            models = Models()
        self.aio = Aio()


class BrainErrors(unittest.TestCase):
    def brain(self, fails):
        from google.genai import errors
        b = cr.GeminiBrain.__new__(cr.GeminiBrain)
        b.client, b.models, b.model_used = FlakyClient(fails), ["m1", "m2"], ""
        cr._COOLDOWN.clear()
        return b, errors

    def setUp(self):
        self._sleep = cr.asyncio.sleep

        async def no_wait(_):
            return None
        cr.asyncio.sleep = no_wait

    def tearDown(self):
        cr.asyncio.sleep = self._sleep

    def test_server_error_is_retried_then_next_model(self):
        from google.genai import errors
        err = errors.ServerError(500, {"error": {"code": 500, "message": "Internal", "status": "INTERNAL"}})
        b, _ = self.brain([err, err])
        name, args, text, raw = asyncio.run(b.next([{"role": "user", "text": "soru"}]))
        self.assertEqual(name, "search")
        self.assertEqual(b.client.models_seen, ["m1", "m1", "m2"], "yoğun modelde yalnız bir kez yeniden dene")
        self.assertEqual(b.model_used, "m2")
        b.model_used = ""                                   # yeni araştırma: m1 hâlâ beklemede → doğrudan m2
        asyncio.run(b.next([{"role": "user", "text": "soru"}]))
        self.assertEqual(b.client.models_seen[-1], "m2")
        self.assertEqual(b.client.models_seen.count("m1"), 2)

    def test_quota_switches_model_immediately(self):
        from google.genai import errors
        err = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        b, _ = self.brain([err])
        asyncio.run(b.next([{"role": "user", "text": "soru"}]))
        self.assertEqual(b.client.models_seen, ["m1", "m2"])

    def test_missing_model_skips_to_next(self):
        from google.genai import errors
        err = errors.ClientError(404, {"error": {"code": 404, "message": "models/m1 is not found", "status": "NOT_FOUND"}})
        b, _ = self.brain([err])
        self.assertEqual(asyncio.run(b.next([{"role": "user", "text": "soru"}]))[0], "search")
        self.assertEqual(b.client.models_seen, ["m1", "m2"])

    def test_all_failing_gives_readable_error(self):
        from google.genai import errors
        err = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        b, _ = self.brain([err] * 8)
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(b.next([{"role": "user", "text": "soru"}]))
        self.assertIn("Gemini yanıt vermedi", str(ctx.exception))

    def test_model_reply_is_sent_back_unchanged(self):
        marker = object()
        b, _ = self.brain([])
        asyncio.run(b.next([{"role": "user", "text": "soru"},
                            {"role": "model", "name": "search", "args": {}, "raw": marker},
                            {"role": "tool", "name": "search", "result": {"results": []}}]))
        self.assertIs(b.client.contents[1], marker, "düşünce imzası kaybolmasın")

    def test_no_empty_object_schemas(self):
        for d in cr._declarations():
            if "parameters" in d:
                self.assertTrue(d["parameters"]["properties"], d["name"])


class Interaction(unittest.TestCase):
    """Tıklama/yazma güvenliği: riskli işlem onaysız yapılmaz, gizli alana ve kart numarası asla yazılmaz."""

    def agent(self, answer=None):
        asked = []

        async def confirm(title, text):
            asked.append(text)
            return answer
        browser = FakeBrowser()
        agent = cr.ResearchAgent(cr.ResearchJob("soru"), browser, None,
                                 confirm=None if answer is None else confirm)
        return agent, browser, asked

    def test_risky_click_needs_and_respects_confirmation(self):
        for answer, clicked in ((False, False), (True, True)):
            agent, browser, asked = self.agent(answer)
            res = asyncio.run(agent._tool("click", {"element_id": 2}))
            self.assertEqual(len(asked), 1)
            self.assertIn("Satın al", asked[0])
            self.assertEqual(bool(browser.clicks), clicked, res)

    def test_without_confirm_callback_risky_click_is_refused(self):
        agent, browser, _ = self.agent(None)
        res = asyncio.run(agent._tool("click", {"element_id": 2}))
        self.assertIn("error", res)
        self.assertEqual(browser.clicks, [])

    def test_cookie_reject_and_plain_link_need_no_confirmation(self):
        agent, browser, asked = self.agent(False)
        asyncio.run(agent._tool("click", {"element_id": 4}))
        res = asyncio.run(agent._tool("click", {"element_id": 6}))
        self.assertEqual(asked, [])
        self.assertEqual(len(browser.clicks), 2)
        self.assertTrue(res["page_changed"])
        self.assertEqual(agent.job.visited[-1][1], "https://ornek.org/b")

    def test_never_types_into_password_or_card_number(self):
        agent, browser, asked = self.agent(True)
        r1 = asyncio.run(agent._tool("type", {"element_id": 3, "text": "gizli123"}))
        r2 = asyncio.run(agent._tool("type", {"element_id": 1, "text": "4111 1111 1111 1111"}))
        self.assertIn("error", r1)
        self.assertIn("error", r2)
        self.assertEqual(browser.typed, [])
        self.assertEqual(asked, [], "onay sorulmadan doğrudan reddedilmeli")

    def test_search_box_submit_needs_no_confirmation(self):
        agent, browser, asked = self.agent(False)
        asyncio.run(agent._tool("type", {"element_id": 1, "text": "süphan dağı", "submit": True}))
        self.assertEqual(browser.typed, ["süphan dağı"])
        self.assertEqual(browser.keys, ["Enter"])
        self.assertEqual(asked, [])

    def test_other_form_submit_needs_confirmation(self):
        agent, browser, asked = self.agent(False)
        res = asyncio.run(agent._tool("type", {"element_id": 5, "text": "Deneme", "submit": True}))
        self.assertEqual(len(asked), 1)
        self.assertIn("error", res)
        self.assertEqual(browser.keys, [])
        agent2, browser2, asked2 = self.agent(False)
        asyncio.run(agent2._tool("type", {"element_id": 5, "text": "Deneme"}))     # göndermeden yazmak serbest
        self.assertEqual(browser2.typed, ["Deneme"])
        self.assertEqual(asked2, [])

    def test_elements_marks_sensitive_fields(self):
        agent, _, _ = self.agent(None)
        res = asyncio.run(agent._tool("elements", {}))
        sens = [e for e in res["elements"] if e.get("sensitive")]
        self.assertEqual([e["id"] for e in sens], [3])

    def test_look_attaches_only_latest_screenshot(self):
        steps = [("look", {}, ""), ("scroll", {"direction": "down"}, ""), ("look", {}, "")] + GOOD[-1:]
        brain = ScriptBrain(steps)
        job = cr.ResearchJob("soru", "quick")
        agent = cr.ResearchAgent(job, FakeBrowser(), brain)
        asyncio.run(agent.run())
        images = [h for h in agent.history if h.get("image")]
        self.assertEqual(len(images), 1)
        self.assertIs(agent.history[-2], images[0], "yalnız en son look görüntüsü kalmalı")

    def test_image_is_sent_to_gemini(self):
        b = cr.GeminiBrain.__new__(cr.GeminiBrain)
        b.client, b.models, b.model_used = FlakyClient([]), ["m1"], ""
        cr._COOLDOWN.clear()
        asyncio.run(b.next([{"role": "user", "text": "soru"}, {"role": "model", "name": "look", "args": {}},
                            {"role": "tool", "name": "look", "result": {"ok": True}, "image": b"\xff\xd8x"}]))
        parts = b.client.contents[2].parts
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[1].inline_data.mime_type, "image/jpeg")

    def test_confirm_rules(self):
        self.assertTrue(cr.click_needs_confirm({"label": "Siparişi tamamla"}))
        self.assertTrue(cr.click_needs_confirm({"label": "Place order"}))
        self.assertTrue(cr.click_needs_confirm({"label": "Kabul et"}))
        self.assertFalse(cr.click_needs_confirm({"label": "Tümünü reddet"}))
        self.assertFalse(cr.click_needs_confirm({"label": "Accept only necessary"}), "reddet/gerekli önce bakılır")
        self.assertFalse(cr.click_needs_confirm({"label": "Sonraki sayfa", "tag": "a"}))
        self.assertTrue(cr.click_needs_confirm({"label": "Devam", "tag": "button", "in_form": True,
                                                "form_has_password": True}))


class HelpAndViewer(unittest.TestCase):
    def test_ask_user_opens_viewer_and_waits_for_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, state, told, browser = make_host(tmp, ScriptBrain(GOOD))
            host.browser = browser
            browser.connected = True

            async def go():
                waiter = asyncio.get_running_loop().create_task(host._ask_user("Robot doğrulamasını geç"))
                await asyncio.sleep(0.05)
                self.assertTrue(state.browser_visible, "yardım isteyince pencere açılmalı")
                self.assertEqual(host.help[0], "Robot doğrulamasını geç")
                host.answer_help(True)
                return await waiter
            self.assertTrue(asyncio.run(go()))
            self.assertIsNone(host.help)
            self.assertIn("YARDIM", told[0][0])

    def test_viewer_frames_only_while_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, state, _, browser = make_host(tmp, ScriptBrain(GOOD))
            host.browser = browser
            browser.connected = True

            async def go():
                await host.set_visible(True)
                await asyncio.sleep(0.05)
                got = host.frame_id
                await host.set_visible(False)
                await asyncio.sleep(0.4)
                return got, host.frame_id
            got, after = asyncio.run(go())
            self.assertGreaterEqual(got, 1)
            self.assertEqual(got, after, "pencere kapalıyken görüntü alınmamalı")
            self.assertTrue(browser.closed, "araştırma yokken pencere kapanınca Chrome da kapanır")

    def test_viewer_click_maps_fraction_to_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, _, _, browser = make_host(tmp, ScriptBrain(GOOD))
            host.browser = browser
            browser.connected = True
            asyncio.run(host.viewer_click(0.5, 0.25))
            self.assertEqual(browser.clicks, [(640.0, 225.0)])

    def test_shutdown_terminates_chrome_process(self):
        class Proc:
            terminated = False

            def poll(self):
                return None

            def terminate(self):
                Proc.terminated = True
        with tempfile.TemporaryDirectory() as tmp:
            host, _, _, browser = make_host(tmp, ScriptBrain(GOOD))
            host.browser = browser
            browser.proc = Proc()
            host.shutdown()
            self.assertTrue(Proc.terminated)


class HostCommands(unittest.TestCase):
    def test_error_detail_goes_to_report(self):
        class Broken:
            async def next(self, history, only_finish=False):
                raise RuntimeError("Gemini yanıt vermedi: m1: 500 Internal")
        with tempfile.TemporaryDirectory() as tmp:
            host, _, told, _ = make_host(tmp, Broken())

            async def go():
                host.start("soru", "quick")
                await asyncio.wait_for(host.task, 10)
            asyncio.run(go())
            report = Path(host.job.report_path).read_text(encoding="utf-8")
            self.assertEqual(host.job.status, "error")
            self.assertIn("Gemini sunucusu yanıt vermediği", report)
            self.assertIn("500 Internal", report)
            self.assertIn("YARIDA KALDI", told[0][0])

    def test_needs_question_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD), key="")

            async def go():
                return host.start("", "deep"), host.start("soru", "deep")
            empty, nokey = asyncio.run(go())
            self.assertEqual(empty["status"], "error")
            self.assertIn("anahtar", nokey["message"])

    def test_busy_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD, delay=0.05))

            async def go():
                host.start("bir", "quick")
                second = host.start("iki", "quick")
                await asyncio.wait_for(host.task, 10)
                return second
            self.assertEqual(asyncio.run(go())["status"], "busy")

    def test_show_without_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD))
            self.assertEqual(asyncio.run(host.toggle_visible())["status"], "not_running")
            self.assertEqual(host.status()["status"], "none")
            self.assertEqual(host.stop()["status"], "not_running")

    def test_tool_handler_routes_actions(self):
        from jarvis import tools
        with tempfile.TemporaryDirectory() as tmp:
            host, *_ = make_host(tmp, ScriptBrain(GOOD))

            class Ctx:
                research = host

            async def go():
                started = await tools.run("chrome_research", {"action": "start", "question": "soru"}, Ctx)
                await asyncio.wait_for(host.task, 10)
                status = await tools.run("chrome_research", {"action": "status"}, Ctx)
                bad = await tools.run("chrome_research", {"action": "uç"}, Ctx)
                return started, status, bad
            started, status, bad = asyncio.run(go())
            self.assertEqual(started["status"], "started")
            self.assertEqual(status["status"], "done")
            self.assertIn("Fiyat", status["report"])
            self.assertTrue(tools.is_error(bad, "chrome_research"))


class OrbAndSafety(unittest.TestCase):
    def test_orb_shows_chrome_browsing(self):
        from jarvis.ui.orb import OrbView
        s = State(status="LISTENING", cloud=True)
        self.assertEqual(OrbView.status_text(s), "DİNLİYOR")
        s.browsing = True
        self.assertEqual(OrbView.mode(s), "search")
        self.assertEqual(OrbView.status_text(s), "CHROME'DA GEZİNİYOR")
        s.speaking = True
        self.assertEqual(OrbView.mode(s), "speaking", "JARVIS konuşurken konuşma hâli öncelikli")

    def test_cdp_module_never_reads_cookies_or_submits_by_script(self):
        # 2. parça: tıklama/yazma gerçek fare/klavye olaylarıyla (Input.*) yapılır ve kararı chrome_research verir;
        # Chrome katmanı çerez okumaz, sayfa içinden form göndermez/tıklamaz, indirmeleri kapatır.
        src = (HERE.parent / "actions" / "chrome_cdp.py").read_text(encoding="utf-8")
        for forbidden in (".click()", ".submit()", "requestSubmit", "Network.getCookies", "Storage.getCookies",
                          "Network.getAllCookies", '"windowState": "minimized"'):   # görünmez Chrome'da çizim durur
            self.assertNotIn(forbidden, src)
        self.assertIn('"behavior": "deny"', src)

    def test_phone_agent_forwards_chrome_research_to_desktop(self):
        # 3. parça (05.10): telefon ajanı Chrome araştırmasını kendisi çalıştırmaz, masaüstü JARVIS 2'ye iletir.
        from jarvis_web.agent import AGENT_BLOCKED, DESKTOP_FORWARD
        self.assertNotIn("chrome_research", AGENT_BLOCKED)
        self.assertIn("chrome_research", DESKTOP_FORWARD)


if __name__ == "__main__":
    unittest.main()
