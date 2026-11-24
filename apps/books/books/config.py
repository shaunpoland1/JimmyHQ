"""Every setting Books reads, in one place. No other module holds a threshold.

``load(app)`` reads the owner's settings and returns a ``Config``; its defaults are the
manifest's own, so the two cannot drift (the SDK's ``ManifestSettings``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmyhq_app.settings import Config, ManifestSettings

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
SETTINGS = ManifestSettings(MANIFEST, "Books")

__all__ = ["MANIFEST", "Config", "defaults", "load"]


def defaults() -> dict[str, Any]:
    return SETTINGS.defaults()


def load(app: Any) -> Config:
    """The owner's settings now, with the manifest's defaults for any not answered."""
    return SETTINGS.load(app)
