"""The public website is built from the repo, so it must stay whole and true to the code."""

from __future__ import annotations

import importlib.util
import json
import re
import struct
import sys
import tomllib
import zlib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
build = importlib.util.module_from_spec(_spec)
sys.modules["site_build"] = build
_spec.loader.exec_module(build)

PAGES = [out for _, out, _, _ in build.NAV] + [build.NOT_FOUND[1]]
URL_ATTRS = {"href", "src", "srcset", "content", "action", "poster", "data"}
SVG_NAMESPACES = {"http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"}


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.refs: list[tuple[str, str, str]] = []  # (tag, attribute, value)
        self.metas: list[dict[str, str]] = []
        self.scripts: list[dict[str, str]] = []
        self.styles = 0
        self.style_attrs = 0
        self.inline_handlers: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "meta":
            self.metas.append(a)
        if tag == "script":
            self.scripts.append(a)
        if tag == "style":
            self.styles += 1
        if "style" in a:
            self.style_attrs += 1
        self.inline_handlers += [k for k in a if k.startswith("on")]
        for key, value in a.items():
            if key in URL_ATTRS and value:
                if key == "srcset":
                    for part in value.split(","):
                        self.refs.append((tag, key, part.strip().split(" ")[0]))
                else:
                    self.refs.append((tag, key, value))

    handle_startendtag = handle_starttag


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site") / "dist"
    shots = tmp_path_factory.mktemp("no-shots")
    build.build(out, shots)
    return out


def parsed(site: Path, name: str) -> Page:
    page = Page()
    page.feed((site / name).read_text(encoding="utf-8"))
    return page


def tiny_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_the_build_writes_every_page_the_sitemap_robots_and_the_404(site):
    for name in PAGES + ["sitemap.xml", "robots.txt", "assets/site.css", "assets/site.js",
                         "assets/social.png", "assets/logo.svg"]:
        assert (site / name).is_file(), name
    sitemap = (site / "sitemap.xml").read_text(encoding="utf-8")
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert len(locs) == len(build.NAV)
    assert all(loc.startswith(build.SITE_URL + "/") for loc in locs)
    assert f"Sitemap: {build.SITE_URL}/sitemap.xml" in (site / "robots.txt").read_text()


def test_every_internal_link_asset_and_anchor_in_the_output_resolves(site):
    ids = {name: parsed(site, name).ids for name in PAGES}
    broken = []
    for name in PAGES:
        for tag, attr, value in parsed(site, name).refs:
            if attr == "content" and not value.startswith("/"):
                continue  # meta text, or an absolute URL checked below
            if value.startswith(("https://", "mailto:")):
                continue
            if tag == "use" or value.startswith("#"):
                if value.lstrip("#") not in ids[name]:
                    broken.append(f"{name}: {value}")
                continue
            parts = urlsplit(value)
            path = unquote(parts.path)
            target = site / ("index.html" if path == "/" else path.lstrip("/"))
            if not target.is_file():
                broken.append(f"{name}: {value}")
            elif parts.fragment and target.suffix == ".html":
                if parts.fragment not in ids.get(target.name, set()):
                    broken.append(f"{name}: {value} (no such anchor)")
    assert not broken, "\n".join(broken)


def test_nothing_but_the_repo_and_the_site_itself_is_referenced_and_never_over_http(site):
    allowed = (build.REPO_URL, build.SITE_URL)
    found = []
    for name in PAGES:
        for _, attr, value in parsed(site, name).refs:
            if "://" in value and not value.startswith(allowed):
                found.append(f"{name}: {attr}={value}")
            if value.startswith("//"):
                found.append(f"{name}: {attr}={value}")
    css = (site / "assets" / "site.css").read_text(encoding="utf-8")
    js = (site / "assets" / "site.js").read_text(encoding="utf-8")
    assert "url(" not in css and "@import" not in css
    assert not [u for u in re.findall(r"https?://[^\s\"')]+", js) if u not in SVG_NAMESPACES]
    assert "fetch(" not in js and "XMLHttpRequest" not in js
    for svg in (site / "shots").glob("*.svg"):
        text = svg.read_text(encoding="utf-8")
        assert set(re.findall(r"https?://[^\s\"']+", text)) <= SVG_NAMESPACES, svg.name
    assert not found, "\n".join(found)


def test_no_link_is_put_inside_another_link(site):
    for name in PAGES:
        text = (site / name).read_text(encoding="utf-8")
        depth = 0
        for tag in re.findall(r"<(/?)a[\s>]", text):
            depth += -1 if tag else 1
            assert depth in (0, 1), name


def test_every_page_carries_a_strict_content_security_policy_it_can_live_with(site):
    for name in PAGES:
        page = parsed(site, name)
        csp = [m["content"] for m in page.metas
               if m.get("http-equiv", "").lower() == "content-security-policy"]
        assert csp == [build.CSP], name
        assert "default-src 'none'" in csp[0] and "'unsafe-inline'" not in csp[0]
        # The policy allows no inline code, so the pages must have none.
        assert all(s.get("src", "").startswith("/assets/") for s in page.scripts), name
        assert page.styles == 0 and page.style_attrs == 0, name
        assert not page.inline_handlers, name


def test_every_page_has_its_title_description_canonical_link_and_social_card(site):
    for name in PAGES:
        text = (site / name).read_text(encoding="utf-8")
        page = parsed(site, name)
        props = {m.get("property") or m.get("name"): m.get("content") for m in page.metas}
        assert re.search(r"<title>[^<]+</title>", text), name
        assert props.get("description") and props.get("og:title"), name
        assert props["og:image"] == f"{build.SITE_URL}/assets/social.png", name
        assert '<main id="main"' in text and 'href="#main"' in text, name


def test_the_version_on_the_site_is_the_project_version(site):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    for name in PAGES:
        assert f"v{version}" in (site / name).read_text(encoding="utf-8"), name


def test_the_apps_page_lists_exactly_the_apps_in_the_repo_with_their_words(site):
    text = (site / "apps.html").read_text(encoding="utf-8")
    first = section_ids(text, "first-h")
    paths = sorted((ROOT / "apps").glob("*/jimmyhq.app.json"))
    manifests = [json.loads(p.read_text()) for p in paths]
    assert first == [m["id"] for m in manifests]
    for m in manifests:
        assert build.esc(m["name"]) in text and build.esc(m["summary"]) in text
        assert f"v{m['version']}" in text
        assert f'class="chip tier tier-{m["tier"]}"' in text
    examples = sorted((ROOT / "apps" / "examples").glob("*/jimmyhq.app.json"))
    assert section_ids(text, "ex-h") == [json.loads(p.read_text())["id"] for p in examples]


def section_ids(text: str, heading_id: str) -> list[str]:
    start = text.index(f'id="{heading_id}"')
    end = text.find("</section>", start)
    return re.findall(r'<article class="app panel" id="([^"]+)"', text[start:end])


def test_a_full_app_shows_the_install_dialogs_warning_first(site):
    text = (site / "apps.html").read_text(encoding="utf-8")
    perms = build.load_permissions()
    warning = perms.BY_ID[perms.RUNS_AS_YOU].sentence
    for block in re.findall(r'<article class="app panel".*?</article>', text, re.S):
        lines = re.findall(r'<li class="(strong)?">([^<]*)</li>', block)
        if "tier-full" in block:
            assert lines[0] == ("strong", build.esc(warning))
        else:
            assert all(cls == "" for cls, _ in lines)


def test_the_newest_dev_log_entry_is_the_project_version(site):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    _, releases = build.read_changelog()
    assert releases[0].version == version, (
        f"CHANGELOG.md's newest entry is {releases[0].version}; the project is {version}")
    devlog = (site / "devlog.html").read_text(encoding="utf-8")
    assert f'id="v{version}"' in devlog
    assert f'id="v{version}"' in (site / "index.html").read_text(encoding="utf-8")


def test_the_backlog_shows_every_section_the_roadmap_promises(site):
    page = parsed(site, "backlog.html")
    for title in build.ROADMAP_SECTIONS:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        assert slug in page.ids, title


def test_the_site_uses_the_apps_own_colour_tokens(site):
    css = (site / "assets" / "site.css").read_text(encoding="utf-8")
    app = (ROOT / "web" / "src" / "index.css").read_text(encoding="utf-8")
    for name, value in re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})", app):
        assert f"{name}: {value}" in css, name


def test_the_roles_and_providers_on_the_site_are_the_ones_in_the_code(site):
    home = (site / "index.html").read_text(encoding="utf-8")
    shapes = (ROOT / "server" / "jimmyhq" / "models" / "shapes.py").read_text()
    roles = re.search(r"^ROLES = \(([^)]*)\)", shapes, re.M).group(1)
    for role in re.findall(r'"(\w+)"', roles):
        assert f'<span class="role-name">{role}</span>' in home
    for name in ("Ollama", "Anthropic", "OpenAI", "AWS Bedrock"):
        assert name in home


def test_a_missing_screenshot_is_drawn_and_a_present_one_is_copied(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "bench.png").write_bytes(tiny_png(144, 90))
    out = tmp_path / "dist"
    missing = build.build(out, shots)
    assert "bench" not in missing and "tasks" in missing
    assert (out / "shots" / "bench.png").read_bytes() == (shots / "bench.png").read_bytes()
    assert not (out / "shots" / "bench.svg").exists()
    svg = (out / "shots" / "tasks.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 1440 900"' in svg
    assert 'viewBox="0 0 390 844"' in (out / "shots" / "phone-bench.svg").read_text()
    home = (out / "index.html").read_text(encoding="utf-8")
    assert 'src="/shots/bench.png" width="144" height="90"' in home


def test_strict_fails_when_a_screenshot_is_missing(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    for stem, (w, h, _) in build.SHOTS.items():
        if stem != "wall":
            (shots / f"{stem}.png").write_bytes(tiny_png(w // 10, h // 10))
    with pytest.raises(build.BuildError, match=r"missing screenshots .*wall\.png"):
        build.build(tmp_path / "dist", shots, strict=True)
    assert build.main(["--out", str(tmp_path / "dist2"), "--shots", str(shots), "--strict"]) == 1


def test_strict_passes_with_every_screenshot_when_an_unreleased_version_is_allowed(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    for stem, (w, h, _) in build.SHOTS.items():
        (shots / f"{stem}.png").write_bytes(tiny_png(w // 10, h // 10))
    assert build.build(tmp_path / "dist", shots, strict=True, allow_unreleased=True) == []
    try:
        build.build(tmp_path / "dist", shots, strict=True)
    except build.BuildError as error:
        assert "screenshot" not in str(error) and "unreleased" in str(error)


def test_a_screenshot_taken_at_twice_the_size_is_shown_at_the_size_it_stands_for(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "village.png").write_bytes(tiny_png(2880, 1800))
    (shots / "bench.png").write_bytes(tiny_png(1440, 900))
    (shots / "bench-light.png").write_bytes(tiny_png(1440, 900))
    build.build(tmp_path / "dist", shots)
    home = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'src="/shots/village.png" width="1440" height="900"' in home
    # A light picture is offered to a visitor in the light theme, where there is one.
    assert ('<source media="(prefers-color-scheme: light)" srcset="/shots/bench-light.png"'
            in home)


def test_the_tour_carries_the_readmes_project_status_and_its_picture_descriptions(site):
    tour = (site / "tour.html").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Solid enough to try" in readme and "<strong>Solid enough to try:</strong>" in tour
    alt = re.search(r'<img src="docs/screenshots/tape\.png" alt="([^"]+)"', readme)
    if alt:
        assert f'alt="{alt.group(1)}"' in tour


def test_the_build_will_not_empty_a_folder_that_is_not_a_site(tmp_path):
    (tmp_path / "keep.txt").write_text("mine")
    with pytest.raises(build.BuildError, match="not empty"):
        build.build(tmp_path, tmp_path / "no-shots")
    assert (tmp_path / "keep.txt").read_text() == "mine"


@pytest.mark.parametrize("text, problem", [
    ("| a | b |\n", "a table"),
    ("```\ncode\n```\n", "a code fence"),
    ("<b>hi</b>\n", "HTML"),
    ("See [this](https://example.com/page).\n", "links to example.com"),
    ("See [this](http://example.com/).\n", "plain http"),
    ("A `stray backtick.\n", "unmatched backtick"),
])
def test_the_markdown_reader_refuses_what_it_does_not_understand(tmp_path, text, problem):
    source = tmp_path / "x.md"
    with pytest.raises(build.BuildError, match=problem):
        blocks = build.parse_markdown(text, source)
        build.render_blocks(blocks, source)


def test_the_markdown_reader_escapes_text_and_points_relative_links_at_the_repo(tmp_path):
    source = tmp_path / "x.md"
    blocks = build.parse_markdown("## A & B\n\n- one `<x>` **two**\n  more [g](docs/a.md#s)\n",
                                  source)
    html = build.render_blocks(blocks, source)
    assert "<h2>A &amp; B</h2>" in html
    assert "<code>&lt;x&gt;</code>" in html and "<strong>two</strong>" in html
    assert f'href="{build.REPO_URL}/blob/main/docs/a.md#s"' in html
    assert "more" in html
