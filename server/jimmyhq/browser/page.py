"""Driving pages: what a page driver is, and the one that drives a real browser.

``Driver`` is the whole interface the rest of JimmyHQ uses. It always acts on the active
tab. The tools above it decide what may be done; a driver only does it, and reports what
the page looks like afterwards: a numbered list of the things one can act on, the page's
text, and where the view is.

``ChromeDriver`` is the real one. Its script runs in a world of its own inside the page,
so the page's scripts cannot change it or see its list. It looks at the main document
only: what sits inside a frame is not listed and cannot be acted on.

The tests have a second driver with the same interface that needs no browser.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from jimmyhq.browser import cdp, engine
from jimmyhq.browser.fence import may_send, sends_data

log = logging.getLogger("jimmyhq.browser.page")

KEYS: dict[str, tuple[str, str, int, str]] = {  # name -> key, code, key code, text
    "enter": ("Enter", "Enter", 13, "\r"), "tab": ("Tab", "Tab", 9, ""),
    "escape": ("Escape", "Escape", 27, ""), "backspace": ("Backspace", "Backspace", 8, ""),
    "delete": ("Delete", "Delete", 46, ""), "space": (" ", "Space", 32, " "),
    "up": ("ArrowUp", "ArrowUp", 38, ""), "down": ("ArrowDown", "ArrowDown", 40, ""),
    "left": ("ArrowLeft", "ArrowLeft", 37, ""), "right": ("ArrowRight", "ArrowRight", 39, ""),
    "pageup": ("PageUp", "PageUp", 33, ""), "pagedown": ("PageDown", "PageDown", 34, ""),
    "home": ("Home", "Home", 36, ""), "end": ("End", "End", 35, ""),
}
SCROLLS = ("down", "up", "top", "bottom")
PERMIT_S = 15.0  # how long a permitted form has to leave
SHOWN_SCHEMES = ("http:", "https:", "about:", "chrome-error:")


class DriverError(Exception):
    """Something was tried on the page and did not work. The sentence is for the agent."""

    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


class Stale(DriverError):
    """The number no longer names anything: the page changed since it was listed."""

    def __init__(self) -> None:
        super().__init__("the page has changed since you last saw it")


@dataclass
class Element:
    n: int
    role: str
    label: str
    value: str | None = None
    href: str | None = None
    checked: bool | None = None


@dataclass
class Tab:
    url: str
    title: str
    active: bool = False


@dataclass
class Look:
    """What the active tab shows right now."""

    url: str
    title: str
    elements: list[Element] = field(default_factory=list)
    more: int = 0  # elements left off the list
    text: str = ""  # the start of the page's text
    text_chars: int = 0  # all of it
    y: int = 0
    view: int = 0
    height: int = 0


@dataclass
class Submit:
    """What acting on an element (a click, or Enter while it has the focus) would send."""

    role: str
    label: str
    in_form: bool = False
    submits: bool = False  # this action is how the form is sent
    method: str = "get"
    action: str = ""
    fields: list[tuple[str, str, str]] = field(default_factory=list)  # name, type, value


class Driver(Protocol):
    level: str  # read | approve | alone: what the page may send (see fence.may_send)

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def alive(self) -> bool: ...
    async def tabs(self) -> list[Tab]: ...
    async def goto(self, url: str) -> None: ...
    async def look(self, max_elements: int, label_chars: int, text_chars: int) -> Look: ...
    async def text(self, start: int, size: int) -> tuple[str, int]: ...
    async def describe(self, n: int) -> dict[str, Any]: ...
    async def submit(self, n: int | None, how: str) -> Submit | None: ...
    async def click(self, n: int) -> None: ...
    async def type_text(self, n: int, text: str) -> None: ...
    async def key(self, name: str) -> None: ...
    async def scroll(self, direction: str) -> None: ...
    async def back(self) -> bool: ...
    async def switch(self, n: int) -> None: ...
    async def close_tab(self, n: int) -> None: ...
    async def screenshot(self, width: int, max_bytes: int) -> bytes | None: ...
    def permit(self) -> None: ...
    def notes(self) -> list[str]: ...


# What runs inside the page, in a world the page's own scripts cannot reach.
HELPERS_JS = r"""
(() => {
  const J = globalThis.__j = {els: []};
  const clean = (s, n) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim().slice(0, n);
  const SEL = 'a[href], button, input, textarea, select, summary, [role=button], [role=link],'
    + ' [role=tab], [role=menuitem], [role=checkbox], [role=radio], [role=textbox],'
    + ' [role=combobox], [role=option], [contenteditable=""], [contenteditable=true]';
  const BUTTONS = ['submit', 'button', 'image', 'reset'];
  const labelOf = (el) => {
    let t = el.getAttribute('aria-label') || '';
    if (!t && el.labels && el.labels.length) t = el.labels[0].innerText;
    if (!t) { const w = el.closest('label'); if (w) t = w.innerText; }
    return t || '';
  };
  const roleOf = (el) => {
    const tag = el.tagName.toLowerCase(), role = (el.getAttribute('role') || '').toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'a' || role === 'link') return 'link';
    if (tag === 'input') {
      if (type === 'hidden' || type === 'file') return null;
      if (type === 'checkbox' || type === 'radio') return type;
      if (BUTTONS.includes(type)) return 'button';
      return type === 'password' ? 'password box' : 'text box';
    }
    if (tag === 'textarea' || role === 'textbox' || el.isContentEditable) return 'text box';
    if (tag === 'select' || role === 'combobox') return 'dropdown';
    if (tag === 'button' || tag === 'summary' || role === 'button') return 'button';
    if (role === 'checkbox' || role === 'radio') return role;
    if (role === 'tab') return 'tab';
    if (role === 'menuitem' || role === 'option') return 'item';
    return null;
  };
  const seen = (el) => {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    return !(r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none'
      || st.opacity === '0' || st.pointerEvents === 'none' || el.disabled
      || el.closest('[aria-hidden="true"]'));
  };
  const get = (n) => {
    const el = n ? J.els[n - 1] : document.activeElement;
    return el && el.isConnected ? el : null;
  };
  const words = (el) => clean(labelOf(el) || el.innerText || el.value
    || el.getAttribute('title') || el.getAttribute('alt'), 200);
  J.look = (max, chars, textChars) => {
    J.els = [];
    const out = [];
    let more = 0;
    for (const el of document.querySelectorAll(SEL)) {
      const role = roleOf(el);
      if (!role || !seen(el)) continue;
      const box = role === 'text box' || role === 'password box' || role === 'dropdown';
      const label = clean(labelOf(el) || (box ? '' : el.innerText)
        || (role === 'button' ? el.value : '') || el.getAttribute('placeholder')
        || el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name'), chars);
      if (!label && !box) continue;
      if (J.els.length >= max) { more += 1; continue; }
      J.els.push(el);
      const item = {n: J.els.length, role, label};
      if (role === 'text box') {
        item.value = clean(el.value !== undefined ? el.value : el.innerText, chars);
      }
      if (role === 'dropdown') item.value = clean(el.selectedOptions && el.selectedOptions[0]
        ? el.selectedOptions[0].text : '', chars);
      if (role === 'checkbox' || role === 'radio') item.checked = !!el.checked;
      if (role === 'link') item.href = String(el.href || '').slice(0, 300);
      out.push(item);
    }
    const body = document.body ? document.body.innerText : '';
    return {href: location.href, title: clean(document.title, 300), elements: out, more,
            text: body.slice(0, textChars), text_chars: body.length, y: Math.round(scrollY),
            view: innerHeight, height: document.documentElement.scrollHeight,
            ready: document.readyState};
  };
  J.text = (start, size) => {
    const body = document.body ? document.body.innerText : '';
    return {text: body.slice(start, start + size), total: body.length};
  };
  J.describe = (n) => {
    const el = get(n);
    if (!el) return null;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const editable = tag === 'textarea' || el.isContentEditable || tag === 'select'
      || el.getAttribute('role') === 'textbox'
      || (tag === 'input'
          && !['checkbox', 'radio', 'file', 'hidden'].concat(BUTTONS).includes(type));
    return {tag, type, name: el.getAttribute('name') || '', id: el.id || '',
            autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
            placeholder: el.getAttribute('placeholder') || '', label: clean(labelOf(el), 200),
            inputmode: (el.getAttribute('inputmode') || '').toLowerCase(),
            role: roleOf(el) || '', editable};
  };
  J.point = (n) => {
    const el = get(n);
    if (!el) return null;
    el.scrollIntoView({block: 'center', inline: 'center'});
    if (!seen(el)) return {hidden: true};
    const r = el.getBoundingClientRect();
    const x = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
    const y = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
    const top = document.elementFromPoint(x, y);
    const own = top && (top === el || el.contains(top)
      || (top.closest('label') && top.closest('label').control === el));
    if (!own) return {covered: top ? clean(top.innerText, 90) : ''};
    return {x, y};
  };
  J.focus = (n) => {
    const el = get(n);
    if (!el) return null;
    el.scrollIntoView({block: 'center'});
    el.focus();
    if (el.tagName === 'SELECT') return 'select';
    if (typeof el.select === 'function') el.select();
    else if (el.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(el);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
    return document.activeElement === el ? 'box' : 'lost';
  };
  J.choose = (n, text) => {
    const el = get(n);
    if (!el || !el.options) return null;
    const want = clean(text, 300).toLowerCase();
    const all = Array.from(el.options);
    const found = all.find((o) => clean(o.text, 300).toLowerCase() === want)
      || all.find((o) => clean(o.text, 300).toLowerCase().includes(want));
    if (!found) return {options: all.slice(0, 30).map((o) => clean(o.text, 60))};
    el.value = found.value;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return {chosen: clean(found.text, 60)};
  };
  J.submit = (n, how) => {
    const el = get(n);
    if (!el || el === document.body) return {none: true};
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const form = el.form || el.closest('form');
    const presses = (tag === 'button' && (type === '' || type === 'submit'))
      || (tag === 'input' && (type === 'submit' || type === 'image'));
    const typed = tag === 'input'
      && !['checkbox', 'radio', 'file', 'button', 'reset'].includes(type);
    const out = {role: roleOf(el) || '', label: words(el), in_form: !!form,
                 submits: !!form && (presses || (how === 'enter' && typed)),
                 method: 'get', action: '', fields: []};
    if (!form) return out;
    const by = presses ? el : null;
    out.method = ((by && by.getAttribute('formmethod')) || form.getAttribute('method')
      || 'get').toLowerCase();
    out.action = String((by && by.hasAttribute('formaction')) ? by.formAction
      : form.action).slice(0, 2000);
    let data;
    try { data = new FormData(form, by || undefined); } catch (e) { data = new FormData(form); }
    for (const [name, value] of data.entries()) {
      const named = form.elements[name];
      const one = named && named.tagName ? named : (named && named[0]);
      const kind = one ? (one.getAttribute('type') || one.tagName).toLowerCase() : '';
      out.fields.push([String(name).slice(0, 200), kind,
                       typeof value === 'string' ? value.slice(0, 20000) : '(a file)']);
      if (out.fields.length >= 200) break;
    }
    return out;
  };
  J.scroll = (dir) => {
    const step = Math.round(innerHeight * 0.85);
    if (dir === 'top') scrollTo(0, 0);
    else if (dir === 'bottom') scrollTo(0, document.documentElement.scrollHeight);
    else scrollBy(0, dir === 'up' ? -step : step);
    return Math.round(scrollY);
  };
})()
"""


UNSAFE_TAB_NOTE = ("A tab could not be set up safely and was closed: nothing it would "
                   "have sent was let out.")
# Targets that make no requests of their own: a failed set-up does not need them closed.
CONTAINER_TYPES = ("browser", "tab")


@dataclass
class _Page:
    target: str
    session: str
    ctx: int | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    permit_until: float = 0.0


class ChromeDriver:
    """One agent's browser: a Chromium-family program with its own profile, reached
    over a pipe, with the fence's proxy as its only way out."""

    def __init__(self, program: Path, profile: Path, proxy_port: int, *, width: int,
                 height: int, max_tabs: int, page_timeout_s: float, settle_ms: int,
                 start_timeout_s: float):
        self.program, self.profile, self.proxy_port = program, profile, proxy_port
        self.width, self.height, self.max_tabs = width, height, max_tabs
        self.page_timeout_s, self.settle_s = page_timeout_s, settle_ms / 1000
        self.start_timeout_s = start_timeout_s
        self.level = "approve"
        self.c: cdp.CDP | None = None
        self.pages: dict[str, _Page] = {}  # session id -> page
        self.order: list[str] = []  # session ids, in the order the tabs opened
        self.active: str | None = None
        self._notes: list[str] = []
        self._tasks: set[asyncio.Task] = set()
        self._setting_up: set[str] = set()  # sessions still being set up (not yet a tab)
        self._root: dict[str, str] = {}  # a frame's or worker's session -> its tab's session

    # ------------------------------------------------------------------ life
    async def start(self) -> None:
        self.profile.mkdir(parents=True, exist_ok=True)
        args = engine.flags(self.profile, self.proxy_port, self.width, self.height)
        loop = asyncio.get_running_loop()
        try:
            proc, write_fd, read_fd = await loop.run_in_executor(
                None, cdp.spawn, self.program, args)
        except OSError as e:
            raise DriverError(f"the browser would not start ({type(e).__name__})") from None
        self.c = cdp.CDP(proc, write_fd, read_fd)
        self.c.on(self._event)
        try:
            await self.c.send("Browser.setDownloadBehavior",
                              {"behavior": "deny", "eventsEnabled": True},
                              timeout=self.start_timeout_s)
            await self.c.send("Target.setAutoAttach", {
                "autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
                timeout=self.start_timeout_s)
            await self._first_page()
        except (cdp.CDPError, TimeoutError):
            await self.stop()
            raise DriverError("the browser would not start") from None

    async def _first_page(self) -> None:
        end = time.monotonic() + self.start_timeout_s
        while not self.order:
            if time.monotonic() > end:
                raise TimeoutError
            await asyncio.sleep(0.05)
        await asyncio.wait_for(self.pages[self.order[0]].ready.wait(), self.start_timeout_s)

    async def stop(self) -> None:
        c, self.c = self.c, None
        for task in list(self._tasks):
            task.cancel()
        self.pages.clear()
        self.order.clear()
        self._setting_up.clear()
        self._root.clear()
        self.active = None
        if c is not None:
            await c.close()

    def kill(self) -> None:
        """Stop without the event loop: for the end of the process."""
        c, self.c = self.c, None
        if c is not None:
            c.kill()

    def alive(self) -> bool:
        return self.c is not None and not self.c.closed and self.c.proc.poll() is None

    def notes(self) -> list[str]:
        out, self._notes = self._notes, []
        return out

    def permit(self) -> None:
        if self.active in self.pages:
            self.pages[self.active].permit_until = time.monotonic() + PERMIT_S

    # ------------------------------------------------------------------ events
    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _event(self, method: str, p: dict[str, Any], session: str | None) -> None:
        if self.c is None:
            return
        if method == "Target.attachedToTarget":
            self._spawn(self._attached(p, session))
        elif method == "Target.detachedFromTarget":
            gone = p.get("sessionId")
            self._setting_up.discard(gone)
            self._root.pop(gone, None)
            if self.pages.pop(gone, None) is not None:
                self.order.remove(gone)
                if self.active == gone:
                    self.active = self.order[-1] if self.order else None
        elif method == "Fetch.requestPaused":
            self._paused(p, session)
        elif method == "Page.javascriptDialogOpening":
            self.c.fire("Page.handleJavaScriptDialog", {"accept": False}, session)
            self._notes.append("The page put up a dialog box; it was dismissed unanswered.")
        elif method == "Browser.downloadWillBegin":
            self._notes.append("The page tried to download a file. Downloads are refused: "
                               "nothing was saved.")
        elif method in ("Page.frameNavigated", "Runtime.executionContextsCleared"):
            page = self.pages.get(session or "")
            if page is not None and not (p.get("frame") or {}).get("parentId"):
                page.ctx = None

    def _paused(self, p: dict[str, Any], session: str | None) -> None:
        request = p.get("request") or {}
        # A request from a frame or a worker of its own comes on that target's session,
        # which is never a permitted page: at "Ask me each time" it is held.
        page = self.pages.get(session or "")
        document = p.get("resourceType") == "Document"
        permitted = bool(page and time.monotonic() < page.permit_until)
        method = str(request.get("method") or "GET")
        has_body = bool(request.get("hasPostData") or request.get("postData")
                        or request.get("postDataEntries"))
        if may_send(method, self.level, permitted, has_body):
            if permitted and document and sends_data(method, has_body) and page is not None:
                page.permit_until = 0.0  # one form, once
            self.c.fire("Fetch.continueRequest", {"requestId": p["requestId"]}, session)
            return
        self.c.fire("Fetch.failRequest",
                    {"requestId": p["requestId"], "errorReason": "BlockedByClient"}, session)
        note = ("The page tried to send something by itself, which was stopped: nothing "
                "was sent.")
        if note not in self._notes:
            self._notes.append(note)

    async def _attached(self, p: dict[str, Any], parent: str | None = None) -> None:
        """Set up a new target, which starts paused. Fail closed: a target whose set-up
        fails is closed and never resumed, because it would send with no request check."""
        info, session = p.get("targetInfo") or {}, p["sessionId"]
        c = self.c
        if c is None:
            return
        kind = info.get("type")
        if kind in CONTAINER_TYPES:
            c.fire("Runtime.runIfWaitingForDebugger", None, session)  # sends nothing itself
            return
        root = self._root.get(parent or "", parent)
        if root is not None:
            self._root[session] = root
        if kind == "page" and len(self.order) + len(self._setting_up) >= self.max_tabs:
            self._notes.append(f"The page tried to open another tab, past the limit of "
                               f"{self.max_tabs}. It was closed.")
            c.fire("Target.closeTarget", {"targetId": info["targetId"]})
            return
        self._setting_up.add(session)
        try:
            # What must be on before the target runs at all: the request check, here and
            # for what it starts. Page.enable is left until after: a real browser was seen
            # not to answer it for a pop-up that is still paused, which made every pop-up
            # run unwatched under the old order. A failure after the target runs still
            # closes it, but by then nothing it sends goes unchecked.
            await c.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, session, 10)
            await self._children(c, session)
            if session not in self._setting_up:  # it went away while it was being set up
                return
            c.fire("Runtime.runIfWaitingForDebugger", None, session)
            if kind == "page":
                await c.send("Page.enable", None, session, 10)
                await c.send("Page.setInterceptFileChooserDialog", {"enabled": True},
                             session, 10)
                await c.send("Emulation.setDeviceMetricsOverride", {
                    "width": self.width, "height": self.height, "deviceScaleFactor": 1,
                    "mobile": False}, session, 10)
        except Exception:  # noqa: BLE001 - whatever went wrong, the target is not let go
            log.debug("a new browser target could not be set up", exc_info=True)
            self._setting_up.discard(session)
            self._close_unsafe(c, info, session, root)
            return
        if session not in self._setting_up:
            return
        self._setting_up.discard(session)
        if kind == "page":
            page = _Page(info["targetId"], session)
            self.pages[session] = page
            self.order.append(session)
            if self.active is None:
                self.active = session
            elif len(self.order) > 1:
                self.active = session  # a new tab comes to the front, as it does for a person
                self._notes.append("A new tab opened and is now the one in front.")
            page.ready.set()

    def _close_unsafe(self, c: cdp.CDP, info: dict[str, Any], session: str,
                      root: str | None) -> None:
        """Close a target that could not be put under the request check, and the tab it
        belongs to when it is a frame or a worker, so nothing it started runs unwatched.
        It is never resumed."""
        c.fire("Target.closeTarget", {"targetId": info.get("targetId")})
        page = self.pages.pop(root, None) if root else None
        if page is not None:
            self.order.remove(root)
            if self.active == root:
                self.active = self.order[-1] if self.order else None
            c.fire("Target.closeTarget", {"targetId": page.target})
        self._notes.append(UNSAFE_TAB_NOTE)

    @staticmethod
    async def _children(c: cdp.CDP, session: str) -> None:
        """Attach to what this target starts of its own (a frame from another site, a
        worker), paused until it is set up, so its requests pass ``_paused`` too."""
        await c.send("Target.setAutoAttach", {
            "autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
            session, 5)

    # ------------------------------------------------------------------ plumbing
    def _page(self) -> _Page:
        if not self.alive() or self.active not in self.pages:
            raise DriverError("the browser is not running")
        return self.pages[self.active]

    async def _send(self, method: str, params: dict[str, Any] | None = None,
                    timeout: float | None = None) -> dict[str, Any]:
        page = self._page()
        try:
            return await self.c.send(method, params, page.session, timeout or self.page_timeout_s)
        except cdp.CDPError as e:
            raise DriverError(str(e)) from None

    async def _call(self, expression: str) -> Any:
        """Run one of the helpers in the page's separate world, making the world first
        when the page is new."""
        page = self._page()
        for _ in range(2):
            try:
                if page.ctx is None:
                    tree = await self.c.send("Page.getFrameTree", None, page.session, 10)
                    world = await self.c.send("Page.createIsolatedWorld", {
                        "frameId": tree["frameTree"]["frame"]["id"],
                        "worldName": "jimmyhq"}, page.session, 10)
                    ctx = world["executionContextId"]
                    await self.c.send("Runtime.evaluate", {
                        "expression": HELPERS_JS, "contextId": ctx}, page.session, 10)
                    page.ctx = ctx
                got = await self.c.send("Runtime.evaluate", {
                    "expression": expression, "contextId": page.ctx, "returnByValue": True},
                    page.session, self.page_timeout_s)
            except cdp.CDPError:
                page.ctx = None
                continue
            if "exceptionDetails" in got:
                raise DriverError("the page could not be read")
            return (got.get("result") or {}).get("value")
        raise DriverError("the page could not be read")

    async def _settle(self) -> None:
        """Give the page a moment to react, then wait for it to finish loading."""
        await asyncio.sleep(self.settle_s)
        end = time.monotonic() + self.page_timeout_s
        while self._setting_up and time.monotonic() < end:  # a new tab is not one till then
            await asyncio.sleep(0.02)
        while time.monotonic() < end:
            try:
                if await self._call("document.readyState") == "complete":
                    return
            except DriverError:
                if not self.alive() or self.active not in self.pages:
                    return
            await asyncio.sleep(0.15)

    async def _info(self, page: _Page) -> dict[str, Any]:
        try:
            got = await self.c.send("Target.getTargetInfo", {"targetId": page.target}, None, 10)
        except cdp.CDPError:
            return {}
        return got.get("targetInfo") or {}

    # ------------------------------------------------------------------ the interface
    async def tabs(self) -> list[Tab]:
        out = []
        for session in list(self.order):
            info = await self._info(self.pages[session])
            out.append(Tab(str(info.get("url") or ""), str(info.get("title") or ""),
                           session == self.active))
        return out

    async def goto(self, url: str) -> None:
        got = await self._send("Page.navigate", {"url": url})
        await self._settle()
        if got.get("errorText") and not got.get("isDownload"):
            raise DriverError(f"the page did not open ({str(got['errorText'])[:80]})")

    async def look(self, max_elements: int, label_chars: int, text_chars: int) -> Look:
        got = await self._call(f"__j.look({int(max_elements)}, {int(label_chars)}, "
                               f"{int(text_chars)})") or {}
        href = str(got.get("href") or "about:blank")
        if not href.startswith(SHOWN_SCHEMES):
            # Not somewhere a page may be: leave it, and say so.
            self._notes.append("The tab had left the web, so it was emptied.")
            await self._send("Page.navigate", {"url": "about:blank"})
            await self._settle()
            return await self.look(max_elements, label_chars, text_chars)
        info = await self._info(self._page())
        return Look(
            url=str(info.get("url") or href), title=str(got.get("title") or ""),
            elements=[Element(**e) for e in got.get("elements") or []],
            more=int(got.get("more") or 0), text=str(got.get("text") or ""),
            text_chars=int(got.get("text_chars") or 0), y=int(got.get("y") or 0),
            view=int(got.get("view") or 0), height=int(got.get("height") or 0))

    async def text(self, start: int, size: int) -> tuple[str, int]:
        got = await self._call(f"__j.text({int(start)}, {int(size)})") or {}
        return str(got.get("text") or ""), int(got.get("total") or 0)

    async def describe(self, n: int) -> dict[str, Any]:
        got = await self._call(f"__j.describe({int(n)})")
        if got is None:
            raise Stale
        return got

    async def submit(self, n: int | None, how: str) -> Submit | None:
        got = await self._call(f"__j.submit({int(n or 0)}, {('click', 'enter')[how == 'enter']!r})")
        if got is None:
            raise Stale
        if got.get("none"):
            return None
        return Submit(role=got["role"], label=got["label"], in_form=got["in_form"],
                      submits=got["submits"], method=got["method"], action=got["action"],
                      fields=[tuple(f) for f in got["fields"]])

    async def click(self, n: int) -> None:
        at = await self._call(f"__j.point({int(n)})")
        if at is None:
            raise Stale
        if at.get("hidden"):
            raise DriverError(f"[{n}] is not visible right now; it may need a menu opened first")
        if "covered" in at:
            raise DriverError(f"[{n}] is covered by something else on the page. Deal with "
                              "that first (a notice usually has its own button), then try again")
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self._send("Input.dispatchMouseEvent", {
                "type": kind, "x": at["x"], "y": at["y"], "button": "left",
                "buttons": 1 if kind == "mousePressed" else 0, "clickCount": 1})
        await self._settle()

    async def type_text(self, n: int, text: str) -> None:
        kind = await self._call(f"__j.focus({int(n)})")
        if kind is None:
            raise Stale
        if kind == "select":
            import json

            got = await self._call(f"__j.choose({int(n)}, {json.dumps(text)})") or {}
            if "chosen" not in got:
                raise DriverError(f"[{n}] is a list to choose from and has no such choice")
        elif kind == "lost":
            raise DriverError(f"[{n}] would not take the cursor")
        else:
            await self._send("Input.insertText", {"text": text})
        await self._settle()

    async def key(self, name: str) -> None:
        key, code, number, text = KEYS[name]
        base = {"key": key, "code": code, "windowsVirtualKeyCode": number,
                "nativeVirtualKeyCode": number}
        down = {"type": "keyDown" if text else "rawKeyDown", **base}
        if text:
            down["text"] = text
        await self._send("Input.dispatchKeyEvent", down)
        await self._send("Input.dispatchKeyEvent", {"type": "keyUp", **base})
        await self._settle()

    async def scroll(self, direction: str) -> None:
        await self._call(f"__j.scroll({direction!r})")
        await asyncio.sleep(min(self.settle_s, 0.3))

    async def back(self) -> bool:
        history = await self._send("Page.getNavigationHistory")
        index = int(history.get("currentIndex") or 0)
        if index <= 0:
            return False
        await self._send("Page.navigateToHistoryEntry",
                         {"entryId": history["entries"][index - 1]["id"]})
        await self._settle()
        return True

    async def switch(self, n: int) -> None:
        self.active = self.order[n - 1]
        with contextlib.suppress(cdp.CDPError):
            await self.c.send("Target.activateTarget",
                              {"targetId": self.pages[self.active].target}, None, 10)

    async def close_tab(self, n: int) -> None:
        session = self.order[n - 1]
        if len(self.order) == 1:  # the last tab is emptied, not closed
            self.active = session
            await self._send("Page.navigate", {"url": "about:blank"})
            await self._settle()
            return
        target = self.pages[session].target
        with contextlib.suppress(cdp.CDPError):
            await self.c.send("Target.closeTarget", {"targetId": target}, None, 10)
        for _ in range(40):
            if session not in self.pages:
                break
            await asyncio.sleep(0.05)

    async def screenshot(self, width: int, max_bytes: int) -> bytes | None:
        try:
            metrics = await self._send("Page.getLayoutMetrics", timeout=10)
            view = metrics.get("cssVisualViewport") or {}
            wide = float(view.get("clientWidth") or self.width)
            high = float(view.get("clientHeight") or self.height)
            scale = min(1.0, width / wide)
            for _ in range(5):
                got = await self._send("Page.captureScreenshot", {
                    "format": "png", "captureBeyondViewport": False,
                    "clip": {"x": float(view.get("pageX") or 0), "y": float(view.get("pageY") or 0),
                             "width": wide, "height": high, "scale": scale}}, timeout=15)
                data = base64.b64decode(got.get("data") or "")
                if data and len(data) <= max_bytes:
                    return data
                scale *= 0.7
        except DriverError:
            pass
        return None
