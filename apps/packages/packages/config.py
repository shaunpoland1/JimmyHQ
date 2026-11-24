"""Every setting Packages reads, in one place. No other module holds a threshold.

``load(app)`` reads the owner's settings and returns a ``Config``; its defaults are the
manifest's own, so the two cannot drift (the SDK's ``ManifestSettings``, as Reader's).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmyhq_app.settings import Config, ManifestSettings

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
SETTINGS = ManifestSettings(MANIFEST, "Packages")

TELL_MODES = ("off", "delivered", "delivered_or_problem", "any_change")
SECRET = "seventeentrack_key"  # noqa: S105 - the name of the secret, not a value

__all__ = ["MANIFEST", "SECRET", "TELL_MODES", "Config", "defaults", "label", "load"]


def defaults() -> dict[str, Any]:
    return SETTINGS.defaults()


def label(key: str) -> str:
    return SETTINGS.label(key)


def load(app: Any) -> Config:
    """The owner's settings now, with the manifest's defaults for any not answered."""
    return SETTINGS.load(app)
