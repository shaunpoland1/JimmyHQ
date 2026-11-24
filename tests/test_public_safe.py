"""The repo is public. These checks catch the obvious ways personal details leak in."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".toml", ".yml", ".yaml", ".html",
        ".css", ".svg", ".txt", ""}
SKIP = {"package-lock.json", "uv.lock", "LICENSE"}

PATTERNS = {
    "a home directory path": re.compile(r"(/Users/|/home/|C:\\Users\\)[A-Za-z0-9._-]+"),
    "a private network address": re.compile(
        r"\b(10\.\d{1,3}|192\.168|172\.(1[6-9]|2\d|3[01])|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7]))"
        r"\.\d{1,3}\.\d{1,3}\b"
    ),
    # Placeholders that start with "example-" are how the docs and tests name a machine.
    "a private network hostname": re.compile(
        r"(?<![\w-])(?!example-)[a-z0-9-]+\.(ts\.net|local|lan)\b(?!\.)"
    ),
    "an email address": re.compile(r"\b[\w.+-]+@(?!anthropic\.com|example\.)[\w-]+\.[a-z]{2,}\b"),
}


def _tracked_files() -> list[Path]:
    out = subprocess.run(  # noqa: S603, S607
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [ROOT / p for p in out]


def test_no_tracked_file_carries_a_home_path_private_address_or_email():
    this_file = Path(__file__).resolve()
    found = []
    for path in _tracked_files():
        if path.suffix not in TEXT or path.name in SKIP or path == this_file or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(ROOT)}:{line} has {label}: {match.group(0)}")
    assert not found, "\n".join(found)


def test_private_working_material_and_runtime_data_are_ignored_by_git():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("Reference/", "data/", "*.db", ".env", "CLAUDE.local.md"):
        assert entry in ignore, entry
    assert not [p for p in _tracked_files() if "Reference" in p.parts]


def test_the_private_hostname_check_allows_only_example_placeholders():
    pattern = PATTERNS["a private network hostname"]
    assert not pattern.search("https://example-machine.example-tailnet.ts.net:8440")
    assert pattern.search("https://someones-mac.tail1234.ts.net") is not None
    assert pattern.search("printer.local") is not None
