"""Run the simulator and print its report.

    uv run python -m jimmyhq.village.sim [--seeds 20] [--set energy.drain_per_min=0.1]
                                         [--out report.md]

It uses the default settings, with any ``--set`` changes on top, and touches no data.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from jimmyhq.village.world import DEFAULTS, getter

from .report import exit_tests, render
from .runner import run_all


def _overrides(pairs: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        if key not in DEFAULTS:
            raise SystemExit(f"There is no world setting called {key!r}. "
                             "See jimmyhq/village/world/defaults.py for the names.")
        try:
            out[key] = type(DEFAULTS[key])(value)
        except ValueError:
            raise SystemExit(f"{key} needs a number, not {value!r}.") from None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m jimmyhq.village.sim")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--out", default=None, help="also write the report to this file")
    args = ap.parse_args()
    overrides = _overrides(args.set)
    get = getter(overrides)
    t0 = time.monotonic()
    results = run_all(get, args.seeds)
    tests = exit_tests(results, get)
    md = render(results, tests, args.seeds, time.monotonic() - t0, overrides)
    sys.stdout.write(md)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(md.encode("utf-8"))
    return 0 if all(t["pass"] for t in tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
