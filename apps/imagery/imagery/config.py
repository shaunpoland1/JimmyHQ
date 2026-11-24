"""Every setting Imagery reads, in one place. No other module holds a threshold.

``load(app)`` reads the owner's settings and returns a ``Config``; its defaults are the
manifest's own, so the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmyhq_app.settings import Config, ManifestSettings

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
SETTINGS = ManifestSettings(MANIFEST, "Imagery")

GEMINI_SECRET = "gemini_key"  # noqa: S105 - the name of the secret, not a value
COMPATIBLE_SECRET = "compatible_key"  # noqa: S105
OWN_NETWORK_SECRET = "own_network_key"  # noqa: S105

__all__ = ["COMPATIBLE_SECRET", "GEMINI_SECRET", "MANIFEST", "OWN_NETWORK_SECRET", "Config",
           "defaults", "label", "load"]


def defaults() -> dict[str, Any]:
    return SETTINGS.defaults()


def label(key: str) -> str:
    return SETTINGS.label(key)


def load(app: Any) -> Config:
    """The owner's settings now, with the manifest's defaults for any not answered."""
    return SETTINGS.load(app)
