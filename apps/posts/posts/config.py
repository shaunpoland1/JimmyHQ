"""Every setting Posts reads, in one place. No other module holds a threshold.

``load(app)`` reads the owner's settings once for a piece of work and returns a
``Config``; its defaults are the manifest's own, so the two cannot drift. The duplicate
thresholds are a pair that must stay the right way round: ``check_thresholds`` says why a
pair is refused, and ``thresholds`` hands out the last pair that was right, so neither
tier can go dead because of one mistaken save. The pattern is the SDK's
(``jimmyhq_app.settings``), the same as Reader's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmyhq_app.settings import Config, ManifestSettings, Pair

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
SETTINGS = ManifestSettings(MANIFEST, "Posts")

# The pairs that must keep warn below strong: kind -> (warn key, strong key).
PAIRS = {"embeddings": ("dup_warn", "dup_strong"), "words": ("words_warn", "words_strong")}

__all__ = ["MANIFEST", "PAIRS", "Config", "Pair", "check_thresholds", "defaults", "label",
           "load", "thresholds"]


def defaults() -> dict[str, Any]:
    return SETTINGS.defaults()


def label(key: str) -> str:
    return SETTINGS.label(key)


def load(app: Any) -> Config:
    """The owner's settings now, with the manifest's defaults for any not answered."""
    return SETTINGS.load(app)


def check_thresholds(kind: str, warn: float, strong: float) -> str | None:
    """None when the pair is usable; otherwise the sentence that refuses it."""
    return SETTINGS.check_pair(*PAIRS[kind], warn, strong)


def thresholds(cfg: Config, store: Any, kind: str) -> Pair:
    """The pair to compare with: ``low`` warns, ``high`` is a near-duplicate."""
    return SETTINGS.pair(cfg, store, *PAIRS[kind], remember=f"thresholds.{kind}")
