"""Every setting Reader reads, in one place. No other module holds a threshold.

``load(app)`` reads the owner's settings and returns a ``Config``; its defaults are the
manifest's own, so the two cannot drift. The similarity levels are pairs that must stay
the right way round ("close to" below "the same story"): ``check_pair`` says why a pair
is refused, and ``pair`` hands out the last pair that was right, so neither level can go
dead because of one mistaken save. The pattern is the SDK's (``jimmyhq_app.settings``),
the same as Posts'.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmyhq_app.settings import Config, ManifestSettings, Pair

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
SETTINGS = ManifestSettings(MANIFEST, "Reader")

# The pairs that must keep close below same: kind -> (close key, same key).
PAIRS = {"embeddings": ("similar_close", "similar_same"), "words": ("words_close", "words_same")}

__all__ = ["MANIFEST", "PAIRS", "Config", "Pair", "check_pair", "defaults", "label", "load",
           "pair"]


def defaults() -> dict[str, Any]:
    return SETTINGS.defaults()


def label(key: str) -> str:
    return SETTINGS.label(key)


def load(app: Any) -> Config:
    """The owner's settings now, with the manifest's defaults for any not answered."""
    return SETTINGS.load(app)


def check_pair(kind: str, close: float, same: float) -> str | None:
    """None when the pair is usable; otherwise the sentence that refuses it."""
    return SETTINGS.check_pair(*PAIRS[kind], close, same)


def pair(cfg: Config, store: Any, kind: str) -> Pair:
    """The levels to compare with: ``low`` is "close to", ``high`` "the same story"."""
    return SETTINGS.pair(cfg, store, *PAIRS[kind], remember=f"pair.{kind}")
