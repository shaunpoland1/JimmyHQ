"""An app that needs more storage than the usual limit asks for it in its manifest.

    "storage_mb": 500

The install dialog shows it as one more sentence (``plan_line``). Agreeing to install is
agreeing to it: the app's own line in ``apps.quota_overrides`` (``<id>.storage_mb=500``,
"Limits for one app" under Settings, Apps) is written then (``grant``), and that is where
the owner raises or lowers it afterwards. An update that asks for more shows the same
sentence and is granted only when the owner presses Update: a version put in place by
itself keeps the storage it had. Nothing is ever lowered by an install or an update.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jimmyhq.apps import quotas

if TYPE_CHECKING:
    from jimmyhq.apps.manifest import Manifest
    from jimmyhq.core import Core


def sentence(asked_mb: int, usual_mb: int) -> str:
    """What the owner reads for an app that asks for more storage."""
    return (f"Keep up to {asked_mb} MB of its own data, such as pictures you give it, on "
            f"this computer. Apps may usually keep {usual_mb} MB. You can change this later "
            "under Settings, Apps.")


def needs_more(core: Core, m: Manifest) -> int | None:
    """The storage the app asks for, when that is more than it may keep now."""
    if m.storage_mb is None:
        return None
    return m.storage_mb if m.storage_mb > quotas.limit(core, m.id, "storage_mb") else None


def plan_line(core: Core, m: Manifest) -> dict[str, Any] | None:
    """The plan's ``storage`` entry: ``{asked_mb, usual_mb, sentence}``, or None."""
    asked = needs_more(core, m)
    if asked is None:
        return None
    usual = quotas.limit(core, m.id, "storage_mb")
    return {"asked_mb": asked, "usual_mb": usual, "sentence": sentence(asked, usual)}


def override_lines(lines: list[str], app_id: str, mb: int) -> list[str]:
    """The overrides with this app's storage line set to ``mb``, every other line kept."""
    kept = []
    for line in lines:
        try:
            who, name, _ = quotas.parse_override(line)
        except Exception:  # noqa: BLE001 - a line that no longer reads is kept as it is
            kept.append(line)
            continue
        if not (who == app_id and name == "storage_mb"):
            kept.append(line)
    return [*kept, f"{app_id}.storage_mb={mb}"]


def grant(core: Core, m: Manifest, *, actor: str = "owner") -> bool:
    """Give the app the storage it asked for, if that is more than it has. True when the
    owner's overrides changed."""
    asked = needs_more(core, m)
    if asked is None:
        return False
    lines = list(core.settings.get(quotas.OVERRIDES))
    core.settings.set(quotas.OVERRIDES, override_lines(lines, m.id, asked), actor=actor)
    return True
