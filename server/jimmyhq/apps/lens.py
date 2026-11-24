"""The ``apps`` lens: what is installed now, folded from the record of each app's life.

State::

    {"apps":    {app_id: row},       # installed now
     "removed": {app_id: {...}}}     # uninstalled, most recent removal of each

A row is what the last ``apps.installed`` or ``apps.updated`` said (name, version, tier,
source, repo, commit, tree), what is granted now (``permissions``, ``fingerprint``),
whether it is switched on, and when and by whom each consent was given (``consents``,
oldest first). ``versions`` lists each version it has run, with what was granted for it,
oldest first; ``previous`` is the version it was switched from, and ``switched`` how
(``"updated"`` or ``"rolled_back"``) and when. ``owner_hosts`` maps each host the owner
gave it himself (``owner_hosts.py``) to when, ``local_addresses`` each address on his own
network he let it reach (``local_reach.py``) to when, and ``local_resolved`` each of those to
the addresses its host led to when he added or last confirmed it. Removing the app drops
them with the row; an update or a rollback that changes its tier drops the addresses on his
network (an allowance is for the kind of app he gave it to).
Times are the events' own, in unix milliseconds.

Screens read this through ``store.installed(core)``; nothing reads the Tape per request.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.apps.kinds import LIFECYCLE
from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

KEEP_CONSENTS = 50
KEEP_VERSIONS = 20

_CODE = ("name", "version", "tier", "source", "repo", "commit", "tree", "reviewed", "ref",
         "example")


def _consent(event: Event, *, version: str, tier: str, permissions: list[str],
             added: list[str], removed: list[str], fingerprint: str) -> dict[str, Any]:
    return {"at": event.ts, "by": event.actor, "version": version, "tier": tier,
            "permissions": list(permissions), "added": list(added), "removed": list(removed),
            "fingerprint": fingerprint}


def _version(row: dict[str, Any], at: int) -> dict[str, Any]:
    return {**{k: row.get(k) for k in _CODE}, "permissions": list(row["permissions"]),
            "fingerprint": row["fingerprint"], "at": at}


def _switched(row: dict[str, Any], how: str, at: int) -> None:
    row["previous"] = {"version": row["version"], "tree": row["tree"]}
    row["switched"] = {"how": how, "at": at}


def _remember(row: dict[str, Any], entry: dict[str, Any]) -> None:
    row["versions"] = [*row.get("versions", []), entry][-KEEP_VERSIONS:]


def _drop_local(row: dict[str, Any], gone: set[str]) -> None:
    for key in ("local_addresses", "local_resolved"):
        if key in row:
            row[key] = {a: v for a, v in row[key].items() if a not in gone}


class AppsLens(Lens):
    name = "apps"
    version = 5
    kinds = LIFECYCLE

    def initial(self) -> dict[str, Any]:
        return {"apps": {}, "removed": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        p = event.payload
        app_id = p["app_id"]
        apps = state["apps"]
        row = apps.get(app_id)

        if event.kind == "apps.installed":
            row = {
                "app_id": app_id, **{k: p.get(k) for k in _CODE},
                "permissions": list(p["permissions"]), "fingerprint": p["fingerprint"],
                "enabled": True, "disabled_reason": None,
                "installed_at": event.ts, "installed_by": event.actor, "updated_at": None,
                "consented_at": event.ts,
                "consents": [_consent(
                    event, version=p["version"], tier=p["tier"],
                    permissions=p["permissions"], added=p["permissions"], removed=[],
                    fingerprint=p["fingerprint"])],
                "versions": [],
            }
            _remember(row, _version(row, event.ts))
            apps[app_id] = row
            state["removed"].pop(app_id, None)
            return state

        if row is None:
            return state  # about an app that is not installed: nothing to change

        tier_before = row.get("tier")
        if event.kind == "apps.updated":
            _switched(row, "updated", event.ts)
            row.update({k: p.get(k) for k in _CODE})
            row["previous_version"] = p["from_version"]
            row["updated_at"] = event.ts
            if p["fingerprint"] != row["fingerprint"]:
                # The update itself carries what is granted from now on.
                row["permissions"] = list(p["permissions"])
                row["fingerprint"] = p["fingerprint"]
            _remember(row, _version(row, event.ts))
        elif event.kind == "apps.permissions_granted":
            row["permissions"] = list(p["permissions"])
            row["fingerprint"] = p["fingerprint"]
            row["consented_at"] = event.ts
            row["consents"] = [*row["consents"], _consent(
                event, version=p["version"], tier=p["tier"], permissions=p["permissions"],
                added=p.get("added", []), removed=p.get("removed", []),
                fingerprint=p["fingerprint"])][-KEEP_CONSENTS:]
        elif event.kind == "apps.enabled":
            row["enabled"] = True
            row["disabled_reason"] = None
        elif event.kind == "apps.disabled":
            row["enabled"] = False
            row["disabled_reason"] = p.get("reason", "owner")
        elif event.kind == "apps.rolled_back":
            kept = [v for v in row.get("versions", []) if v["version"] == p["version"]
                    and (p.get("tree") is None or v.get("tree") == p["tree"])]
            _switched(row, "rolled_back", event.ts)
            if kept:
                row.update({k: kept[-1].get(k) for k in _CODE})
                if kept[-1].get("fingerprint"):
                    # Back to what the owner agreed to for that version, no more.
                    row["permissions"] = list(kept[-1]["permissions"])
                    row["fingerprint"] = kept[-1]["fingerprint"]
            else:
                row["version"] = p["version"]
            row["previous_version"] = p["from_version"]
            row["updated_at"] = event.ts
            _remember(row, _version(row, event.ts))
        elif event.kind == "apps.host_added":
            row["owner_hosts"] = {**row.get("owner_hosts", {}), p["host"]: event.ts}
        elif event.kind == "apps.host_removed":
            row["owner_hosts"] = {h: at for h, at in row.get("owner_hosts", {}).items()
                                  if h != p["host"]}
        elif event.kind == "apps.local_address_added":
            row["local_addresses"] = {**row.get("local_addresses", {}), p["address"]: event.ts}
            row["local_resolved"] = {**row.get("local_resolved", {}),
                                     p["address"]: list(p.get("resolved") or [])}
        elif event.kind == "apps.local_address_confirmed":
            if p["address"] in row.get("local_addresses", {}):
                row["local_resolved"] = {**row.get("local_resolved", {}),
                                         p["address"]: list(p["resolved"])}
        elif event.kind == "apps.local_address_removed":
            _drop_local(row, {p["address"]})
        if event.kind in ("apps.updated", "apps.rolled_back") \
                and row.get("tier") != tier_before:
            _drop_local(row, set(row.get("local_addresses", {})))
        if event.kind == "apps.removed":
            del apps[app_id]
            state["removed"][app_id] = {
                "app_id": app_id, "name": row["name"], "version": p["version"],
                "tier": row["tier"], "removed_at": event.ts, "removed_by": event.actor,
                "data": p.get("data", "kept"), "tape_erased": bool(p.get("tape_erased")),
            }
        return state
