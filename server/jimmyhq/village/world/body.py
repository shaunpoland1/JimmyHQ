"""Needs and mood. Mood is computed from needs, never set."""

from __future__ import annotations

NEEDS = ("energy", "frustration", "social", "curiosity", "purpose")

# +1: high is satisfied. -1: high is an unmet urge or a bad feeling.
POLARITY = {"energy": +1, "frustration": -1, "social": +1, "curiosity": -1, "purpose": +1}


def satisfaction(need: str, value: float) -> float:
    v = max(0.0, min(100.0, value)) / 100.0
    return v if POLARITY[need] > 0 else 1.0 - v


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


def compute_mood(needs: dict[str, float], weights: dict[str, float]) -> tuple[float, float]:
    """Valence (bad day..good day) and arousal (flat..wired), each -1..1."""
    total_w = sum(weights.values()) or 1.0
    valence = sum(weights[n] / total_w * (2 * satisfaction(n, needs[n]) - 1) for n in NEEDS)
    arousal = (
        0.5 * (needs["energy"] - 50) / 50
        + 0.3 * (needs["frustration"] - 50) / 50
        + 0.2 * (needs["curiosity"] - 50) / 50
    )
    return round(_clamp(valence), 4), round(_clamp(arousal), 4)


def starting_needs() -> dict[str, float]:
    return {"energy": 80.0, "frustration": 10.0, "social": 55.0, "curiosity": 40.0,
            "purpose": 50.0}
