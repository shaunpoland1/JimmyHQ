"""The release guide and the script that runs it stay in step."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402


def test_the_guide_names_every_step_once_and_in_the_order_a_release_needs():
    every = release.steps(release.GUIDE.read_text(encoding="utf-8"))
    names = [name for name, _, _ in every]
    assert len(names) == len(set(names))
    wanted = ["set-variables", "install", "demo-data", "screenshots", "readme-tables",
              "checks-server", "checks-web", "site", "build"]
    assert [n for n in names if n in wanted] == wanted


def test_nothing_that_publishes_is_ever_run_by_the_script():
    every = release.steps(release.GUIDE.read_text(encoding="utf-8"))
    runs = release.script([s for s in every if s[2]])
    by_hand = [name for name, _, ok in every if not ok]
    assert {"snapshot", "tag", "deploy-site"} <= set(by_hand)
    for word in ("git push", "publish_snapshot", "git tag", "gh release"):
        assert word not in runs, word


def test_every_script_a_step_calls_exists():
    import re

    text = release.GUIDE.read_text(encoding="utf-8")
    for path in set(re.findall(r"uv run python ((?:scripts|site)/[\w/]+\.py)", text)):
        assert (ROOT / path).is_file(), path
