"""Run the release steps written in docs/releasing.md, in order, stopping at the first that
fails.

    uv run python scripts/release.py            # every step a script may run
    uv run python scripts/release.py --list
    uv run python scripts/release.py --from readme-tables

The steps are the fenced ``bash`` blocks in that guide whose first line is ``# step: name``.
A step marked ``(needs a person)`` is never run: publishing is a person's decision. All steps
run in one shell, so the variables the first step sets reach the rest.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "releasing.md"
BLOCK = re.compile(r"```bash\n# step: ([^\n]+)\n(.*?)```", re.S)
BY_HAND = "(needs a person)"


def steps(text: str) -> list[tuple[str, str, bool]]:
    """Each step's name, its commands, and whether a script may run it."""
    found = []
    for title, body in BLOCK.findall(text):
        by_hand = BY_HAND in title
        found.append((title.replace(BY_HAND, "").strip(), body.strip(), not by_hand))
    return found


def script(chosen: list[tuple[str, str, bool]]) -> str:
    """One shell script that runs the chosen steps and stops at the first failure."""
    lines = ["set -euo pipefail"]
    for name, body, _ in chosen:
        lines += [f"echo '== {name} =='", body]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="show the steps and stop")
    parser.add_argument("--from", dest="start", default=None, help="start at this step")
    args = parser.parse_args(argv)
    every = steps(GUIDE.read_text(encoding="utf-8"))
    if args.list:
        for name, _, runs in every:
            print(f"{name}{'' if runs else '   ' + BY_HAND}")
        return 0
    names = [name for name, _, _ in every]
    first = every[0][0] if every else ""
    if args.start is not None and args.start not in names:
        print(f"There is no step called {args.start!r}. The steps are: {', '.join(names)}.",
              file=sys.stderr)
        return 2
    at = names.index(args.start) if args.start else 0
    # The first step sets the variables the others need, so it always runs.
    chosen = [s for n, s in enumerate(every) if s[2] and (n >= at or s[0] == first)]
    done = subprocess.run(["bash", "-c", script(chosen)], cwd=ROOT, check=False)  # noqa: S603, S607
    if done.returncode == 0:
        left = [name for name, _, runs in every if not runs]
        print("\nEvery step passed. Left for a person: " + ", ".join(left) + ".")
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
