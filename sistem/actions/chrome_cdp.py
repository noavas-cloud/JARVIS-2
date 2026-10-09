"""JARVIS'in kendi Chrome'u: ayrı profil + Chrome DevTools Protokolü (CDP).

* Kullanıcının normal Chrome profiline dokunulmaz. Profil: ~/Library/Application Support/JARVIS 2/Chrome.
* 2. parça (04.10.2026): Chrome GÖRÜNMEZ ("headless") çalışır. Neden: görünür Chrome simge durumundayken ya da macOS ile
  gizlenmişken sayfayı çizmeyi durduruyor → tıklama sayfaya ulaşmıyor, ekran görüntüsü alınamıyor (gerçek Chrome'da
  ölçüldü); ekran dışına taşımada ise macOS en az 40 px bırakıyor. Kullanıcı izlemek isterse JARVIS içindeki
  "JARVIS Chrome" penceresi (jarvis/ui/browser_view.py) canlı ekran görüntüsünü gösterir ve tıklama/yazmayı buraya iletir.
  ⚠ Görünmez Chrome'da pencereyi küçültme (Browser.setWindowBounds minimized) → ekran görüntüsü takılır.
* Denetim portu yalnız 127.0.0.1'de ve rastgele (--remote-debugging-port=0 → DevToolsActivePort dosyası).
* İndirmeler kapalı (Browser.setDownloadBehavior deny). Çerez/şifre okunmaz. Bu modül neye tıklanacağına karar vermez:
  güvenlik kararları (onay, şifre/kart alanı yasağı) actions/chrome_research.py'dedir.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import subprocess
from pathlib import Path

CHROME_APP = Path("/Applications/Google Chrome.app")
PROFILE_DIR = Path.home() / "Library" / "Application Support" / "JARVIS 2" / "Chrome"
VIEWPORT = (1280, 900)


class ChromeError(RuntimeError):
    pass


class ChromeGone(ChromeError):
    """JARVIS Chrome'u kapandı (çöktü ya da JARVIS kapatırken sonlandırıldı)."""


# Sayfanın okunur metni + bağlantıları. Gezinme/menü/altbilgi atılır; makale/ana bölüm varsa o tercih edilir.
READ_JS = r"""
(() => {
  const pick = document.querySelector('article') || document.querySelector('main') || document.body;
  if (!pick) return {title: document.title, url: location.href, text: '', links: []};
  const clone = pick.cloneNode(true);
  clone.querySelectorAll('script,style,noscript,svg,iframe,nav,footer,header,aside,form,[aria-hidden="true"],'
    + '[role="navigation"],[role="banner"],[role="contentinfo"],.cookie,.cookies,#cookie,.consent').forEach(e => e.remove());
  // innerText yalnız sayfaya bağlı (çizilen) öğede satır/hücre aralarını korur → kopya görünmez bir kutuda çizilir.
  const box = document.createElement('div');
  box.style.cssText = 'position:absolute;left:-100000px;top:0;width:1100px;';
  box.appendChild(clone); document.body.appendChild(box);
  let text = (clone.innerText || clone.textContent || '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
  box.remove();
  if (text.length < 400 && pick !== document.body) {
    text = (document.body.innerText || '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
  }
  const seen = new Set(); const links = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const u = a.href; const t = (a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!u.startsWith('http') || !t || t.length < 3 || seen.has(u)) continue;
    seen.add(u); links.push({t: t.slice(0, 120), u: u.slice(0, 400)});
    if (links.length >= 150) break;
  }
  const blocked = /\/sorry\/|captcha|recaptcha|cf-chl|challenge-platform/i.test(location.href + ' ' +
    Array.from(document.querySelectorAll('iframe')).map(f => f.src).join(' ')) ||
    /unusual traffic|olağan dışı trafik|robot olmadığınızı|verify you are human|are you a robot/i.test(text.slice(0, 3000));
  const consent = /consent\.(google|youtube)\.com/.test(location.host);
  return {title: document.title, url: location.href, text: text.slice(0, 60000), links, blocked, consent};
})()
"""

# Arama motoru sonuç sayfasından başlık/adres/özet (Google, Bing, DuckDuckGo; olmazsa genel bağlantılar).
SERP_JS = r"""
(() => {
  const out = []; const seen = new Set();
  const add = (a, title, snip) => {
    if (!a) return; let u = a.href || '';
    try { const p = new URL(u); if (p.hostname.endsWith('duckduckgo.com') && p.searchParams.get('uddg')) u = p.searchParams.get('uddg'); } catch (e) {}
    if (!u.startsWith('http') || seen.has(u) || /google\.[a-z.]+\/(search|url|aclk)|bing\.com\/(search|ck)|webcache|translate\.google/.test(u)) return;
    seen.add(u); out.push({title: (title || '').trim().slice(0, 200), url: u.slice(0, 500), snippet: (snip || '').replace(/\s+/g, ' ').trim().slice(0, 320)});
  };
  document.querySelectorAll('#search a h3, #rso a h3').forEach(h => {
    const a = h.closest('a'); const box = h.closest('div.g, div[data-hveid], div.MjjYud') || a.parentElement;
    add(a, h.innerText, box ? box.innerText.replace(h.innerText, '') : '');
  });
  document.querySelectorAll('li.b_algo').forEach(li => { const a = li.querySelector('h2 a'); add(a, a && a.innerText, (li.querySelector('.b_caption p, p') || {}).innerText); });
  document.querySelectorAll('.result, .results_links').forEach(r => { const a = r.querySelector('a.result__a'); add(a, a && a.innerText, (r.querySelector('.result__snippet') || {}).innerText); });
  if (!out.length) document.querySelectorAll('a[href] h2, a[href] h3').forEach(h => add(h.closest('a'), h.innerText, ''));
  return out.slice(0, 12);
})()
"""



# Görünen etkileşimli öğeler: her birine data-jarvis-id verilir (her çağrıda yeniden numaralanır).
ELEMENTS_JS = r"""
(() => {
  document.querySelectorAll('[data-jarvis-id]').forEach(e => e.removeAttribute('data-jarvis-id'));
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],' +
              '[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox],[role=searchbox],[contenteditable=true]';
  const sens = /pass|şifre|sifre|parola|card|kart|cvv|cvc|ccv|iban|expiry|son kullanma|tc ?kimlik|kimlik no|ssn|passport|pasaport|otp|doğrulama kodu|one-time/i;
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    if (e.type === 'hidden' || e.disabled) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > innerHeight * 2 || r.right < 0 || r.left > innerWidth) continue;
    const st = getComputedStyle(e);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    const id = out.length + 1; e.setAttribute('data-jarvis-id', id);
    let label = (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder || e.title || e.alt || e.name || '');
    if (!label && e.id) { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) label = l.innerText; }
    label = label.replace(/\s+/g, ' ').trim().slice(0, 90);
    const ac = (e.getAttribute('autocomplete') || '');
    const meta = [e.type, e.name, e.id, e.placeholder, ac, e.getAttribute('aria-label')].join(' ');
    out.push({id, tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || e.getAttribute('role') || '').toLowerCase(),
              label, href: (e.href || '').slice(0, 160), in_view: r.top >= 0 && r.bottom <= innerHeight,
              sensitive: e.type === 'password' || /^cc-|password|one-time-code/.test(ac) || sens.test(meta)});
    if (out.length >= 90) break;
  }
  return out;
})()
"""

# Bir öğeyi görünür alana getirip merkezini ve bağlamını döndürür (tıklama/yazma kararı için).
TARGET_JS = r"""
((id) => {
  const e = document.querySelector('[data-jarvis-id="' + id + '"]');
  if (!e) return null;
  e.scrollIntoView({block: 'center', inline: 'center'});
  if (e.tagName === 'A') e.removeAttribute('target');          // yeni sekme açılmasın, aynı sekmede kalsın
  const r = e.getBoundingClientRect(); const f = e.form || e.closest('form');
  const sens = /pass|şifre|sifre|parola|card|kart|cvv|cvc|ccv|iban|expiry|tc ?kimlik|kimlik no|ssn|passport|pasaport|otp|one-time/i;
  const ac = (e.getAttribute('autocomplete') || '');
  const meta = [e.type, e.name, e.id, e.placeholder, ac, e.getAttribute('aria-label')].join(' ');
  let search = false, formPass = false, formFields = 0;
  if (f) {
    search = f.getAttribute('role') === 'search' || !!f.querySelector('input[type=search]') ||
             /search|ara|query|q$/i.test((f.action || '') + ' ' + (f.id || '') + ' ' + (f.className || ''));
    formPass = !!f.querySelector('input[type=password]');
    formFields = f.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]),textarea,select').length;
  }
  if (!f && (e.type === 'search' || e.getAttribute('role') === 'searchbox' || /^(q|query|search|s|k|keyword)$/i.test(e.name || ''))) search = true;
  const label = (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder || e.title || e.name || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, tag: e.tagName.toLowerCase(),
          type: (e.getAttribute('type') || e.getAttribute('role') || '').toLowerCase(), label,
          href: (e.href || '').slice(0, 300), in_form: !!f, search, form_has_password: formPass, form_fields: formFields,
          sensitive: e.type === 'password' || /^cc-|password|one-time-code/.test(ac) || sens.test(meta),
          editable: e.isContentEditable || ['input', 'textarea'].includes(e.tagName.toLowerCase())};
})
"""

KEYS = {"Enter": (13, "\r"), "Tab": (9, ""), "Backspace": (8, ""), "Escape": (27, ""), "ArrowDown": (40, ""),
        "ArrowUp": (38, ""), "ArrowLeft": (37, ""), "ArrowRight": (39, ""), "Delete": (46, ""), "PageDown": (34, ""),
        "PageUp": (33, ""), "Home": (36, ""), "End": (35, "")}


class ChromeBrowser:
    """Tek sekmeli CDP istemcisi (görünmez Chrome). Tüm yöntemler aynı asyncio döngüsünde çağrılmalı."""

    def __init__(self, profile_dir: Path | None = None, chrome_app: Path | None = None, launch=None):
        self.profile_dir = Path(profile_dir or PROFILE_DIR)
        self.chrome_app = Path(chrome_app or CHROME_APP)
        self._launch = launch or self._launch_chrome
        self.ws = None
        self._reader: asyncio.Task | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._waiters: list[tuple[str, str | None, asyncio.Future]] = []
        self.session: str | None = None
        self.target: str | None = None
        self.visible = False            # yalnız bilgi: kullanıcı JARVIS Chrome penceresini izliyor mu
        self.proc: subprocess.Popen | None = None
        self.gone = False

    # ── Bağlantı ──────────────────────────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self.ws is not None and not self.gone

    def _active_port_file(self) -> Path:
        return self.profile_dir / "DevToolsActivePort"

    def _launch_chrome(self):
        binary = self.chrome_app / "Contents" / "MacOS" / "Google Chrome"
        if not binary.exists():
            raise ChromeError("Google Chrome bulunamadı (/Applications/Google Chrome.app).")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [str(binary), "--headless=new", f"--user-data-dir={self.profile_dir}", "--remote-debugging-port=0",
             "--no-first-run", "--no-default-browser-check", f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}",
             "--lang=tr-TR", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)

    async def _read_port(self, wait: float) -> tuple[int, str] | None:
        f = self._active_port_file()
        for _ in range(max(1, int(wait / 0.25))):
            try:
                lines = f.read_text().split("\n")
                if len(lines) >= 2 and lines[0].strip().isdigit() and lines[1].startswith("/devtools/browser/"):
                    return int(lines[0]), lines[1].strip()
            except OSError:
                pass
            if wait <= 0.25:
                break
            await asyncio.sleep(0.25)
        return None

    async def _connect_ws(self, port: int, path: str) -> bool:
        import websockets
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(f"ws://127.0.0.1:{port}{path}", max_size=64 * 1024 * 1024,
                                   ping_interval=None), 5)
        except Exception:
            self.ws = None
            return False
        self.gone = False
        self._reader = asyncio.create_task(self._read_loop())
        return True

    async def start(self, visible: bool = False):
        """JARVIS Chrome'una bağlanır (açık değilse görünmez açar) ve bir sekmeye bağlanır."""
        if self.connected and self.session:
            return
        if not self.connected:
            found = await self._read_port(0)
            if not (found and await self._connect_ws(*found)):
                try:
                    self._active_port_file().unlink()
                except OSError:
                    pass
                await asyncio.to_thread(self._launch)
                found = await self._read_port(20)
                if not found:
                    raise ChromeError("JARVIS Chrome'u açılamadı (denetim portu gelmedi). Profil başka bir Chrome'da açık olabilir.")
                if not await self._connect_ws(*found):
                    raise ChromeError("JARVIS Chrome'una bağlanılamadı.")
            await self.send("Target.setDiscoverTargets", {"discover": True})
            try:
                await self.send("Browser.setDownloadBehavior", {"behavior": "deny"})
            except ChromeError:
                pass
        await self._attach()
        self.visible = bool(visible)

    async def _attach(self):
        targets = (await self.send("Target.getTargets")).get("targetInfos", [])
        page = next((t for t in targets if t.get("type") == "page" and not t.get("url", "").startswith(
            ("chrome-extension://", "devtools://"))), None)
        if page is None:
            page = {"targetId": (await self.send("Target.createTarget", {"url": "about:blank"}))["targetId"]}
        self.target = page["targetId"]
        self.session = (await self.send("Target.attachToTarget",
                                        {"targetId": self.target, "flatten": True}))["sessionId"]
        await self.send("Page.enable", session=True)
        try:
            ua = (await self.send("Browser.getVersion")).get("userAgent", "")
            if "HeadlessChrome" in ua:       # siteler görünmez Chrome'u robot sanıp engellemesin
                await self.send("Network.setUserAgentOverride", {"userAgent": ua.replace("HeadlessChrome", "Chrome"),
                                                                 "acceptLanguage": "tr-TR,tr;q=0.9,en;q=0.8"}, session=True)
            await self.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, session=True)
        except ChromeError:
            pass

    async def _ensure_page(self):
        if self.gone:
            raise ChromeGone("JARVIS Chrome'u kapandı.")
        if not self.connected:
            await self.start(self.visible)
        elif not self.session:
            await self._attach()

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(ChromeError(str(msg["error"].get("message", msg["error"]))))
                        else:
                            fut.set_result(msg.get("result", {}))
                    continue
                method, sid = msg.get("method", ""), msg.get("sessionId")
                params = msg.get("params", {})
                if method == "Target.detachedFromTarget" and params.get("sessionId") == self.session:
                    self.session = None
                elif method == "Target.targetDestroyed" and params.get("targetId") == self.target:
                    self.session = None
                for item in list(self._waiters):
                    name, want_sid, fut = item
                    if name == method and (want_sid is None or want_sid == sid) and not fut.done():
                        fut.set_result(params)
                        self._waiters.remove(item)
        except Exception:
            pass
        finally:
            self.gone = True
            self.session = None
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ChromeGone("JARVIS Chrome'u kapandı."))
            self._pending.clear()
            for _, _, fut in self._waiters:
                if not fut.done():
                    fut.set_exception(ChromeGone("JARVIS Chrome'u kapandı."))
            self._waiters.clear()

    async def send(self, method: str, params: dict | None = None, session: bool = False, timeout: float = 30):
        if self.ws is None or self.gone:
            raise ChromeGone("JARVIS Chrome'una bağlı değil.")
        mid = next(self._ids)
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            if not self.session:
                raise ChromeError("Sekme kapandı.")
            msg["sessionId"] = self.session
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self.ws.send(json.dumps(msg))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ChromeError(f"Chrome yanıt vermedi ({method}).") from None
        finally:
            self._pending.pop(mid, None)

    def _wait_event(self, method: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append((method, self.session, fut))
        return fut

    async def _settle(self, fut: asyncio.Future, timeout: float):
        """Bir eylemden sonra sayfa yükleniyorsa bitmesini bekler (yüklenmiyorsa kısa bekleyip döner)."""
        try:
            await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if not fut.done():
                fut.cancel()
            self._waiters = [w for w in self._waiters if w[2] is not fut]

    async def set_visible(self, on: bool):
        self.visible = bool(on)

    # ── Sayfa okuma ───────────────────────────────────────────────────────────────────────────
    async def navigate(self, url: str, timeout: float = 25.0) -> dict:
        await self._ensure_page()
        loaded = self._wait_event("Page.loadEventFired")
        try:
            res = await self.send("Page.navigate", {"url": url}, session=True, timeout=timeout)
        except BaseException:
            loaded.cancel()
            self._waiters = [w for w in self._waiters if w[2] is not loaded]
            raise
        if res.get("errorText"):
            loaded.cancel()
            self._waiters = [w for w in self._waiters if w[2] is not loaded]
            raise ChromeError(f"Sayfa açılamadı: {res['errorText']}")
        await self._settle(loaded, timeout)
        await asyncio.sleep(0.8)            # geç yüklenen içerik
        return await self.read()

    async def evaluate(self, expression: str, timeout: float = 15.0):
        await self._ensure_page()
        res = await self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                   "awaitPromise": True}, session=True, timeout=timeout)
        if res.get("exceptionDetails"):
            raise ChromeError("Sayfa okunamadı.")
        return res.get("result", {}).get("value")

    async def read(self) -> dict:
        data = await self.evaluate(READ_JS)
        return data if isinstance(data, dict) else {"title": "", "url": "", "text": "", "links": []}

    async def serp(self) -> list:
        data = await self.evaluate(SERP_JS)
        return data if isinstance(data, list) else []

    async def elements(self) -> list:
        data = await self.evaluate(ELEMENTS_JS)
        return data if isinstance(data, list) else []

    async def target_info(self, element_id: int) -> dict | None:
        return await self.evaluate(f"({TARGET_JS})({int(element_id)})")

    async def back(self) -> dict:
        await self._ensure_page()
        hist = await self.send("Page.getNavigationHistory", session=True)
        idx = hist.get("currentIndex", 0)
        if idx <= 0:
            raise ChromeError("Geri gidilecek sayfa yok.")
        entry = hist["entries"][idx - 1]
        loaded = self._wait_event("Page.loadEventFired")
        await self.send("Page.navigateToHistoryEntry", {"entryId": entry["id"]}, session=True)
        await self._settle(loaded, 15)
        await asyncio.sleep(0.5)
        return await self.read()

    async def screenshot(self, quality: int = 60) -> bytes:
        await self._ensure_page()
        shot = await self.send("Page.captureScreenshot", {"format": "jpeg", "quality": int(quality)},
                               session=True, timeout=15)
        return base64.b64decode(shot.get("data", ""))

    # ── Etkileşim (kararı çağıran verir; burada yalnız uygulanır) ────────────────────────────
    async def click_xy(self, x: float, y: float, wait: float = 6.0) -> None:
        """Sayfa koordinatına (CSS piksel) gerçek fare tıklaması; sayfa değişirse yüklenmesini bekler."""
        await self._ensure_page()
        loaded = self._wait_event("Page.loadEventFired")
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, session=True)
        for kind in ("mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left",
                                                         "clickCount": 1}, session=True)
        await self._settle(loaded, wait)
        await asyncio.sleep(0.6)

    async def insert_text(self, text: str) -> None:
        await self._ensure_page()
        await self.send("Input.insertText", {"text": str(text)}, session=True)

    async def press(self, key: str, wait: float = 6.0) -> None:
        await self._ensure_page()
        code, text = KEYS.get(key, (0, ""))
        if not code:
            raise ChromeError(f"Bilinmeyen tuş: {key}")
        loaded = self._wait_event("Page.loadEventFired") if key == "Enter" else None
        for kind in ("keyDown", "keyUp"):
            params = {"type": kind, "key": key, "code": key, "windowsVirtualKeyCode": code}
            if kind == "keyDown" and text:
                params["text"] = text
            await self.send("Input.dispatchKeyEvent", params, session=True)
        if loaded is not None:
            await self._settle(loaded, wait)
            await asyncio.sleep(0.6)

    async def clear_focused(self) -> None:
        """Odaktaki alanın içeriğini seçip siler (yazmadan önce)."""
        await self.evaluate("(() => { const e = document.activeElement; if (!e) return false;"
                            " if (e.select) { e.select(); } else { document.execCommand('selectAll'); } return true; })()")
        await self.press("Backspace")

    async def scroll(self, pages: float = 1.0):
        await self._ensure_page()
        await self.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": VIEWPORT[0] / 2, "y": VIEWPORT[1] / 2,
                                                     "deltaX": 0, "deltaY": int(VIEWPORT[1] * 0.8 * float(pages))},
                        session=True)
        await asyncio.sleep(0.5)

    async def close(self):
        """JARVIS Chrome'unu kapatır."""
        try:
            if self.connected:
                await self.send("Browser.close", timeout=5)
        except ChromeError:
            pass
        await self.disconnect()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    async def disconnect(self):
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        self.session = None
