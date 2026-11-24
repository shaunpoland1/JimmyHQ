"""Every app that ships with JimmyHQ can load its own screens: each screen's page, and
every script and stylesheet it reaches (its ``<script src>`` and ``<link href>``, and the
static imports of those scripts, followed all the way), is answered by the app file
server under a mount. This is what would have caught the bridge script being served only
at the top of an app's files, when every full app's page sits under ``web/``.

A screen drawn by the shell from its description (``"entry": "described"``) has no page
of its own and is left out."""

from __future__ import annotations

import json
import posixpath
import re
import shutil
from pathlib import Path

import pytest

from apps_full_helpers import client_for
from apps_install_helpers import install_folder

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery"


def shipped() -> list[Path]:
    """Every app folder under apps/ and apps/examples/."""
    found = [p.parent for p in (ROOT / "apps").glob("*/jimmyhq.app.json")]
    found += [p.parent for p in (ROOT / "apps" / "examples").glob("*/jimmyhq.app.json")]
    return sorted(found)


_SCRIPT = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_LINK = re.compile(r"<link\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
_IMPORT = re.compile(r"""(?:^|[;\s])(?:import|export)\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""",
                     re.M)


def references(path: str, text: str) -> list[str]:
    """What a page or script names that the browser fetches from the app, as paths from
    the app's top. Addresses elsewhere and in-page anchors are not followed."""
    if path.endswith(".html"):
        found = _SCRIPT.findall(text) + _LINK.findall(text)
    elif path.endswith((".js", ".mjs")):
        found = _IMPORT.findall(text)
    else:
        found = []
    out = []
    for ref in found:
        if re.match(r"^[a-z][a-z0-9+.-]*:|^//|^#", ref, re.I):
            continue
        ref = ref.split("#")[0].split("?")[0]
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), ref))
        out.append(joined)
    return out


def test_references_are_read_from_pages_and_scripts_relative_to_where_each_is():
    page = '<link rel="stylesheet" href="style.css"><script type="module" src="./app.js"></script>'
    assert references("web/index.html", page) == ["web/app.js", "web/style.css"]
    script = ('import { hq } from "./_bridge.js";\nimport * as v from "../lib/x.js";\n'
              'export { y } from "./y.js";\nimport "./side.js";\nconst s = "import nothing";')
    assert references("web/views/a.js", script) == ["web/views/_bridge.js", "web/lib/x.js",
                                                    "web/views/y.js", "web/views/side.js"]
    assert references("web/index.html", '<script src="https://cdn.example.com/x.js">') == []


@pytest.mark.parametrize("folder", shipped(), ids=lambda p: p.name)
def test_every_screen_of_a_shipped_app_loads_with_all_it_references(core, tmp_path, folder):
    copy = tmp_path / "src" / folder.name
    shutil.copytree(folder, copy, ignore=shutil.ignore_patterns("__pycache__", ".*", "tests"))
    install_folder(core, copy)
    manifest = json.loads((folder / "jimmyhq.app.json").read_text(encoding="utf-8"))
    app_id = manifest["id"]
    pages = [s["entry"] for s in manifest.get("screens", []) if s.get("entry") != "described"]
    if not pages:
        pytest.skip(f"{folder.name} has only screens the shell draws from their description")
    with client_for(core) as web:
        assert web.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
        base = web.post(f"/api/apps/{app_id}/mount").json()["base"]
        seen: set[str] = set()
        waiting = list(pages)
        while waiting:
            path = waiting.pop()
            if path in seen:
                continue
            seen.add(path)
            answer = web.get(base + path)
            assert answer.status_code == 200, f"{folder.name}: {path} is not served"
            waiting += references(path, answer.text)
        assert any(p.endswith("_bridge.js") for p in seen), \
            f"{folder.name}'s screens never load the bridge"
