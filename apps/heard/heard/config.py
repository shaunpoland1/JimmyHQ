"""Every setting Heard reads, in one place. ``load(app)`` reads the owner's settings; the
defaults are the manifest's own, so the two cannot drift."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmyhq_app.settings import Config, ManifestSettings

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
SETTINGS = ManifestSettings(MANIFEST, "Heard")

__all__ = ["MANIFEST", "Config", "defaults", "load"]


def defaults() -> dict[str, Any]:
    return SETTINGS.defaults()


def load(app: Any) -> Config:
    return SETTINGS.load(app)
