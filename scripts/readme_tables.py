"""Keeps the parts of README.md that come from the code in step with the code.

    uv run python scripts/readme_tables.py --check    # exit 1 and say what differs
    uv run python scripts/readme_tables.py --write    # rewrite those parts in place

Three parts, each between marker comments in README.md:

- the table of first-party apps, built from `apps/*/jimmyhq.app.json`
  (`<!-- apps-table:start -->` ... `<!-- apps-table:end -->`);
- the links to those apps' guides, one for each app that has a `docs/<id>.md`
  (`<!-- apps-guides:start -->` ... `<!-- apps-guides:end -->`, inside a sentence);
- the version number, taken from `pyproject.toml`
  (`<!-- version:start -->` ... `<!-- version:end -->`).

Nothing else in README.md is touched. The tests in `tests/test_readme_current.py` run the
same `--check`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
BLOCKS = ("apps-table", "apps-guides", "version")
INLINE = ("apps-guides", "version")  # written inside a sentence, with no line breaks


def manifests(root: Path = ROOT) -> list[dict]:
    """Every first-party app's manifest, by name. `apps/examples/` holds examples, not apps."""
    found = []
    for path in sorted((root / "apps").glob("*/jimmyhq.app.json")):
        found.append(json.loads(path.read_text(encoding="utf-8")))
    return sorted(found, key=lambda m: m["name"].lower())


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    match = re.match(r".+?[.!?](?=\s|$)", text)
    return (match.group(0) if match else text).replace("|", "\\|")


def apps_table(root: Path = ROOT) -> str:
    rows = ["| App | What it does | Tier | Private store | Version |",
            "|---|---|---|---|---|"]
    for m in manifests(root):
        rows.append(f"| **{m['name']}** | {first_sentence(m['summary'])} | {m['tier']} | "
                    f"{'yes' if m.get('private') else 'no'} | {m['version']} |")
    return "\n".join(rows)


def apps_guides(root: Path = ROOT) -> str:
    """Links to the guide of each app that has one, in name order, as a list that ends the
    sentence it is in: `[A](docs/a.md), [B](docs/b.md) and [C](docs/c.md)`."""
    links = [f"[{m['name']}](docs/{m['id']}.md)" for m in manifests(root)
             if (root / "docs" / f"{m['id']}.md").is_file()]
    if len(links) < 2:
        return "".join(links)
    return ", ".join(links[:-1]) + " and " + links[-1]


def project_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def wanted(root: Path = ROOT) -> dict[str, str]:
    return {"apps-table": apps_table(root), "apps-guides": apps_guides(root),
            "version": project_version(root)}


def pattern(name: str) -> re.Pattern:
    return re.compile(rf"(<!-- {name}:start -->)(.*?)(<!-- {name}:end -->)", re.DOTALL)


def current(text: str, name: str) -> str | None:
    """What README holds between a block's markers, or None when a marker is missing."""
    found = pattern(name).search(text)
    return found.group(2).strip("\n") if found else None


def render(text: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        inline = name in INLINE
        glue = "" if inline else "\n"
        text = pattern(name).sub(
            lambda m, glue=glue, value=value: f"{m.group(1)}{glue}{value}{glue}{m.group(3)}",
            text)
    return text


def problems(text: str, values: dict[str, str]) -> list[str]:
    out = []
    for name, value in values.items():
        have = current(text, name)
        if have is None:
            out.append(f"README.md has no `<!-- {name}:start -->` and `<!-- {name}:end -->` "
                       "markers. Add them where the generated part belongs.")
        elif have.strip() != value.strip():
            out.append(f"The `{name}` part of README.md is out of date. Run "
                       "`uv run python scripts/readme_tables.py --write`.")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="exit 1 when README is out of date")
    mode.add_argument("--write", action="store_true", help="rewrite the generated parts")
    args = parser.parse_args(argv)
    text = README.read_text(encoding="utf-8")
    values = wanted()
    if args.check:
        found = problems(text, values)
        for line in found:
            print(line, file=sys.stderr)
        return 1 if found else 0
    missing = [p for p in problems(text, values) if "markers" in p]
    if missing:
        for line in missing:
            print(line, file=sys.stderr)
        return 1
    new = render(text, values)
    if new != text:
        README.write_text(new, encoding="utf-8", newline="\n")
        print("README.md updated.")
    else:
        print("README.md is already current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
