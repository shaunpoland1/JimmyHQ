"""A full app that keeps crashing: recorded, and the owner told.

The supervisor holds a program back after ``apps.crash_limit`` quick exits in a row and
says so in ``status.json`` (``runtime``). It has no Tape of its own, so the worker looks
(``notice``) and, once for each time a program is held back:

- prints ``apps.held_back`` on the Tape, so the record says what happened and when;
- with ``apps.crash_loop`` at "Hold it back and tell me" (the default), puts a card in
  the Bench's "done" lane that says what happened and that Restart on the Apps screen
  starts it again.

What happens then is the owner's decision, ``apps.crash_loop``: by default nothing is
switched off and nothing is rolled back. The owner may instead choose "switch it off"
(``apps.disabled``, reason "crashing") or "go back to the version before"
(``update.crash_rollback``, only when the crashing began with an update and that version
is kept; otherwise it is held back as by default). Each of those tells the owner too.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import kinds, runtime, store
from jimmyhq.apps.plugs.state import state
from jimmyhq.ids import now_ms

if TYPE_CHECKING:
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.plugs.crashes")

CARD_KIND = "apps.held_back"


def held_back(status: dict[str, Any]) -> bool:
    return status.get("state") == "crashing"


def occasion(status: dict[str, Any]) -> str:
    """Which holding back this is: a program held back, restarted and held back again
    is told about again."""
    last = status.get("last_exit") or {}
    return f"{status.get('generation', 0)}:{last.get('at', 0)}"


TELLING = ("hold_and_tell", "switch_off", "roll_back")


def tells_owner(core: Core) -> bool:
    return core.settings.get("apps.crash_loop") in TELLING


def card_words(name: str, exits: int, then: str = "held",
               version: str | None = None) -> dict[str, str]:
    stopped = (f"{name}'s program stopped {exits} times in a row, each time soon after it "
               "started, so JimmyHQ stopped starting it.")
    if then == "switched_off":
        return {
            "title": f"{name} kept crashing, so it was switched off",
            "summary": "You chose to have apps that keep crashing switched off.",
            "body": (f"{stopped} As you chose in Settings, Apps, it was switched off. It is "
                     "still installed and its data is untouched. Its log on the Apps screen "
                     "shows what it printed. Switch it on there when you are ready."),
        }
    if then == "rolled_back":
        return {
            "title": f"{name} kept crashing after its update, so it went back to {version}",
            "summary": "You chose to have a crashing update rolled back.",
            "body": (f"{stopped} The crashing began with its latest update, so, as you chose "
                     f"in Settings, Apps, it went back to version {version}. Its data was "
                     "left as it is; the copy taken before the update is kept, and Roll "
                     "back on its page can put it back. Its log shows what it printed."),
        }
    return {
        "title": f"{name} kept crashing, so it was stopped",
        "summary": "It stays stopped until you press Restart on the Apps screen.",
        "body": (f"{stopped} It is still installed and switched on, and its data is "
                 "untouched. Its log on the Apps screen shows what it printed. When you are "
                 "ready, press Restart there to start it again, or switch it off or "
                 "remove it."),
    }


def act(core: Core, app_id: str, status: dict[str, Any]) -> tuple[str, str | None]:
    """Carry out the owner's choice for a program held back. Returns what was done
    ("held", "switched_off" or "rolled_back") and the version gone back to."""
    choice = core.settings.get("apps.crash_loop")
    if choice == "switch_off":
        from jimmyhq.apps import install  # noqa: PLC0415 - it imports this package

        install.disable(core, app_id, actor="system", reason="crashing")
        return "switched_off", None
    if choice == "roll_back":
        from jimmyhq.apps import update  # noqa: PLC0415

        version = update.crash_rollback(core, app_id, status)
        if version is not None:
            return "rolled_back", version
    return "held", None


def notice(core: Core, *, now: int | None = None) -> list[str]:
    """Record each program held back since the last look. Returns their app ids."""
    from jimmyhq import bench  # noqa: PLC0415

    now = now_ms() if now is None else now
    found = (runtime.statuses(core).get("apps") or {})
    told = []
    for app_id, status in sorted(found.items()):
        if not isinstance(status, dict) or not held_back(status):
            continue
        row = store.get(core, app_id)
        if row is None or not state(core).tell_once(app_id, occasion(status), now):
            continue
        exits = int(status.get("failures") or 0)
        last_code = (status.get("last_exit") or {}).get("code")
        tell = tells_owner(core)
        try:
            then, version = act(core, app_id, status)
        except Exception:  # noqa: BLE001 - it stays held back, and the owner is told
            log.exception("apps: acting on %s's crash loop failed", app_id)
            then, version = "held", None
        core.tape.print("apps.held_back", {
            "app_id": app_id, "version": row["version"], "exits": exits,
            "last_code": last_code if isinstance(last_code, int) else None, "told": tell,
            "then": then,
        }, actor="system", subject=kinds.subject(app_id))
        if tell:
            bench.file_card(core, filed_by="system", kind=CARD_KIND, source="Apps",
                            lane="done", subject=f"app:{app_id}",
                            **card_words(row["name"], exits, then, version))
        told.append(app_id)
    return told

