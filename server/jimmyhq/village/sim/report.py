"""The simulator's exit tests and its Markdown report.

The exit tests are the bar a change to the body's rules or numbers must clear.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from jimmyhq.village.world import Getter


def _sum(rs: list[dict[str, Any]], key: str) -> int:
    return sum(r[key] for r in rs)


def exit_tests(results: dict[str, list[dict[str, Any]]], get: Getter) -> list[dict[str, Any]]:
    tests = []

    def add(name: str, ok: bool, detail: str) -> None:
        tests.append({"name": name, "pass": bool(ok), "detail": detail})

    all_runs = [r for rs in results.values() for r in rs]
    pinned: Counter = Counter()
    for r in all_runs:
        for k in r["pinned"]:
            pinned[k.split(":", 1)[1]] += 1
    add("No need pinned at 0 or 100 for 2h+ while awake", not pinned,
        "none" if not pinned else ", ".join(f"{k} in {v} runs" for k, v in pinned.most_common()))
    unexplained = _sum(all_runs, "unexplained_changes")
    add("Every need change has a cause and evidence", unexplained == 0,
        f"{unexplained} unexplained changes")

    trap = results.get("loop_trap", [])
    traps, sa = _sum(trap, "traps"), _sum(trap, "set_asides_on_traps")
    add("Set-asides fire on loops", traps > 0 and sa >= 0.8 * traps,
        f"{sa} set-asides across {traps} traps hit")
    healthy = results.get("healthy_repetition", [])
    add("Healthy repetition is not punished",
        _sum(healthy, "set_asides_on_healthy") == 0 and _sum(healthy, "rejects_on_scroll") == 0,
        f"{_sum(healthy, 'set_asides_on_healthy')} set-asides, "
        f"{_sum(healthy, 'rejects_on_scroll')} scroll refusals")
    honest = sum(r["flags"].get("claim_contradicted", 0) + r["flags"].get("body_artifact", 0)
                 for r in all_runs)
    add("The body never contradicts its own log", honest == 0,
        f"{honest} platform-fault divergence flags across {len(all_runs)} simulated runs")
    slow = results.get("slow_owner", [])
    cap = get("frustration.wait_cap_total")
    outlet = get("modes.outlet_at_frustration")
    worst = max((m for r in slow for m in r["wait_frustration_max"].values()), default=0)
    add("Waiting on the owner never forces an outlet by itself",
        worst <= cap + 1e-6 and worst < outlet,
        f"max frustration caused by waiting in slow_owner = {worst} "
        f"(cap {cap}, outlet at {outlet})")
    return tests


def _agg_mode(rs: list[dict[str, Any]]) -> str:
    c: Counter = Counter()
    n = 0
    for r in rs:
        for shares in r["mode_share"].values():
            c.update(shares)
            n += 1
    return ", ".join(f"{m} {v / n:.0%}" for m, v in c.most_common())


def _agg_need(rs: list[dict[str, Any]], need: str) -> str:
    vals = [r["need_stats"][f"{a}:{need}"] for r in rs for a in r["agents"]
            if r["need_stats"][f"{a}:{need}"]]
    if not vals:
        return "-"
    return (f"{min(v['min'] for v in vals):.0f} / "
            f"{sum(v['mean'] for v in vals) / len(vals):.0f} / "
            f"{max(v['max'] for v in vals):.0f}")


def render(results: dict[str, list[dict[str, Any]]], tests: list[dict[str, Any]],
           seeds: int, secs: float, overrides: Mapping[str, Any] | None = None) -> str:
    lines = ["# Village simulator report", ""]
    passed = sum(t["pass"] for t in tests)
    lines += [f"Seeds per scenario: {seeds}. Runtime {secs:.0f}s. "
              f"Exit tests passed: **{passed}/{len(tests)}**.", ""]
    lines += ["## Exit tests", "", "| Test | Result | Detail |", "|---|---|---|"]
    for t in tests:
        lines.append(f"| {t['name']} | {'PASS' if t['pass'] else 'FAIL'} | {t['detail']} |")
    lines += ["", "## Scenarios", ""]
    lines += ["| Scenario | Completions | Submissions | Set-asides (traps/other) | Refusals "
              "| Naps | Flags | Modes |", "|---|---|---|---|---|---|---|---|"]
    for name, rs in results.items():
        rej: Counter = Counter()
        flags: Counter = Counter()
        naps = 0
        for r in rs:
            rej.update(r["rejects"])
            flags.update(r["flags"])
            naps += r["sleeps"].get("nap", 0)
        lines.append(f"| {name} | {_sum(rs, 'completions')} | {_sum(rs, 'submissions')} | "
                     f"{_sum(rs, 'set_asides_on_traps')}/{_sum(rs, 'set_asides_on_healthy')} | "
                     f"{dict(rej) or 0} | {naps} | {dict(flags) or 0} | {_agg_mode(rs)} |")
    lines += ["", "Need ranges while awake (min / mean / max across all runs):", "",
              "| Scenario | Energy | Frustration | Social | Curiosity | Purpose |",
              "|---|---|---|---|---|---|"]
    for name, rs in results.items():
        lines.append(f"| {name} | " + " | ".join(
            _agg_need(rs, n) for n in ("energy", "frustration", "social", "curiosity",
                                       "purpose")) + " |")
    lines += ["", "Feelings reported by the bodies:", ""]
    for name, rs in results.items():
        c: Counter = Counter()
        for r in rs:
            c.update(r["feelings"])
        lines.append(f"- {name}: {dict(c.most_common())}")
    lines += ["", "## Settings used", "", "Changed from the defaults: " +
              (", ".join(f"{k}={v}" for k, v in (overrides or {}).items()) or "none")]
    return "\n".join(lines) + "\n"
