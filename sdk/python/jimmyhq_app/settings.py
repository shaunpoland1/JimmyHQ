"""An app's settings read in one place, with the manifest's own defaults.

    SETTINGS = ManifestSettings(Path(__file__).parents[1] / "jimmyhq.app.json", "Posts")
    cfg = SETTINGS.load(app)                 # cfg.dup_days, ...: every setting by name
    SETTINGS.label("dup_warn")               # the words the owner sees
    pair = SETTINGS.pair(cfg, store, "dup_warn", "dup_strong", remember="pair.embeddings")

``load`` reads the owner's settings and falls back to the manifest's defaults for any
the platform did not answer, so code and manifest cannot drift. Two settings that must
stay the right way round (a "close" level below a "the same" level) are read with
``pair``: the owner's pair when it is right, otherwise the last pair that was, so neither
level can go dead because of one mistaken save, and a sentence that says so.
``store`` is anything with ``get_meta(key)`` and ``set_meta(key, value)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["Config", "ManifestSettings", "Pair"]


@dataclass(frozen=True)
class Config:
    """The settings as they are now, by name: ``cfg.dup_days``."""

    values: dict[str, Any]
    app_name: str = "This app"

    def __getattr__(self, key: str) -> Any:
        values = self.__dict__.get("values", {})
        if key in values:
            return values[key]
        raise AttributeError(f"{self.__dict__.get('app_name', 'This app')} has no setting "
                             f"called {key!r}.")


@dataclass(frozen=True)
class Pair:
    """Two levels to compare with, ``low`` below ``high``. ``refused`` is the sentence
    when the owner's own pair could not be used."""

    low: float
    high: float
    refused: str | None = None


class ManifestSettings:
    def __init__(self, manifest: Path | str, app_name: str):
        self.manifest = Path(manifest)
        self.app_name = app_name
        self._specs: dict[str, dict[str, Any]] | None = None

    def specs(self) -> dict[str, dict[str, Any]]:
        """Each setting the manifest declares, by key, read once."""
        if self._specs is None:
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
            self._specs = {s["key"]: s for s in data.get("settings", [])}
        return self._specs

    def defaults(self) -> dict[str, Any]:
        return {key: spec["default"] for key, spec in self.specs().items()}

    def label(self, key: str) -> str:
        return self.specs()[key]["label"]

    def load(self, app: Any) -> Config:
        """The owner's settings now, falling back to the manifest's defaults for any the
        platform did not answer (or when it could not be asked at all)."""
        values = self.defaults()
        try:
            given = app.settings.all() or {}
        except Exception:  # noqa: BLE001 - the defaults are always a safe reading
            given = {}
        for key in values:
            if key in given and given[key] is not None:
                values[key] = given[key]
        return Config(values, self.app_name)

    def check_pair(self, low_key: str, high_key: str, low: float, high: float) -> str | None:
        """Rule ``a_pair_stays_the_right_way_round``: None when ``low`` is below
        ``high``; otherwise the sentence that refuses it."""
        if low < high:
            return None
        return (f"You set \"{self.label(low_key)}\" to {low:.2f}, which is not below "
                f"\"{self.label(high_key)}\" at {high:.2f}, so {self.app_name} did not take "
                "the change. Set the first lower than the second.")

    def pair(self, cfg: Config, store: Any, low_key: str, high_key: str, *,
             remember: str) -> Pair:
        """The levels to compare with: the owner's when they are the right way round
        (then remembered under ``remember`` as the last good pair), otherwise the last
        good pair, or the defaults, with the sentence that says so."""
        low, high = float(cfg.values[low_key]), float(cfg.values[high_key])
        refused = self.check_pair(low_key, high_key, low, high)
        if refused is None:
            store.set_meta(remember, {"low": low, "high": high})
            return Pair(low, high)
        kept = store.get_meta(remember)
        if isinstance(kept, dict) and "low" in kept:
            last = (float(kept["low"]), float(kept["high"]))
        elif isinstance(kept, dict) and len(kept) == 2:  # kept before this module, in order
            last = tuple(float(v) for v in kept.values())  # type: ignore[assignment]
        else:
            base = self.defaults()
            last = (float(base[low_key]), float(base[high_key]))
        return Pair(last[0], last[1], f"{refused} {self.app_name} keeps comparing at "
                                      f"{last[0]:.2f} and {last[1]:.2f}.")
