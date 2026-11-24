"""Temperament: rolled once at birth, never set by hand."""

from __future__ import annotations

import random
from typing import Any

from .body import NEEDS

RANGES = {
    "energy_drain_rate": (0.6, 1.4),
    "boredom_rate": (0.6, 1.4),
    "social_capacity": (0.5, 1.5),
    "frustration_tolerance": (0.5, 1.5),
    "risk_appetite": (0.0, 1.0),
}
# How the agent's model is sampled. A narrow band around common local-model defaults.
VOICE = {"temperature": (0.6, 0.85), "top_p": (0.75, 0.88)}
TOPICS = [
    "weather", "old maps", "woodworking", "birds", "typography", "trains", "astronomy",
    "cooking", "gardening", "board games", "architecture", "music theory", "rivers",
    "bicycles", "folk tales", "insects", "radio", "knots", "tea", "lighthouses",
]
HOTHEAD_BELOW = 0.8  # frustration_tolerance below this counts as low
SLEEP_OFFSET_MINUTES = (-60, 60)  # each agent's body clock runs this far off the wall clock


def roll(rng: random.Random) -> dict[str, Any]:
    t: dict[str, Any] = {k: round(rng.uniform(*r), 3) for k, r in RANGES.items()}
    w = [rng.random() + 0.05 for _ in NEEDS]
    s = sum(w)
    t["mood_weights"] = {n: round(x / s, 3) for n, x in zip(NEEDS, w, strict=True)}
    t["seed_curiosities"] = rng.sample(TOPICS, rng.randint(2, 3))
    t["voice"] = {k: round(rng.uniform(*r), 3) for k, r in VOICE.items()}
    t["sleep_offset_minutes"] = rng.randint(*SLEEP_OFFSET_MINUTES)
    return t


def roll_group(rng: random.Random, n: int, max_tries: int = 1000) -> list[dict[str, Any]]:
    """Roll n agents independently, rejecting groups where every agent is a hothead."""
    for _ in range(max_tries):
        group = [roll(rng) for _ in range(n)]
        if n < 2 or not all(g["frustration_tolerance"] < HOTHEAD_BELOW for g in group):
            return group
    raise RuntimeError("could not roll a workable group")
