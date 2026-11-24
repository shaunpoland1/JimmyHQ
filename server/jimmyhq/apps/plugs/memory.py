"""Memory sources: what a full app collects, offered to Memory to learn from.

Each source the manifest declares (``memory_sources``) is registered with Memory as
``app_<id>_<source>``, labelled with the app's name, when the owner granted
``memory.source`` and the app is not marked private. Like every source it has its own
switch under Settings, Memory, and **it starts off whatever the manifest says**: the
owner switches each one on themselves, as the install dialog told them.

When Memory reads a source that is on, ``collect`` queues a ``collect`` delivery,
``{source, cursor, limit}``, and waits up to ``apps.collect_timeout_s`` for the app's
answer, ``{items: [{ref, when_ms, who, text, where}], cursor}``. What comes back is held
to these rules before Memory sees it:

- at most ``limit`` items; text, ``where`` and ``ref`` cut to their lengths;
- ``ref`` is kept inside the app's own names (``app.<id>:<ref>``), so it can never pose
  as a reference to the owner's record or another source's;
- ``who`` is ``owner`` when the app says the owner wrote it, and otherwise the app
  itself: an app cannot say an agent or the assistant said something.

An app that is switched off, no longer granted, not offering the source or not running is
read as having nothing new; one that does not answer raises, and Memory tries again at
its next pass.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import bridge, inbox, store
from jimmyhq.apps.permissions import FULL, allows

if TYPE_CHECKING:
    from jimmyhq.apps.bridge import Installed
    from jimmyhq.apps.manifest import Manifest
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.plugs.memory")

TIMEOUT_SETTING = "apps.collect_timeout_s"
MAX_TEXT = 8000
MAX_WHERE = 120
MAX_REF = 200
MAX_CURSOR = 500

APP_SOURCE = "app_"  # every app's source id starts so, and no core source's does


class SourceUnanswered(RuntimeError):
    """The app did not answer Memory in time."""


def may_offer_memory(app: Installed | None) -> bool:
    """A source is offered only by a full app the owner granted ``memory.source``, that
    is not marked private. The grant is read afresh each time."""
    return (app is not None and app.manifest.tier == FULL and not app.manifest.private
            and allows(app.granted, "memory.source"))


def starts_off(_declared_default: bool) -> bool:
    """Whatever the manifest says, an app's source starts switched off: the owner
    switches it on under Settings, Memory."""
    return False


def label_of(m: Manifest, label: str) -> str:
    return f"{m.name}: {label}"


def switch_key(source_id: str) -> str:
    """The on/off setting Memory declares for a source."""
    return f"memory.source.{source_id}"


def withdraw_source(source_id: str) -> None:
    """Take an app's source out of Memory, and its switch off the Settings screen. Whether
    the owner had switched it on stays on the record, for when it comes back."""
    from jimmyhq import settings as registry  # noqa: PLC0415
    from jimmyhq.memory import sources  # noqa: PLC0415

    sources.unregister_source(source_id)
    registry.withdraw(switch_key(source_id))


def wanted_sources(core: Core, manifests: list[Manifest]) -> dict[str, dict[str, Any]]:
    """For each app that may offer Memory sources now, what each should be:
    ``{app_id: {source_id: (label, description, name)}}``."""
    out: dict[str, dict[str, Any]] = {}
    for m in manifests:
        if not may_offer_memory(bridge.find_app(core, m.id)):
            continue
        out[m.id] = {m.source_id(d.id): (label_of(m, d.label), d.description, d.id)
                     for d in m.memory_sources}
    return out


def in_step(source_id: str, label: str, description: str) -> bool:
    """Whether the source registered here is the one wanted, switch and all."""
    from jimmyhq import settings as registry  # noqa: PLC0415
    from jimmyhq.memory import sources  # noqa: PLC0415

    have = sources.get_source(source_id)
    return (have is not None and have.label == label and have.description == description
            and not have.default_on
            and any(s.key == switch_key(source_id) for s in registry.declared()))


def register_app(app_id: str, wanted: dict[str, Any]) -> list[str]:
    """One app's sources in step: left alone when they are, otherwise taken out and
    registered again, all of them or none. Returns the ids registered."""
    from jimmyhq.memory import sources  # noqa: PLC0415

    if all(in_step(sid, label, text) for sid, (label, text, _) in wanted.items()):
        return []
    for source_id in wanted:
        withdraw_source(source_id)
    try:
        for source_id, (label, text, name) in wanted.items():
            sources.register_source(sources.Source(
                id=source_id, label=label, description=text, default_on=starts_off(True),
                collect=collector(app_id, name)))
    except Exception:
        for source_id in wanted:
            withdraw_source(source_id)
        raise
    return list(wanted)


def register_all(core: Core, manifests: list[Manifest] | None = None) -> list[str]:
    """Every granted app source registered with Memory as its manifest says now, and those
    of apps gone, no longer granted, or whose files no longer read taken away, with their
    switches. An app whose sources cannot be registered is logged and offers none; the
    others are unaffected. Returns the ids registered now."""
    from jimmyhq import settings as registry  # noqa: PLC0415
    from jimmyhq.memory import sources  # noqa: PLC0415

    wanted = wanted_sources(core, store.manifests(core) if manifests is None else manifests)
    every = {sid for by_app in wanted.values() for sid in by_app}
    for source in sources.all_sources():
        if source.id.startswith(APP_SOURCE) and source.id not in every:
            withdraw_source(source.id)
    for spec in registry.declared():  # a switch left without its source
        sid = spec.key.removeprefix("memory.source.")
        if spec.key.startswith(switch_key(APP_SOURCE)) and sid not in every:
            registry.withdraw(spec.key)
    made = []
    for app_id, by_app in sorted(wanted.items()):
        try:
            made.extend(register_app(app_id, by_app))
        except Exception as exc:  # noqa: BLE001 - one app's mistake must not stop the rest
            log.warning("The memory sources of the app %s could not be offered, so none of "
                        "them are: %s", app_id, exc)
    return made


def collector(app_id: str, name: str) -> Any:
    def collect(core: Core, cursor: str | None, limit: int) -> tuple[list[Any], str | None]:
        return collect_from(core, app_id, name, cursor, limit)
    return collect


def offering(core: Core, app_id: str, name: str) -> bool:
    """Whether the app may and does offer this source right now."""
    app = bridge.find_app(core, app_id)
    return (may_offer_memory(app) and app is not None and app.enabled
            and name in (inbox.plugs(core, app_id).get("sources") or [])
            and inbox.responding(core, app_id))


def collect_from(core: Core, app_id: str, name: str, cursor: str | None,
                 limit: int) -> tuple[list[Any], str | None]:
    if not offering(core, app_id, name):
        return [], cursor
    timeout_s = core.settings.get(TIMEOUT_SETTING)
    delivery = inbox.deliver(core, app_id, "collect",
                             {"source": name, "cursor": cursor, "limit": limit},
                             key=f"collect:{name}")
    found = inbox.wait_answer(core, delivery, timeout_s)
    if found is None:
        inbox.expire(core, delivery)
        raise SourceUnanswered(f"app {app_id} did not answer for source {name}")
    if not found.ok:
        raise SourceUnanswered(f"app {app_id} could not collect {name}")
    return checked_items(app_id, found.result, limit, cursor)


def own_ref(app_id: str, ref: Any) -> str | None:
    """An item's reference, inside the app's own names, without spaces."""
    if not isinstance(ref, str) or not ref.strip():
        return None
    clean = "".join(ch for ch in ref if ch.isprintable() and not ch.isspace())[:MAX_REF]
    return f"app.{app_id}:{clean}" if clean else None


def who_of(app_id: str, who: Any) -> str:
    """The owner, when the app says so; otherwise the app itself."""
    return "owner" if who == "owner" else f"app:{app_id}"


def checked_items(app_id: str, result: Any, limit: int,
                  cursor: str | None) -> tuple[list[Any], str | None]:
    """The app's answer, held to the rules above. Anything that does not fit is left
    out; an answer that is not the right shape is read as nothing new."""
    from jimmyhq.memory.sources import Item  # noqa: PLC0415

    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        return [], cursor
    next_cursor = result.get("cursor")
    if next_cursor is not None and (not isinstance(next_cursor, str)
                                    or len(next_cursor) > MAX_CURSOR):
        return [], cursor
    out = []
    for raw in result["items"][:max(0, limit)]:
        if not isinstance(raw, dict):
            continue
        ref = own_ref(app_id, raw.get("ref"))
        when = raw.get("when_ms")
        text = raw.get("text")
        if ref is None or isinstance(when, bool) or not isinstance(when, int) or when < 0 \
                or not isinstance(text, str):
            continue
        where = raw.get("where") if isinstance(raw.get("where"), str) else ""
        out.append(Item(ref=ref, when_ms=when, who=who_of(app_id, raw.get("who")),
                        text=text[:MAX_TEXT], where=where[:MAX_WHERE]))
    return out, next_cursor
