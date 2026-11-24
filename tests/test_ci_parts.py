"""The Windows job cuts the test suite into parts that run side by side. A test file that
fell between the parts would never run there, and one in two parts would run twice."""

from __future__ import annotations

import glob
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PART = re.compile(r'- \{name: "(?P<name>[^"]+)", paths: "(?P<paths>[^"]+)"\}')


def windows_parts() -> dict[str, list[str]]:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    section = text[text.index("server-windows:"):text.index("\n  web:")]
    return {m["name"]: m["paths"].split() for m in PART.finditer(section)}


def files_of(patterns: list[str]) -> set[str]:
    found: set[str] = set()
    for pattern in patterns:
        found.update(Path(p).as_posix() for p in glob.glob(str(ROOT / pattern)))
    return {Path(p).relative_to(ROOT).as_posix() for p in found}


def test_every_pattern_of_the_windows_run_matches_a_file_or_pytest_would_stop():
    for name, paths in windows_parts().items():
        for pattern in paths:
            if pattern != "apps":
                assert glob.glob(str(ROOT / pattern)), f"'{pattern}' in '{name}' matches no file"


def test_every_test_file_is_in_exactly_one_part_of_the_windows_run():
    parts = {name: files_of(paths) for name, paths in windows_parts().items()
             if all(path.startswith("tests/") for path in paths)}
    assert len(parts) >= 4, parts.keys()
    every = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")}
    seen: dict[str, str] = {}
    for name, files in parts.items():
        for file in files:
            assert file not in seen, f"{file} is in both '{seen[file]}' and '{name}'"
            seen[file] = name
    assert every - set(seen) == set(), f"in no part of the Windows run: {sorted(every - set(seen))}"


def test_the_apps_tests_are_cut_into_two_parts_that_neither_holds_everything():
    parts = windows_parts()
    a_parts = {name: files_of(paths) for name, paths in parts.items()
               if any(path.startswith("tests/test_a") for path in paths)}
    assert len(a_parts) == 2
    sizes = sorted(len(files) for files in a_parts.values())
    assert sizes[0] >= 20, a_parts  # two parts of some weight, not one file split off
    assert all(any(f.startswith("tests/test_apps_") for f in files) for files in a_parts.values())


def test_the_part_that_runs_the_apps_folder_is_still_there():
    assert windows_parts()["apps"] == ["apps"]
