"""Build the public website from site/src into site/dist, with facts read from the repo.

Standard library only. Run it from anywhere:

    python3 site/build.py                 # into site/dist
    python3 site/build.py --out /tmp/x    # somewhere else
    python3 site/build.py --strict        # fail if any screenshot is missing (for a release)

What it reads from the repo, so the site cannot drift from the product:

- the version, from pyproject.toml;
- the apps made by the JimmyHQ project and the example apps, from apps/*/jimmyhq.app.json,
  with the exact sentences the install dialog shows, from server/jimmyhq/apps/permissions.py;
- the colour tokens and font stacks, from web/src/index.css;
- the model roles and what each is for, and the kinds of model provider, from the models code;
- where the project stands, from the "Project status" section of README.md, and the
  screenshots' descriptions, from the README's own pictures;
- the design rules, from CONTRIBUTING.md;
- the Dev log from CHANGELOG.md and the Backlog from ROADMAP.md;
- the logo, from web/public;
- screenshots, from docs/screenshots (a drawn placeholder stands in for a missing one).

A page in src/pages is HTML with a short header comment (title, description) and
placeholders in double braces, such as {{version}} or {{shot:bench|A caption}}. An unknown
placeholder, or Markdown outside the small subset below, stops the build with the file and
line, so a mistake never reaches the site quietly.
"""

from __future__ import annotations

import argparse
import ast
import html
import importlib.util
import json
import re
import shutil
import struct
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

# The project's public repository. One constant, used for every link to the code and docs.
REPO_URL = "https://github.com/shaunpoland1/JimmyHQ"
# The site's own address, for canonical links, the sitemap and social cards.
SITE_URL = "https://jimmyhq.ai"
BRANCH = "main"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = HERE / "src"
DEFAULT_OUT = HERE / "dist"
DEFAULT_SHOTS = ROOT / "docs" / "screenshots"

# Every screenshot the site uses: file stem -> (width, height, alt text).
SHOTS: dict[str, tuple[int, int, str]] = {
    "today": (1440, 900, "The Bench, with today's tasks in the side panel"),
    "bench": (1440, 900, "The Bench: cards waiting for a decision, and one card open"),
    "tape": (1440, 900, "The Tape tab of the side panel, with one event opened"),
    "tasks": (1440, 900, "Tasks: one list for your work and your agents' work"),
    "chat": (1440, 900, "Chat: a conversation with a model, with the record switch"),
    "village": (1440, 900, "The Village: agents with their needs, mode and diary"),
    "memory": (1440, 900, "Memory: notes in the Library and facts waiting for a decision"),
    "apps": (1440, 900, "Apps: installed apps and the apps made by the JimmyHQ project"),
    "settings": (1440, 900, "Settings, Models and roles: the providers and the roles"),
    "wall": (1440, 900, "The Wall: a status board for a second screen"),
    "app-books": (1440, 900, "Books: accounts, a register and bills"),
    "app-posts": (1440, 900, "Posts: drafts checked against their source"),
    "app-packages": (1440, 900, "Packages: each parcel with its status and last event"),
    "app-heard": (1440, 900, "Heard: transcribed conversations kept by day"),
    "app-imagery": (1440, 900, "Imagery: a gallery of pictures made from descriptions"),
    "today-light": (1440, 900, "The Bench with today's tasks, in the light theme"),
    "bench-light": (1440, 900, "The Bench, in the light theme"),
    "phone-today": (390, 844, "Today on a phone"),
    "phone-bench": (390, 844, "The Bench on a phone"),
}

# The pages, in the order the menu lists them: (source file, output path, menu label, icon).
NAV: list[tuple[str, str, str, str]] = [
    ("index.html", "index.html", "Home", "home"),
    ("tour.html", "tour.html", "Tour", "tour"),
    ("apps.html", "apps.html", "Apps", "apps"),
    ("get-started.html", "get-started.html", "Get started", "start"),
    ("security.html", "security.html", "Security", "shield"),
    ("devlog.html", "devlog.html", "Dev log", "log"),
    ("backlog.html", "backlog.html", "Backlog", "list"),
    ("about.html", "about.html", "About", "info"),
]
NOT_FOUND = ("404.html", "404.html")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "font-src 'self'; base-uri 'none'; form-action 'none'")

NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                "seventeen", "eighteen", "nineteen", "twenty"]


class BuildError(Exception):
    """Something in the repo or in site/src cannot be built. The message says where."""


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def words(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def join_or(items: list[str], last: str = "or") -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last} {items[-1]}"


# --- links --------------------------------------------------------------------------------


def repo_link(path: str) -> str:
    """A link to a file in the repo on GitHub: a folder or a file, with any #anchor kept."""
    target, _, anchor = path.partition("#")
    target = target.lstrip("./")
    kind = "tree" if target.endswith("/") or "." not in target.rsplit("/", 1)[-1] else "blob"
    url = f"{REPO_URL}/{kind}/{BRANCH}/{target.rstrip('/')}" if target else REPO_URL
    return f"{url}#{anchor}" if anchor else url


def check_url(url: str, where: str) -> str:
    """Links in Markdown may point into the repo (relative) or at the repo or this site."""
    if url.startswith("http://"):
        raise BuildError(f"{where}: {url} is plain http. Use https, or a path in the repo.")
    if url.startswith("https://"):
        if url == REPO_URL or url.startswith(REPO_URL + "/") or url.startswith(SITE_URL):
            return url
        host = urlsplit(url).hostname
        raise BuildError(f"{where}: {url} links to {host}. The site links only to the repo and "
                         "to itself; link to the repo's copy instead.")
    if url.startswith("#"):
        return url
    if url.startswith("/"):
        return url
    return repo_link(url)


# --- a small, strict Markdown -------------------------------------------------------------

INLINE_CODE = re.compile(r"`([^`]+)`")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")


def inline(text: str, where: str) -> str:
    """Inline Markdown: `code`, **bold** and [links](url). Everything else is escaped text."""
    out = []
    for i, part in enumerate(INLINE_CODE.split(text)):
        if i % 2:
            out.append(f"<code>{esc(part)}</code>")
            continue
        if "`" in part:
            raise BuildError(f"{where}: an unmatched backtick")
        pieces = []
        last = 0
        for m in LINK.finditer(part):
            pieces.append(esc(part[last:m.start()]))
            url = check_url(m.group(2), where)
            external = url.startswith("https://")
            attrs = ' rel="noopener"' if external else ""
            pieces.append(f'<a href="{esc(url)}"{attrs}>{esc(m.group(1))}</a>')
            last = m.end()
        pieces.append(esc(part[last:]))
        done = "".join(pieces)
        done = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", done)
        if "**" in done:
            raise BuildError(f"{where}: an unmatched ** (bold)")
        out.append(done)
    return "".join(out)


@dataclass
class Block:
    kind: str  # "h1", "h2", "h3", "p", "ul", "ol"
    text: str = ""  # headings and paragraphs: the raw text
    items: list[str] = field(default_factory=list)  # lists: each item's raw text
    line: int = 0


UNSUPPORTED = (("```", "a code fence"), ("|", "a table"), (">", "a quote"), ("<", "HTML"),
               ("![", "an image"), ("* ", "a list with *; use -"), ("####", "a fourth-level "
               "heading"), ("---", "a rule"), ("    ", "an indented code block"))


def parse_markdown(text: str, source: Path) -> list[Block]:
    """Headings (#, ##, ###), paragraphs, lists (- and 1.) with indented continuation lines,
    inline code, bold and links. Anything else stops the build, naming the line."""
    blocks: list[Block] = []
    current: Block | None = None
    for n, raw in enumerate(text.splitlines(), start=1):
        where = f"{rel(source)}:{n}"
        line = raw.rstrip()
        if not line:
            current = None
            continue
        bullet = re.match(r"^- (.*)$", line)
        number = re.match(r"^\d+\. (.*)$", line)
        heading = re.match(r"^(#{1,3}) (.*)$", line)
        if heading:
            blocks.append(Block(f"h{len(heading.group(1))}", heading.group(2).strip(), line=n))
            current = None
        elif bullet or number:
            kind = "ul" if bullet else "ol"
            body = (bullet or number).group(1)
            if current is None or current.kind != kind:
                current = Block(kind, line=n)
                blocks.append(current)
            current.items.append(body)
        elif raw.startswith(" ") and current is not None and current.kind in ("ul", "ol"):
            if not current.items:
                raise BuildError(f"{where}: an indented line with no list item above it")
            current.items[-1] += " " + line.strip()
        else:
            for start, what in UNSUPPORTED:
                if raw.startswith(start):
                    raise BuildError(f"{where}: {what} is not in the Markdown the site reads "
                                     "(headings, paragraphs, lists, `code`, **bold**, links)")
            if current is not None and current.kind == "p":
                current.text += " " + line.strip()
            else:
                current = Block("p", line.strip(), line=n)
                blocks.append(current)
    return blocks


def render_blocks(blocks: list[Block], source: Path, shift: int = 0) -> str:
    """Blocks as HTML. `shift` moves headings down, so a file's ## can be the page's h3."""
    out = []
    for b in blocks:
        where = f"{rel(source)}:{b.line}"
        if b.kind in ("h1", "h2", "h3"):
            level = min(6, int(b.kind[1]) + shift)
            out.append(f"<h{level}>{inline(b.text, where)}</h{level}>")
        elif b.kind == "p":
            out.append(f"<p>{inline(b.text, where)}</p>")
        else:
            items = "".join(f"<li>{inline(i, where)}</li>" for i in b.items)
            out.append(f"<{b.kind}>{items}</{b.kind}>")
    return "\n".join(out)


def section(text: str, heading: str, source: Path) -> str:
    """The text under one ## heading of a Markdown file, up to the next ## heading."""
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise BuildError(f"{rel(source)}: no section '## {heading}'")
    return m.group(1)


# --- facts from the repo ------------------------------------------------------------------


@dataclass
class App:
    id: str
    name: str
    summary: str
    version: str
    tier: str  # "full", "sandboxed" or "described"
    private: bool
    lines: list[tuple[str, bool]]  # the install dialog's sentences, and whether one is a warning
    folder: str


@dataclass
class Release:
    version: str
    date: str  # YYYY-MM-DD, or "unreleased"
    summary: list[str]  # paragraphs, as HTML
    lists: list[tuple[str, str]]  # (Added / Changed / Fixed, the list as HTML)


@dataclass
class Facts:
    version: str
    apps: list[App]
    examples: list[App]
    tokens_light: str
    tokens_dark: str
    font_sans: str
    font_mono: str
    roles: list[tuple[str, str]]
    local_servers: list[str]
    hosted: list[str]
    status: str
    alts: dict[str, str]
    rules: str
    changelog_intro: str
    releases: list[Release]
    roadmap_intro: str
    roadmap: list[tuple[str, str, str]]  # (title, intro HTML, list HTML)


def read_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def load_permissions():
    """The app platform's permission table, loaded from its own file (it needs only the
    standard library), so each app's lines are exactly what the install dialog shows."""
    path = ROOT / "server" / "jimmyhq" / "apps" / "permissions.py"
    name = "_site_build_permissions"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load {rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def consent_lines(perms, manifest: dict) -> list[tuple[str, bool]]:
    hosts = manifest.get("owner_hosts")
    asks = SimpleNamespace(
        id=manifest["id"], tier=manifest["tier"],
        permissions=tuple(manifest.get("permissions", [])),
        private=bool(manifest.get("private", False)),
        owner_hosts=SimpleNamespace(purpose=hosts["purpose"], max=hosts["max"]) if hosts else None,
        net_expects=[SimpleNamespace(host=h, max_kb=v.get("max_kb"), timeout_s=v.get("timeout_s"))
                     for h, v in (manifest.get("net_expects") or {}).items()],
        inbound=[SimpleNamespace(max_kb=i.get("max_kb")) for i in manifest.get("inbound", [])],
    )
    return [(line.sentence, bool(line.strong)) for line in perms.describe(asks)]


def read_app(perms, path: Path) -> App:
    m = json.loads(path.read_text(encoding="utf-8"))
    tier = m["tier"]
    screens = m.get("screens", [])
    if tier == "sandboxed" and screens and all(s.get("entry") == "described" for s in screens):
        tier = "described"
    return App(id=m["id"], name=m["name"], summary=m["summary"], version=m["version"], tier=tier,
               private=bool(m.get("private", False)), lines=consent_lines(perms, m),
               folder=str(path.parent.relative_to(ROOT)) + "/")


def read_apps() -> tuple[list[App], list[App]]:
    perms = load_permissions()
    first = [read_app(perms, p) for p in sorted((ROOT / "apps").glob("*/jimmyhq.app.json"))]
    examples = [read_app(perms, p)
                for p in sorted((ROOT / "apps" / "examples").glob("*/jimmyhq.app.json"))]
    if not first:
        raise BuildError("no apps found under apps/*/jimmyhq.app.json")
    return first, examples


def read_tokens() -> tuple[str, str, str, str]:
    css = (ROOT / "web" / "src" / "index.css").read_text(encoding="utf-8")
    light = re.search(r"^:root \{(.*?)^\}", css, re.M | re.S)
    dark = re.search(r'^:root\[data-theme="dark"\] \{(.*?)^\}', css, re.M | re.S)
    sans = re.search(r"--font-sans:\s*([^;]+);", css)
    mono = re.search(r"--font-mono:\s*([^;]+);", css)
    if not (light and dark and sans and mono):
        raise BuildError("web/src/index.css no longer has the :root tokens, the dark tokens "
                         "and the two font stacks this build reads")

    def clean(block: str) -> str:
        decls = [d.strip() for d in re.sub(r"/\*.*?\*/", "", block, flags=re.S).split(";")]
        return "\n".join(f"  {d};" for d in decls if d)

    return clean(light.group(1)), clean(dark.group(1)), sans.group(1).strip(), mono.group(1).strip()


def read_roles() -> list[tuple[str, str]]:
    shapes = ROOT / "server" / "jimmyhq" / "models" / "shapes.py"
    roles: tuple[str, ...] | None = None
    for node in ast.parse(shapes.read_text(encoding="utf-8")).body:
        names = [getattr(target, "id", "") for target in getattr(node, "targets", [])]
        if isinstance(node, ast.Assign) and "ROLES" in names:
            roles = ast.literal_eval(node.value)
    ts = (ROOT / "web" / "src" / "modules" / "models" / "lib" / "roles.ts").read_text(
        encoding="utf-8")
    block = re.search(r"ROLE_USES[^=]*=\s*\{(.*?)\};", ts, re.S)
    if not roles or not block:
        raise BuildError("cannot read the model roles from models/shapes.py and roles.ts")
    uses = dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))
    if set(uses) != set(roles):
        raise BuildError(f"the roles in shapes.py {sorted(roles)} and the role uses in roles.ts "
                         f"{sorted(uses)} differ; make them agree before building the site")
    return [(r, uses[r]) for r in roles]


def read_providers() -> tuple[list[str], list[str]]:
    ts = (ROOT / "web" / "src" / "modules" / "models" / "lib" / "providers.ts").read_text(
        encoding="utf-8")
    kinds = re.search(r"export const KINDS[^=]*=\s*\[(.*?)\n\];", ts, re.S)
    local = re.search(r"export const LOCAL_SUGGESTIONS[^=]*=\s*\[(.*?)\n\];", ts, re.S)
    if not kinds or not local:
        raise BuildError("cannot read the provider kinds from models/lib/providers.ts")
    hosted = []
    for entry in re.findall(r"\{(.*?)\n  \}", kinds.group(1), re.S):
        title = re.search(r'title:\s*"([^"]+)"', entry)
        hint = re.search(r'hint:\s*"([^"]+)"', entry)
        if title and hint and hint.group(1).startswith("Hosted"):
            hosted.append(title.group(1))
    servers = re.findall(r'name:\s*"([^"]+)"', local.group(1))
    if not hosted or not servers:
        raise BuildError("found no hosted providers or local servers in providers.ts")
    return servers, hosted


def read_status() -> str:
    """README's "## Project status" section, as HTML: the one place that says what is solid,
    what has not met a real service yet, and what is not built."""
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    if not re.search(r"^## Project status\s*$", text, re.M):
        raise BuildError("README.md has no '## Project status' section; the site's status "
                         "comes from its list")
    body = re.sub(r"<!--.*?-->", "", section(text, "Project status", path), flags=re.S)
    blocks = parse_markdown(body, path)
    if not any(b.kind == "ul" for b in blocks):
        raise BuildError("README.md's '## Project status' section has no list of what is solid, "
                         "not yet proven and not built")
    return render_blocks(blocks, path, shift=1)


def read_alts() -> dict[str, str]:
    """The README describes each screenshot it shows; the site uses the same words."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.findall(r'<img src="docs/screenshots/([\w-]+)\.png" alt="([^"]+)"', text)
    return {stem: html.unescape(alt) for stem, alt in found}


def read_rules() -> str:
    path = ROOT / "CONTRIBUTING.md"
    blocks = parse_markdown(section(path.read_text(encoding="utf-8"), "Design rules", path), path)
    if not blocks or blocks[0].kind != "ol":
        raise BuildError("CONTRIBUTING.md's '## Design rules' should be one numbered list")
    return render_blocks(blocks, path)


RELEASE_HEADING = re.compile(r"^(\d+\.\d+\.\d+) - (\d{4}-\d{2}-\d{2}|unreleased)$")
RELEASE_PARTS = ("Added", "Changed", "Fixed")


def semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def read_changelog() -> tuple[str, list[Release]]:
    path = ROOT / "CHANGELOG.md"
    if not path.is_file():
        raise BuildError("CHANGELOG.md is missing at the repo root")
    blocks = parse_markdown(path.read_text(encoding="utf-8"), path)
    intro: list[str] = []
    releases: list[Release] = []
    part: str | None = None
    for b in blocks:
        where = f"{rel(path)}:{b.line}"
        if b.kind == "h1":
            continue
        if b.kind == "h2":
            m = RELEASE_HEADING.match(b.text)
            if not m:
                raise BuildError(f"{where}: a release heading is '## 1.2.3 - 2026-01-31' "
                                 "(or '- unreleased')")
            releases.append(Release(m.group(1), m.group(2), [], []))
            part = None
        elif not releases:
            if b.kind != "p":
                raise BuildError(f"{where}: only a paragraph may come before the first release")
            intro.append(inline(b.text, where))
        elif b.kind == "h3":
            if b.text not in RELEASE_PARTS or any(p == b.text for p, _ in releases[-1].lists):
                raise BuildError(f"{where}: a release's parts are Added, Changed and Fixed, "
                                 "each once")
            part = b.text
        elif b.kind == "p" and part is None:
            releases[-1].summary.append(inline(b.text, where))
        elif b.kind == "ul" and part is not None:
            releases[-1].lists.append((part, render_blocks([b], path)))
            part = None
        else:
            raise BuildError(f"{where}: a release is its summary, then Added, Changed and Fixed, "
                             "each a heading and a list")
    if not releases:
        raise BuildError(f"{rel(path)}: no releases")
    for r in releases:
        if not r.summary:
            raise BuildError(f"{rel(path)}: release {r.version} has no summary")
    versions = [semver(r.version) for r in releases]
    if versions != sorted(versions, reverse=True) or len(set(versions)) != len(versions):
        raise BuildError(f"{rel(path)}: releases must be newest first, each version once")
    return " ".join(intro), releases


ROADMAP_SECTIONS = ("Now", "Next", "Later", "Not yet exercised against real services",
                    "Considering")


def read_roadmap() -> tuple[str, list[tuple[str, str, str]]]:
    path = ROOT / "ROADMAP.md"
    blocks = parse_markdown(path.read_text(encoding="utf-8"), path)
    intro: list[str] = []
    sections: list[list] = []
    for b in blocks:
        where = f"{rel(path)}:{b.line}"
        if b.kind == "h1":
            continue
        if b.kind == "h2":
            sections.append([b.text, [], ""])
        elif b.kind == "h3":
            raise BuildError(f"{where}: the backlog has one level of sections (##)")
        elif not sections:
            intro.append(inline(b.text, where) if b.kind == "p" else "")
        elif b.kind == "p":
            if sections[-1][2]:
                raise BuildError(f"{where}: a paragraph after a section's list")
            sections[-1][1].append(f"<p>{inline(b.text, where)}</p>")
        else:
            if sections[-1][2]:
                raise BuildError(f"{where}: one list for each section")
            sections[-1][2] = render_blocks([b], path)
    titles = [s[0] for s in sections]
    missing = [t for t in ROADMAP_SECTIONS if t not in titles]
    if missing:
        raise BuildError(f"{rel(path)}: missing the sections {missing}")
    for title, _, items in sections:
        if not items:
            raise BuildError(f"{rel(path)}: the section '{title}' has no list")
    return " ".join(i for i in intro if i), [(t, "".join(p), items) for t, p, items in sections]


def read_facts() -> Facts:
    apps, examples = read_apps()
    light, dark, sans, mono = read_tokens()
    servers, hosted = read_providers()
    intro, releases = read_changelog()
    road_intro, road = read_roadmap()
    return Facts(version=read_version(), apps=apps, examples=examples, tokens_light=light,
                 tokens_dark=dark, font_sans=sans, font_mono=mono, roles=read_roles(),
                 local_servers=servers, hosted=hosted, status=read_status(), alts=read_alts(),
                 rules=read_rules(),
                 changelog_intro=intro, releases=releases, roadmap_intro=road_intro,
                 roadmap=road)


# --- screenshots --------------------------------------------------------------------------


def png_size(path: Path) -> tuple[int, int] | None:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def token(block: str, name: str) -> str:
    m = re.search(rf"--{name}:\s*([^;]+);", block)
    if not m:
        raise BuildError(f"web/src/index.css has no --{name} token")
    return m.group(1).strip()


def placeholder_svg(stem: str, w: int, h: int, light: str, dark: str) -> str:
    """A neutral drawing of the app's frame, the same shape as the screenshot it stands in
    for, in the app's colours for both themes."""
    def palette(block: str) -> str:
        return (f".bg{{fill:{token(block, 'bg')}}}.sf{{fill:{token(block, 'surface')}}}"
                f".s2{{fill:{token(block, 'surface-2')}}}.ln{{fill:{token(block, 'border')}}}"
                f".tx{{fill:{token(block, 'text-faint')}}}")

    phone = w < h
    shapes = [f'<rect class="bg" width="{w}" height="{h}"/>']
    if phone:
        shapes += [f'<rect class="ln" y="56" width="{w}" height="1"/>',
                   '<rect class="s2" x="16" y="18" width="96" height="20" rx="6"/>']
        y = 80
        while y + 120 < h - 40:
            shapes.append(f'<rect class="sf" x="16" y="{y}" width="{w - 32}" height="104" rx="8"/>')
            shapes.append(f'<rect class="s2" x="32" y="{y + 18}" width="{(w - 64) // 2}" '
                          'height="12" rx="4"/>')
            y += 124
    else:
        side = round(w * 224 / 1440)
        shapes += [f'<rect class="ln" x="{side}" width="1" height="{h}"/>',
                   f'<rect class="ln" x="{side}" y="44" width="{w - side}" height="1"/>',
                   f'<rect class="ln" x="{side}" y="{h - 28}" width="{w - side}" height="1"/>']
        for i in range(7):
            shapes.append(f'<rect class="{"sf" if i == 1 else "s2"}" x="10" y="{56 + i * 36}" '
                          f'width="{side - 20}" height="28" rx="8"/>')
        x0, gap = side + 24, 16
        col = (w - x0 - 24 - gap) // 2
        for row in range(3):
            for c in range(2):
                y = 72 + row * 250
                if y + 220 > h - 40:
                    continue
                shapes.append(f'<rect class="sf" x="{x0 + c * (col + gap)}" y="{y}" '
                              f'width="{col}" height="220" rx="8"/>')
                shapes.append(f'<rect class="s2" x="{x0 + c * (col + gap) + 20}" y="{y + 22}" '
                              f'width="{col // 3}" height="12" rx="4"/>')
    label = esc(f"{stem}.png")
    size = 13 if phone else 20
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
            f'height="{h}" role="img" aria-label="Screenshot to come">'
            f"<style>{palette(light)}@media (prefers-color-scheme: dark){{{palette(dark)}}}"
            f"text{{font:{size}px ui-monospace,Menlo,Consolas,monospace}}</style>"
            + "".join(shapes)
            + f'<text class="tx" x="{w / 2}" y="{h / 2}" text-anchor="middle">{label} · '
              "screenshot to come</text></svg>")


@dataclass
class Shot:
    src: str
    width: int
    height: int
    real: bool


class Shots:
    def __init__(self, folder: Path, out: Path, facts: Facts):
        self.folder, self.out, self.facts = folder, out, facts
        self.done: dict[str, Shot] = {}
        self.missing: list[str] = []

    def get(self, stem: str) -> Shot:
        if stem not in SHOTS:
            raise BuildError(f"unknown screenshot '{stem}'; the list is SHOTS in build.py")
        if stem in self.done:
            return self.done[stem]
        w, h, _ = SHOTS[stem]
        target = self.out / "shots"
        target.mkdir(parents=True, exist_ok=True)
        source = self.folder / f"{stem}.png"
        size = png_size(source) if source.is_file() else None
        if size:
            shutil.copyfile(source, target / f"{stem}.png")
            # A picture taken at two or three times the size is shown at the size it stands for.
            scale = size[0] / w
            if scale in (1, 2, 3) and size[1] == h * scale:
                size = (w, h)
            shot = Shot(f"/shots/{stem}.png", size[0], size[1], True)
        else:
            svg = placeholder_svg(stem, w, h, self.facts.tokens_light, self.facts.tokens_dark)
            (target / f"{stem}.svg").write_text(svg, encoding="utf-8")
            shot = Shot(f"/shots/{stem}.svg", w, h, False)
            self.missing.append(stem)
        self.done[stem] = shot
        return shot

    def figure(self, stem: str, caption: str = "", eager: bool = False,
               link: bool = True) -> str:
        shot = self.get(stem)
        alt = self.facts.alts.get(stem, SHOTS[stem][2])
        source = ""
        if f"{stem}-light" in SHOTS:
            light = self.get(f"{stem}-light")
            if light.real:
                source = (f'<source media="(prefers-color-scheme: light)" srcset="{light.src}" '
                          f'width="{light.width}" height="{light.height}">')
        loading = 'fetchpriority="high"' if eager else 'loading="lazy"'
        phone = " shot-phone" if shot.width < shot.height else ""
        cap = f"<figcaption>{caption}</figcaption>" if caption else ""
        picture = (f'<picture>{source}<img src="{shot.src}" width="{shot.width}" '
                   f'height="{shot.height}" alt="{esc(alt)}" {loading} decoding="async"></picture>')
        if shot.real and link:
            # The full-size picture, a click away, for anyone who wants to read the details.
            picture = (f'<a class="shot-link" href="{shot.src}" title="Open the full-size '
                       f'picture">{picture}</a>')
        return f'<figure class="shot{phone}">{picture}{cap}</figure>'



# --- generated pieces ---------------------------------------------------------------------

TIER_WORDS = {
    "full": "A program that runs as you. JimmyHQ cannot limit it.",
    "sandboxed": "A page in a locked frame. It can do only what its permissions list.",
    "described": "A manifest and nothing else: tables and views, no code.",
}


def tier_chip(app: App) -> str:
    chips = [f'<span class="chip tier tier-{app.tier}" title="{esc(TIER_WORDS[app.tier])}">'
             f"{esc(app.tier)}</span>"]
    if app.private:
        chips.append('<span class="chip" title="It asks only a model on your own machines, and '
                     'keeps what it holds off your record.">private</span>')
    return "".join(chips)


def app_card(app: App, shots: Shots, with_shot: bool) -> str:
    lines = "".join(
        f'<li class="{"strong" if strong else ""}">{esc(text)}</li>' for text, strong in app.lines)
    shot = ""
    if with_shot and f"app-{app.id}" in SHOTS:
        shot = shots.figure(f"app-{app.id}")
    return (f'<article class="app panel" id="{esc(app.id)}"><div class="app-about">'
            f'<header class="app-head"><h3>{esc(app.name)}</h3><span class="app-meta">'
            f'{tier_chip(app)}<span class="ver">v{esc(app.version)}</span></span></header>'
            f'<p class="app-sum">{esc(app.summary)}</p>'
            f'<p class="app-src"><a href="{esc(repo_link(app.folder))}" rel="noopener">'
            f"Source: <code>{esc(app.folder)}</code></a></p>{shot}</div>"
            f'<div class="consent"><p class="label">What you agree to before it installs</p>'
            f"<ul>{lines}</ul></div></article>")


def apps_compact(facts: Facts) -> str:
    rows = "".join(
        f'<li><a href="/apps.html#{esc(a.id)}"><span class="row-name">{esc(a.name)}</span>'
        f'<span class="row-sum">{esc(a.summary)}</span><span class="row-meta">{tier_chip(a)}'
        "</span></a></li>" for a in facts.apps)
    return f'<ul class="rows">{rows}</ul>'


def consent_demo(facts: Facts, app_id: str) -> str:
    app = next((a for a in facts.examples + facts.apps if a.id == app_id), None)
    if app is None:
        raise BuildError(f"no app '{app_id}' for the consent example")
    lines = "".join(f'<li class="{"strong" if s else ""}">{esc(t)}</li>' for t, s in app.lines)
    return (f'<div class="consent-demo" role="group" aria-label="The install dialog for '
            f'{esc(app.name)}"><p class="consent-title">Install {esc(app.name)}? '
            f'{tier_chip(app)}</p><p class="consent-sub">It may:</p><ul>{lines}</ul></div>')


def roles_table(facts: Facts) -> str:
    rows = "".join(
        f'<li><span class="role-name">{esc(r)}</span><span class="role-use">{esc(u)}</span></li>'
        for r, u in facts.roles)
    return f'<ul class="roles" aria-label="Model roles">{rows}</ul>'


def release_html(r: Release, full: bool) -> str:
    date = "not released yet" if r.date == "unreleased" else r.date
    dt = "" if r.date == "unreleased" else f' datetime="{r.date}"'
    summary = "".join(f"<p>{p}</p>" for p in (r.summary if full else r.summary[:1]))
    parts = ""
    if full:
        parts = "".join(f'<div class="rel-part"><h3 class="label">{esc(name)}</h3>{body}</div>'
                        for name, body in r.lists)
    return (f'<li class="release" id="v{esc(r.version)}"><div class="rel-when">'
            f'<a class="chip" href="/devlog.html#v{esc(r.version)}">v{esc(r.version)}</a>'
            f'<time{dt}>{esc(date)}</time></div><div class="rel-body">{summary}{parts}</div>'
            "</li>")


def changelog_html(facts: Facts, teaser: bool) -> str:
    items = facts.releases[:3] if teaser else facts.releases
    return f'<ol class="ledger">{"".join(release_html(r, not teaser) for r in items)}</ol>'


def roadmap_html(facts: Facts) -> str:
    out = []
    for title, intro, items in facts.roadmap:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        count = items.count("<li>")
        out.append(f'<section class="lane-block" id="{slug}" aria-labelledby="{slug}-h">'
                   f'<h2 id="{slug}-h" class="lane-title">{esc(title)} '
                   f'<span class="count">{count}</span></h2>{intro}'
                   f'<div class="lane-items">{items}</div></section>')
    return "\n".join(out)


# --- pages --------------------------------------------------------------------------------

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_-]+)(?::([^}]*))?\s*\}\}")


def page_meta(text: str, source: Path) -> tuple[dict[str, str], str]:
    m = re.match(r"\s*<!--(.*?)-->", text, re.S)
    if not m:
        raise BuildError(f"{rel(source)}: a page starts with <!-- title: ... description: ... -->")
    meta = dict(re.findall(r"^\s*(\w+):\s*(.+?)\s*$", m.group(1), re.M))
    for key in ("title", "description"):
        if key not in meta:
            raise BuildError(f"{rel(source)}: the header comment has no {key}")
    return meta, text[m.end():]


def fill(text: str, source: Path, facts: Facts, shots: Shots) -> str:
    def one(m: re.Match) -> str:
        name, arg = m.group(1), (m.group(2) or "").strip()
        line = text.count("\n", 0, m.start()) + 1
        where = f"{rel(source)}:{line}"
        args = [a.strip() for a in arg.split("|")] if arg else []
        if name == "version":
            return esc(facts.version)
        if name == "repo":
            return esc(REPO_URL)
        if name == "repo_name":
            return esc(REPO_URL.rstrip("/").rsplit("/", 1)[-1])
        if name == "code":
            return esc(repo_link(arg))
        if name == "shot":
            flags = set(args[2:])
            if flags - {"eager", "nolink"}:
                raise BuildError(f"{where}: a screenshot takes the flags eager and nolink")
            return shots.figure(args[0], args[1] if len(args) > 1 else "",
                                eager="eager" in flags, link="nolink" not in flags)
        if name == "status" and not arg:
            return facts.status
        if name == "apps":
            if arg == "first-party":
                return "\n".join(app_card(a, shots, True) for a in facts.apps)
            if arg == "examples":
                return "\n".join(app_card(a, shots, False) for a in facts.examples)
            if arg == "compact":
                return apps_compact(facts)
            if arg == "count":
                return words(len(facts.apps))
            if arg == "Count":
                return words(len(facts.apps)).capitalize()
            if arg == "names":
                return esc(join_or([a.name for a in facts.apps], "and"))
        if name == "consent":
            return consent_demo(facts, arg)
        if name == "roles":
            return roles_table(facts)
        if name == "role_names":
            return esc(join_or([r for r, _ in facts.roles], "and"))
        if name == "role_count":
            return words(len(facts.roles))
        if name == "providers":
            if arg == "local":
                return esc(join_or(facts.local_servers))
            if arg == "hosted":
                return esc(join_or(facts.hosted))
        if name == "rules":
            return facts.rules
        if name == "changelog":
            if arg == "teaser":
                return changelog_html(facts, teaser=True)
            if arg == "intro":
                return facts.changelog_intro
            if not arg:
                return changelog_html(facts, teaser=False)
        if name == "roadmap":
            if arg == "intro":
                return facts.roadmap_intro
            if not arg:
                return roadmap_html(facts)
        raise BuildError(f"{where}: unknown placeholder {m.group(0)}")

    return PLACEHOLDER.sub(one, text)


def nav_html(current: str) -> str:
    items = []
    for _, out, label, icon in NAV:
        href = "/" if out == "index.html" else f"/{out}"
        here = ' aria-current="page"' if out == current else ""
        items.append(f'<li><a href="{href}"{here}><svg aria-hidden="true"><use href="#i-{icon}"/>'
                     f"</svg><span>{esc(label)}</span></a></li>")
    return "".join(items)


def render_page(src_name: str, out_name: str, facts: Facts, shots: Shots, layout: str) -> str:
    source = SRC / "pages" / src_name
    meta, body = page_meta(source.read_text(encoding="utf-8"), source)
    body = fill(body, source, facts, shots)
    path = "/" if out_name == "index.html" else f"/{out_name}"
    title = meta["title"] if out_name == "index.html" else f"{meta['title']} · JimmyHQ"
    values = {
        "title": esc(title),
        "description": esc(meta["description"]),
        "canonical": esc(SITE_URL + path),
        "social_image": esc(f"{SITE_URL}/assets/social.png"),
        "csp": esc(CSP),
        "nav": nav_html(out_name),
        "body": body,
        "version": esc(facts.version),
        "repo": esc(REPO_URL),
        "page_class": esc(meta.get("class", "page")),
        "bg_light": esc(token(facts.tokens_light, "bg")),
        "bg_dark": esc(token(facts.tokens_dark, "bg")),
        "robots": '<meta name="robots" content="noindex">' if out_name == "404.html" else "",
    }
    page = re.sub(r"\[\[(\w+)\]\]", lambda m: values[m.group(1)], layout)
    left = re.search(r"\{\{|\}\}|\[\[", page)
    if left:
        raise BuildError(f"{rel(source)}: something was left unfilled near "
                         f"{page[max(0, left.start() - 40):left.start() + 40]!r}")
    return page


def build(out: Path, shots_dir: Path = DEFAULT_SHOTS, strict: bool = False,
          allow_unreleased: bool = False) -> list[str]:
    """Build the site into `out`. Returns the screenshots that were drawn as placeholders."""
    facts = read_facts()
    # Empty the folder rather than remove it, so a preview server running in it keeps working.
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()) and not (out / "assets" / "site.css").is_file():
        raise BuildError(f"{out} is not empty and is not an earlier build of the site; "
                         "choose an empty folder for --out")
    for old in out.iterdir():
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)
        else:
            old.unlink()
    (out / "assets").mkdir()
    shots = Shots(shots_dir, out, facts)
    layout = (SRC / "layout.html").read_text(encoding="utf-8")

    css = (SRC / "assets" / "site.css").read_text(encoding="utf-8")
    css = (css.replace("/* [[tokens_light]] */", facts.tokens_light)
              .replace("/* [[tokens_dark]] */", facts.tokens_dark)
              .replace("[[font_sans]]", facts.font_sans)
              .replace("[[font_mono]]", facts.font_mono))
    if "[[" in css:
        raise BuildError("site/src/assets/site.css: a [[...]] was left unfilled")
    (out / "assets" / "site.css").write_text(css, encoding="utf-8")
    for name in ("site.js", "social.png"):
        shutil.copyfile(SRC / "assets" / name, out / "assets" / name)
    shutil.copyfile(ROOT / "web" / "public" / "logo.svg", out / "assets" / "logo.svg")
    shutil.copyfile(ROOT / "web" / "public" / "logo.png", out / "assets" / "logo.png")

    for src_name, out_name, _, _ in NAV:
        (out / out_name).write_text(render_page(src_name, out_name, facts, shots, layout),
                                    encoding="utf-8")
    (out / NOT_FOUND[1]).write_text(render_page(*NOT_FOUND, facts, shots, layout),
                                    encoding="utf-8")

    urls = "".join(
        f"<url><loc>{esc(SITE_URL + ('/' if o == 'index.html' else '/' + o))}</loc></url>"
        for _, o, _, _ in NAV)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
        encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n",
                                    encoding="utf-8")

    # Every screenshot is in the output, used or not, so a release can be checked whole.
    for stem in SHOTS:
        shots.get(stem)
    if strict:
        problems = []
        if shots.missing:
            problems.append("missing screenshots in " + rel(shots_dir) + ": "
                            + ", ".join(f"{s}.png" for s in shots.missing))
        if facts.releases[0].date == "unreleased" and not allow_unreleased:
            problems.append(f"CHANGELOG.md: {facts.releases[0].version} is still 'unreleased'; "
                            "give it its date")
        if problems:
            raise BuildError("not ready for a release: " + "; ".join(problems))
    return shots.missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the JimmyHQ website.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the site")
    parser.add_argument("--shots", type=Path, default=DEFAULT_SHOTS,
                        help="the folder of screenshots (default: docs/screenshots)")
    parser.add_argument("--strict", action="store_true",
                        help="fail if a screenshot is missing or the newest release has no date")
    parser.add_argument("--allow-unreleased", action="store_true",
                        help="with --strict, accept a newest release still marked unreleased")
    args = parser.parse_args(argv)
    try:
        missing = build(args.out.resolve(), args.shots.resolve(), strict=args.strict,
                        allow_unreleased=args.allow_unreleased)
    except BuildError as error:
        print(f"site build failed: {error}", file=sys.stderr)
        return 1
    print(f"Built the site into {args.out}.")
    if missing:
        print(f"{len(missing)} screenshots are drawn placeholders: "
              + ", ".join(missing) + ". Run with --strict to require them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
