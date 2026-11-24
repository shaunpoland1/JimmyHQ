"""Prices, and turning token counts into estimated money when a screen asks.

Events store tokens only. Prices change and differ between owners, so cost is
worked out here, at read time, from whichever table is current.
"""

from __future__ import annotations

import tomllib
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path
from typing import Any

from jimmyhq.models.lenses import HOUR_MS, OTHER

_DEFAULTS = Path(__file__).with_name("default_prices.toml")


@cache
def _shipped() -> tuple[dict[str, Any], ...]:
    return tuple(tomllib.loads(_DEFAULTS.read_text(encoding="utf-8")).get("prices", []))


def default_prices() -> list[dict[str, Any]]:
    """The example table shipped in the package, used until the owner saves one."""
    return [dict(entry) for entry in _shipped()]


def find_price(prices: list[dict], provider_id: str, provider_type: str,
               model: str) -> dict | None:
    """The first line that matches wins, so the owner controls precedence by order."""
    for entry in prices:
        if entry["provider"] in (provider_id, provider_type, "*") and fnmatchcase(
            model, entry["model"]
        ):
            return entry
    return None


def _money(tokens_in: int, tokens_out: int, entry: dict) -> float:
    return (tokens_in * entry["input_per_mtok"] + tokens_out * entry["output_per_mtok"]) / 1e6


def summarise(buckets: list[dict], prices: list[dict], reference: dict | None,
              top: int = 10) -> dict[str, Any]:
    """Add up usage buckets and price them.

    Work on the owner's own machines costs nothing, so only hosted tokens are
    priced. ``saved_by_local`` is what the local tokens would have cost at the
    reference line, or None when no reference is chosen.
    """
    rows: dict[str, list[int]] = {}
    embeds: dict[str, list[int]] = {}
    fallbacks = failed = 0
    for bucket in buckets:
        fallbacks += bucket["fb"]
        failed += bucket["fail"]
        for key, row in bucket.get("e", {}).items():
            total = embeds.setdefault(key, [0, 0, 0, 0])
            for i, value in enumerate(row):
                total[i] += value
        for key, row in bucket["m"].items():
            total = rows.setdefault(key, [0, 0, 0, 0, 0, 0])
            for i, value in enumerate(row):
                total[i] += value
    models = []
    cost = 0.0
    unpriced = 0
    for key, (calls, t_in, t_out, l_calls, l_in, l_out) in rows.items():
        provider_id, provider_type, model = key.split("\n", 2)
        # The row that gathers every model past the cap has no one price.
        entry = None if key == OTHER else find_price(prices, provider_id, provider_type, model)
        hosted_calls = calls - l_calls
        if hosted_calls == 0:
            model_cost: float | None = 0.0
        elif entry is None:
            model_cost = None
            unpriced += hosted_calls
        else:
            model_cost = _money(t_in - l_in, t_out - l_out, entry)
            cost += model_cost
        models.append({
            "provider_id": provider_id, "provider_type": provider_type, "model": model,
            "calls": calls, "input_tokens": t_in, "output_tokens": t_out,
            "local_calls": l_calls,
            "estimated_cost": None if model_cost is None else round(model_cost, 6),
        })
    models.sort(key=lambda m: (-(m["input_tokens"] + m["output_tokens"]), m["model"]))
    # Embeddings: priced at a line's input price, since there is no output.
    embed_cost = 0.0
    embed_unpriced = 0
    for key, (calls, tokens, l_calls, l_tokens) in embeds.items():
        provider_id, provider_type, model = key.split("\n", 2)
        entry = None if key == OTHER else find_price(prices, provider_id, provider_type, model)
        if calls - l_calls == 0:
            continue
        if entry is None:
            embed_unpriced += calls - l_calls
        else:
            embed_cost += _money(tokens - l_tokens, 0, entry)
    local_in = sum(r[4] for r in rows.values())
    local_out = sum(r[5] for r in rows.values())
    return {
        "calls": sum(r[0] for r in rows.values()),
        "input_tokens": sum(r[1] for r in rows.values()),
        "output_tokens": sum(r[2] for r in rows.values()),
        "local_calls": sum(r[3] for r in rows.values()),
        "local_input_tokens": local_in,
        "local_output_tokens": local_out,
        "fallback_calls": fallbacks,
        "failed_calls": failed,
        # Chat and embeddings together. The embeddings share is also given below.
        "estimated_cost": round(cost + embed_cost, 6),
        "unpriced_calls": unpriced,
        "embeddings": {
            "calls": sum(r[0] for r in embeds.values()),
            "input_tokens": sum(r[1] for r in embeds.values()),
            "local_calls": sum(r[2] for r in embeds.values()),
            "local_input_tokens": sum(r[3] for r in embeds.values()),
            "estimated_cost": round(embed_cost, 6),
            "unpriced_calls": embed_unpriced,
        },
        "saved_by_local": (None if reference is None
                           else round(_money(local_in, local_out, reference), 6)),
        "models": models[:top],
    }


def usage_report(state: dict, prices: list[dict], reference: dict | None,
                 now_ms: int) -> dict[str, Any]:
    first_hour = now_ms // HOUR_MS - 23  # this hour and the 23 before it
    recent = [b for h, b in state["hours"].items() if int(h) >= first_hour]
    days = []
    for date in sorted(state["days"]):
        day = summarise([state["days"][date]], prices, reference, top=0)
        days.append({k: day[k] for k in ("calls", "input_tokens", "output_tokens",
                                         "local_calls", "failed_calls", "estimated_cost")}
                    | {"date": date})
    return {
        "all_time": summarise([state["all"]], prices, reference),
        "last_24h": summarise(recent, prices, reference),
        "days": days,
    }
