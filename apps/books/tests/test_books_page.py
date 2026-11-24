"""The page's own logic passes its node tests. What this cannot check: the screens, the real
bridge and the shell's save (a refusal for a file over the owner's limit): those need a browser."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_the_pages_own_logic_passes_its_node_tests():
    here = Path(__file__).resolve().parent
    done = subprocess.run(
        [NODE or "node", "--no-warnings", "--test", str(here / "page_lib.test.mjs")],
        capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-3000:]


def test_every_script_of_the_page_parses_as_a_module(tmp_path):
    """A page script with a stray bracket leaves the screen on "Loading..." for ever with no
    sign of why (the browser says nothing the owner sees), so each one is parsed here."""
    web = Path(__file__).resolve().parent.parent / "web"
    scripts = sorted(web.rglob("*.js"))
    assert scripts, "the page has scripts"
    for number, script in enumerate(scripts):
        copy = tmp_path / f"script{number}.mjs"
        copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
        done = subprocess.run([NODE or "node", "--check", str(copy)],
                              capture_output=True, text=True, timeout=60, check=False)
        assert done.returncode == 0, f"{script.relative_to(web)}: {done.stderr[-800:]}"
