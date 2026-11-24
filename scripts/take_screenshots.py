"""Take the README's screenshots of the demo data, in a headless Chrome-family browser.

    JIMMYHQ_DATA=/tmp/jimmyhq-demo JIMMYHQ_PORT=8478 uv run python scripts/take_screenshots.py
    ... take_screenshots.py --only today,bench      # just those (names without .png)

It works only on a folder that `scripts/demo_data.py` filled (the folder has its marker file),
on the port named by JIMMYHQ_PORT, never 8440. It starts JimmyHQ and a small pretend model
on the next port, signs in as the demo owner, takes each picture, and stops JimmyHQ by the
pid it started. The pictures go to `docs/screenshots/`. An app can ask for pictures of its own in a
`screenshots.json` in its folder (a list of objects with the fields of `Shot`).

The browser is driven over the DevTools protocol through a pipe, with the same small client
JimmyHQ's own agent browser uses (`jimmyhq.browser.cdp`), so nothing extra is installed.
It needs Chrome, Chromium, Edge or Brave on this computer. Each picture is 1440 by 900 at
twice the pixel density where that stays under 400 KB, and at normal density where it does
not (the two phone pictures are 390 by 844).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import struct
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _demo_common as common  # noqa: E402

OUT = common.ROOT / "docs" / "screenshots"
LIMIT = 400_000
TIMEZONE = "Europe/London"
LOCALE = "en-GB"

# Written in a helper so each step stays short. `$$` lists elements; `press` clicks a button,
# link, tab or summary by its words (the start of them, with `startsWith`).
HELPERS = """
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const words = (e) => (e.getAttribute('aria-label') || e.textContent || '').trim();
const press = (t, n = 0, exact = false) => {
  const hits = $$('button,a,[role=tab],[role=button],summary,label')
    .filter((e) => exact ? words(e) === t : words(e).startsWith(t));
  if (!hits[n]) return 'no ' + t;
  hits[n].click(); return 'ok';
};
const leaf = (t) => {
  const hit = [...document.querySelectorAll('*')]
    .find((e) => !e.children.length && e.textContent.trim() === t);
  if (!hit) return 'no ' + t;
  hit.click(); return 'ok';
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
"""


def recompress(png: bytes) -> bytes:
    """The same picture with its pixel data deflated as hard as zlib can, and no extra chunks."""
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        return png
    pos, header, data = 8, b"", []
    while pos < len(png):
        size, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + size]
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            data.append(body)
        pos += 12 + size
    packed = zlib.compress(zlib.decompress(b"".join(data)), 9)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body)))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", packed)
            + chunk(b"IEND", b""))


@dataclass
class Shot:
    name: str
    route: str  # the address after "#", such as "/bench"
    dock: str | None = "today"  # which dock tab to show; None leaves it closed
    light: bool = False
    phone: bool = False
    steps: list[str] = field(default_factory=list)  # JavaScript, run in the page in order
    frame_steps: list[str] = field(default_factory=list)  # run inside the app's own page
    wait_text: str | None = None  # text that must be on the page before the picture
    path: str = ""  # a path other than "/" (the Wall is at /wall)
    dock_width: int = 320


class Page:
    """One tab of the browser, and the pages (frames) an app runs in."""

    def __init__(self, cdp, session: str):
        self.cdp, self.session = cdp, session
        self.frames: list[tuple[str, dict]] = []
        self.size = (1440, 900)
        cdp.on(self._event)

    def _event(self, method: str, params: dict, session: str | None) -> None:
        if method == "Target.attachedToTarget":
            info = params.get("targetInfo", {})
            if info.get("type") == "iframe":
                self.frames.append((params["sessionId"], info))
        elif method == "Target.detachedFromTarget":
            self.frames = [f for f in self.frames if f[0] != params.get("sessionId")]

    async def send(self, method: str, params: dict | None = None, session: str | None = None):
        return await self.cdp.send(method, params or {}, session or self.session)

    async def js(self, expression: str, session: str | None = None):
        out = await self.send("Runtime.evaluate", {
            "expression": f"(async()=>{{{HELPERS}\n{expression}\n}})()",
            "awaitPromise": True, "returnByValue": True, "userGesture": True}, session)
        if "exceptionDetails" in out:
            detail = out["exceptionDetails"].get("exception", {}).get("description", "")
            raise RuntimeError(f"page script failed: {detail[:300]}")
        return out.get("result", {}).get("value")

    async def view(self, width: int, height: int, scale: float, phone: bool) -> None:
        self.size = (width, height)
        await self.send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": scale, "mobile": phone})
        await self.send("Emulation.setTimezoneOverride", {"timezoneId": TIMEZONE})
        await self.send("Emulation.setLocaleOverride", {"locale": LOCALE})

    async def go(self, url: str) -> None:
        self.frames = []
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(1.5)

    async def text(self) -> str:
        return await self.js("return document.body.innerText") or ""

    async def wait_for(self, text: str, seconds: float = 20) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if text in await self.text():
                return
            await asyncio.sleep(0.4)
        raise RuntimeError(f"the page never showed {text!r}")

    async def settle(self, seconds: float = 2.0) -> None:
        """Wait until the page has stopped saying it is loading, then a moment more."""
        end = time.time() + 20
        while time.time() < end:
            busy = await self.js(
                "return /Loading|Reconnecting|Checking/.test(document.body.innerText)")
            if not busy:
                break
            await asyncio.sleep(0.4)
        await asyncio.sleep(seconds)

    async def frame_js(self, expression: str) -> object:
        if not self.frames:
            raise RuntimeError("no app page is open in this screen")
        session = self.frames[-1][0]
        await self.send("Runtime.enable", {}, session)
        return await self.js(expression, session)

    async def capture(self) -> bytes:
        out = await self.send("Page.captureScreenshot", {"format": "png",
                                                         "captureBeyondViewport": False})
        return base64.b64decode(out["data"])


CHOOSE = ("const pick = (label, value) => { const el = $$('select')"
          ".find((s) => s.getAttribute('aria-label') === label); el.value = value; "
          "el.dispatchEvent(new Event('change', {bubbles: true})); };\n")
FIRST_ROWS = ("$$('button[aria-expanded=\"false\"][title]').slice(0, 2)"
              ".forEach((b) => b.click());")
OPEN_LETTER = "leaf('Reply to the school about the trip form')"

SHOTS: list[Shot] = [
    # The Today tab of the side panel next to the Bench: the hero picture.
    Shot("today", "/bench", dock="today"),
    # A letter an agent drafted, waiting for a click.
    Shot("bench", "/bench", dock=None, steps=[OPEN_LETTER]),
    # The Tape in the side panel, made wider, with two events opened.
    Shot("tape", "/tasks", dock="tape", dock_width=560, steps=[FIRST_ROWS]),
    Shot("tasks", "/tasks", dock="today",
         steps=[CHOOSE + "pick('Filter by who it is assigned to', 'owner')"]),
    Shot("chat", "/chat", dock=None, steps=["leaf('Boiler and the week ahead')"]),
    Shot("village", "/village", dock=None),
    Shot("memory", "/memory", dock=None, steps=["leaf('projects.md')"]),
    Shot("apps", "/apps", dock=None),
    Shot("settings", "/settings/models", dock=None),
    Shot("wall", "", dock=None, path="/wall"),
    Shot("app-books", "/app/books/books", dock=None,
         frame_steps=["const s = document.querySelector('select');"
                      " s.value = [...s.options].find((o) => o.text === 'Everyday Checking').value;"
                      " s.dispatchEvent(new Event('change', {bubbles: true}));"
                      " document.activeElement.blur();"]),
    Shot("app-posts", "/app/posts/posts", dock=None, frame_steps=["press('Brands')"]),
    Shot("app-packages", "/app/packages/packages", dock=None),
    Shot("app-heard", "/app/heard/heard", dock=None),
    Shot("app-imagery", "/app/imagery/imagery", dock=None),
    Shot("today-light", "/bench", dock="today", light=True),
    Shot("bench-light", "/bench", dock=None, light=True, steps=[OPEN_LETTER]),
    Shot("phone-today", "/bench", dock="today", phone=True,
         steps=["document.querySelector('[aria-label=\"Show side panel\"]').click()"]),
    Shot("phone-bench", "/bench", dock=None, phone=True),
]


def app_shots(apps: Path = common.ROOT / "apps") -> list[Shot]:
    """The pictures an app asks for in its own `screenshots.json`: a list of objects with the
    fields of `Shot`. An app in this checkout is shot when it is here; one that is not here
    is not looked for."""
    found: list[Shot] = []
    for path in sorted(apps.glob("*/screenshots.json")):
        found += [Shot(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
    return found


SHOTS += app_shots()


async def sign_in(page: Page, base: str) -> None:
    await page.go(base + "/")
    status = await page.js(
        "const r = await fetch('/api/auth/login', {method: 'POST', headers: "
        "{'Content-Type': 'application/json'}, body: JSON.stringify({password: "
        f"{json.dumps(common.DEMO_PASSWORD)}}})}}); return r.status")
    if status != 200:
        raise SystemExit(f"The demo owner could not sign in (answer {status}).")


async def take(page: Page, base: str, shot: Shot, scale: float) -> bytes:
    width, height = (390, 844) if shot.phone else (1440, 900)
    await page.view(width, height, scale, shot.phone)
    # The dock's tab is remembered by the browser; set it, then load the page afresh.
    await page.go(base + "/")
    await page.js(
        "localStorage.setItem('jimmyhq.dock.open', " + ("'true'" if shot.dock else "'false'")
        + "); localStorage.setItem('jimmyhq.dock.tab', JSON.stringify("
        + json.dumps(shot.dock or "tape") + "));"
        + f"localStorage.setItem('jimmyhq.dock.width', '{shot.dock_width}');")
    await page.go(base + (shot.path or "/") + ("" if shot.path else f"#{shot.route}"))
    if not shot.path:  # only the address after # changed, so load the page again
        page.frames = []
        await page.send("Page.reload")
        await asyncio.sleep(1.5)
    await page.settle(1.0)
    if shot.wait_text:
        await page.wait_for(shot.wait_text)
    for step in shot.steps:
        await page.js(step)
        await asyncio.sleep(0.8)
    for step in shot.frame_steps:
        await page.frame_js(step)
        await asyncio.sleep(0.8)
    if shot.light:  # the top strip's own theme switch, put back to dark afterwards
        await page.js("press('Light theme', 0, true)")
        await asyncio.sleep(1.0)
    await page.settle(1.5)
    png = await page.capture()
    if shot.light:
        await page.js("press('Dark theme', 0, true)")
        await asyncio.sleep(1.0)
    return png


def save(shot: Shot, png: bytes) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{shot.name}.png").write_bytes(png)
    return len(png)


async def run(base: str, chosen: list[Shot]) -> None:
    from jimmyhq.browser import cdp, engine

    program = next((p for p in engine.candidates() if p.is_file()), None)
    if program is None:
        import shutil as _shutil

        found = next((_shutil.which(n) for n in engine.NAMES if _shutil.which(n)), None)
        program = Path(found) if found else None
    if program is None:
        raise SystemExit("No Chrome, Chromium, Edge or Brave was found. Install one, then run "
                         "this again.")
    profile = Path(tempfile.mkdtemp(prefix="jimmyhq-shots-"))
    flags = ["--headless=new", f"--user-data-dir={profile}", "--remote-debugging-pipe",
             "--no-first-run", "--no-default-browser-check", "--disable-extensions",
             "--hide-scrollbars", "--force-color-profile=srgb", "--font-render-hinting=none",
             "--disable-background-networking", "--disable-sync", "--mute-audio",
             "about:blank"]
    proc, write_fd, read_fd = cdp.spawn(program, flags)
    client = cdp.CDP(proc, write_fd, read_fd)
    print(f"browser started, pid {proc.pid}", flush=True)
    try:
        target = await client.send("Target.createTarget", {"url": "about:blank"})
        attached = await client.send("Target.attachToTarget", {
            "targetId": target["targetId"], "flatten": True})
        page = Page(client, attached["sessionId"])
        await page.send("Page.enable")
        await page.send("Runtime.enable")
        await page.send("Target.setAutoAttach", {"autoAttach": True, "flatten": True,
                                                 "waitForDebuggerOnStart": False})
        await page.view(1440, 900, 1, False)
        await sign_in(page, base)
        for shot in chosen:
            scale = 1.0 if shot.phone else 2.0
            png = recompress(await take(page, base, shot, scale))
            if len(png) > LIMIT and scale > 1:
                png = recompress(await take(page, base, shot, 1.0))
                note = "1x"
            else:
                note = f"{scale:g}x"
            size = save(shot, png)
            print(f"{shot.name}.png  {note}  {size // 1000} KB", flush=True)
    finally:
        await client.close()
        shutil.rmtree(profile, ignore_errors=True)
        print(f"browser stopped, pid {proc.pid}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", help="comma-separated picture names, without .png")
    args = parser.parse_args()
    data, port = common.environment(fresh=False)
    chosen = SHOTS
    if args.only:
        names = {n.strip() for n in args.only.split(",")}
        unknown = names - {s.name for s in SHOTS}
        if unknown:
            raise SystemExit(f"No such picture: {sorted(unknown)}. Names: "
                             f"{', '.join(s.name for s in SHOTS)}")
        chosen = [s for s in SHOTS if s.name in names]
    logfile = data.parent / f"{data.name}-shots-server.log"
    with common.pretend_model(port + 1):
        proc = common.start_server(data, port, logfile)
        try:
            time.sleep(3)
            asyncio.run(run(f"http://127.0.0.1:{port}", chosen))
        finally:
            common.stop_server(proc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
