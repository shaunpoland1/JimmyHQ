"""README.md is the first page a stranger reads, so what it says about the code is checked
against the code: the apps table, the version, every picture and every link."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SHOTS = ROOT / "docs" / "screenshots"


def _load_tables():
    spec = importlib.util.spec_from_file_location(
        "readme_tables", ROOT / "scripts" / "readme_tables.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tables = _load_tables()


def _tracked() -> set[str]:
    out = subprocess.run(  # noqa: S603, S607
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    return set(out)


def _is_file_in_repo(relative: str, tracked: set[str]) -> bool:
    relative = relative.rstrip("/")
    return relative in tracked or any(name.startswith(relative + "/") for name in tracked)


def _links(text: str) -> list[str]:
    """Targets of Markdown links and images, and of `src` and `href` in raw HTML."""
    found = re.findall(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text)
    found += re.findall(r"""(?:src|href)=["']([^"']+)["']""", text)
    return found


def _relative(target: str) -> bool:
    return not re.match(r"^([a-z][a-z0-9+.-]*:|//|#|mailto:)", target, re.IGNORECASE)


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(markdown: str) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    in_code = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        match = None if in_code else re.match(r"^#{1,6}\s+(.*)$", line)
        if match:
            slug = _slug(match.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            out.add(slug if count == 0 else f"{slug}-{count}")
    return out


def test_the_apps_table_in_the_readme_is_what_the_manifests_say():
    text = README.read_text(encoding="utf-8")
    found = tables.problems(text, {"apps-table": tables.apps_table()})
    assert not found, "\n".join(found)


def test_the_apps_table_has_a_row_for_every_first_party_app():
    table = tables.apps_table()
    manifests = tables.manifests()
    assert len(manifests) >= 7  # the apps that ship in every copy
    for manifest in manifests:
        assert f"**{manifest['name']}**" in table


def test_the_version_in_the_readme_is_the_projects_version():
    text = README.read_text(encoding="utf-8")
    found = tables.problems(text, {"version": tables.project_version()})
    assert not found, "\n".join(found)
    from jimmyhq import __version__

    assert __version__ == tables.project_version()


def test_the_readme_check_tool_passes_when_run_the_way_the_release_steps_run_it():
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "readme_tables.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_check_names_a_part_that_is_out_of_date():
    text = README.read_text(encoding="utf-8")
    stale = tables.render(text, {"apps-table": "| nothing |", "version": "9.9.9"})
    found = tables.problems(stale, tables.wanted())
    assert len(found) == 2
    assert tables.render(stale, tables.wanted()) == text


def test_every_relative_link_and_picture_in_the_readme_is_a_file_in_the_repo():
    tracked = _tracked()
    missing = []
    for target in _links(README.read_text(encoding="utf-8")):
        if not _relative(target):
            continue
        path = target.split("#", 1)[0].split("?", 1)[0]
        if path and not _is_file_in_repo(path, tracked):
            missing.append(target)
    assert not missing, f"README.md links to files that are not in the repo: {missing}"


def test_every_link_to_a_heading_in_the_readme_or_a_guide_finds_the_heading():
    problems = []
    for source in [README, *sorted((ROOT / "docs").glob("*.md"))]:
        text = source.read_text(encoding="utf-8")
        for target in _links(text):
            if not _relative(target) and not target.startswith("#"):
                continue
            path, _, anchor = target.partition("#")
            if not anchor:
                continue
            page = source if not path else (source.parent / path).resolve()
            if page.suffix != ".md" or not page.is_file():
                continue
            if anchor not in _anchors(page.read_text(encoding="utf-8")):
                problems.append(f"{source.name}: {target}")
    # The guides were written before this check, so only README's own links must all hold.
    readme_problems = [p for p in problems if p.startswith("README.md")]
    assert not readme_problems, readme_problems


def test_every_screenshot_in_the_folder_is_used_by_the_readme_or_a_guide():
    used = set()
    for source in [README, *sorted((ROOT / "docs").glob("*.md"))]:
        for target in _links(source.read_text(encoding="utf-8")):
            used.add(Path(target.split("#", 1)[0]).name)
    unused = sorted(p.name for p in SHOTS.glob("*.png") if p.name not in used)
    assert not unused, f"docs/screenshots has pictures nothing shows: {unused}"


def test_every_picture_the_readme_shows_from_the_screenshot_folder_exists():
    text = README.read_text(encoding="utf-8")
    shown = [t for t in _links(text) if "docs/screenshots/" in t]
    assert shown, "README.md should show screenshots"
    for target in shown:
        assert (ROOT / target).is_file(), target


@pytest.mark.parametrize("name", [
    "today", "bench", "tape", "tasks", "chat", "village", "memory", "apps", "settings", "wall",
    "app-books", "app-posts", "app-packages", "app-heard", "app-imagery", "today-light",
    "bench-light", "phone-today", "phone-bench"])
def test_the_screenshot_other_pages_rely_on_exists_and_is_a_small_png(name):
    path = SHOTS / f"{name}.png"
    assert path.is_file(), f"{path.name} is missing: run scripts/take_screenshots.py"
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) <= 500_000, f"{path.name} is {len(data) // 1000} KB; keep each under 500 KB"
