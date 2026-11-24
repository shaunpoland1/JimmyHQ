"""An app's own settings: on the Settings screen, and a word to the app when one changes.

**Declared.** Each installed app's ``settings`` are declared in the core's registry as
``app.<id>.<key>``, on a page of their own (``app.<id>``, under the Apps group), so the
owner changes them like any other. Every process keeps the pages in step with what is
installed (``apps.sync``): a page is declared when its app is installed, replaced whole
when an update changes it, and taken away when the app is gone or its files no longer
read.

**Added by JimmyHQ.** Every app's page also has ``app.<id>.auto_update``: whether a new
version of this app waits for the owner's click or is put in place by itself, or follows
``apps.auto_update``. It is the core's, never the app's: no delivery is sent when it changes.

**Delivered.** When the owner changes or resets one, the worker sees ``setting.updated``
or ``setting.reset`` on the Tape and queues a ``settings`` delivery, ``{key}`` (the short
key, as the app declared it). The value is not sent: the app reads it with
``settings.get``, which is always the value now. A change to the owner's time zone or
week start is delivered to every full app the same way, as ``{key: "owner.timezone"}``
or ``{key: "owner.week_start"}``; the new value is in ``/hello``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jimmyhq import settings as registry
from jimmyhq.apps import store
from jimmyhq.apps.manifest import core_setting

if TYPE_CHECKING:
    from jimmyhq.apps.manifest import Manifest
    from jimmyhq.core import Core
    from jimmyhq.tape.registry import Event

log = logging.getLogger("jimmyhq.apps.plugs.settings")

KINDS = ("setting.updated", "setting.reset")
# The owner's settings every full app hears of, under the names it is handed them by
# ``/hello``. An app's own keys have no dot, so these can never be mistaken for one.
OWNER_CALENDAR = {"owner.timezone": "owner.timezone", "owner.week_starts": "owner.week_start"}
AUTO_UPDATE = "auto_update"  # the per-app setting JimmyHQ adds to every app's page
AUTO_UPDATE_CHOICES = (("default", "As set for all apps, under Settings, Apps"),
                       ("wait", "Wait for my click"),
                       ("auto", "Update by itself when it asks for nothing new"))


def section_id(app_id: str) -> str:
    """The Settings page an app's own settings are on."""
    return f"app.{app_id}"


def page_line(m: Manifest) -> str:
    """The one line under the page's heading."""
    line = (m.summary or f"The settings of {m.name}.").strip()
    line = line if line.endswith((".", "!", "?")) else line + "."
    return line if len(line) <= 140 else line[:137].rstrip() + "..."


def auto_update_setting(m: Manifest) -> registry.Setting:
    """The setting JimmyHQ adds to every app's page: how a new version of it is put in
    place."""
    return registry.Setting(
        key=m.setting_key(AUTO_UPDATE), section=section_id(m.id), type="choice",
        default="default", choices=AUTO_UPDATE_CHOICES,
        label=f"When a new version of {m.name} is found",
        help="Waiting, nothing changes until you press Update on the Apps screen. By "
             "itself, a version that asks for no new permission is put in place for you. "
             "One that asks for more always waits for you.")


PAGE_ORDER = 900  # app pages come after the core's own under Apps


def wanted_page(m: Manifest) -> tuple[dict[str, Any], list[registry.Setting]]:
    """What one app's page should be, from its manifest: the page and every setting on
    it, in order, the one JimmyHQ adds last."""
    page = {"label": m.name, "group": "apps", "order": PAGE_ORDER,
            "description": page_line(m)}
    return page, [*(core_setting(m, setting) for setting in m.settings),
                  auto_update_setting(m)]


def page_now(section: str) -> tuple[dict[str, Any] | None, list[registry.Setting]]:
    """What this process has declared for a page now, in the shape of ``wanted_page``."""
    found = next((s for s in registry.ordered_sections() if s.id == section), None)
    page = None if found is None else {"label": found.label, "group": found.group,
                                       "order": found.order,
                                       "description": found.description}
    return page, [spec for spec in registry.declared() if spec.section == section]


def declare_app(m: Manifest) -> list[str]:
    """Put one app's page in step with its manifest. A page already in step is left
    alone; any other is taken away and declared again whole, so a setting the app no
    longer has goes and one it changed is replaced. All or nothing: when one setting
    cannot be declared the page is taken away and the error raised, so no app is ever
    half on the screen. Returns the keys declared."""
    page, specs = wanted_page(m)
    section = section_id(m.id)
    if page_now(section) == (page, specs):
        return []
    registry.withdraw_section(section)
    try:
        registry.section(section, page["label"], group=page["group"], order=page["order"],
                         description=page["description"])
        for spec in specs:
            registry.declare(spec)
    except Exception:
        registry.withdraw_section(section)
        raise
    return [spec.key for spec in specs]


def sync_pages(manifests: list[Manifest]) -> tuple[list[str], list[str]]:
    """Every page in step with what is installed: each app's declared or replaced
    (``declare_app``), and the page of any app not in ``manifests`` taken away. An app
    whose page cannot be declared is logged and left with no page; the others are
    unaffected. Returns (keys declared, pages taken away)."""
    made: list[str] = []
    ids = set()
    for m in manifests:
        try:
            made.extend(declare_app(m))
        except Exception as exc:  # noqa: BLE001 - one app's mistake must not stop the rest
            log.warning("The settings of the app %s could not be put on the Settings "
                        "screen, so none of them are there: %s", m.id, exc)
            continue
        ids.add(m.id)
    gone = [spec.id for spec in registry.ordered_sections()
            if is_app_page(spec.id) and spec.id.removeprefix("app.") not in ids]
    for section in gone:
        registry.withdraw_section(section)
    return made, gone


def declare_all(core: Core) -> list[str]:
    """Every installed app's settings, on the Settings screen. Pages of apps no longer
    installed, or whose files no longer read, are taken away."""
    return sync_pages(store.manifests(core))[0]


def is_app_page(section: str) -> bool:
    """A page an installed app's settings were put on (one dot: ``app.<id>``)."""
    return section.startswith("app.") and section.count(".") == 1


def settings_delivery(app_id: str, event: Event) -> dict[str, Any] | None:
    """What a ``settings`` delivery carries for this event, or None when it is not about
    one of this app's own settings or the owner's time zone or week start."""
    if event.kind not in KINDS:
        return None
    key = event.payload.get("key")
    if isinstance(key, str) and key in OWNER_CALENDAR:
        return {"key": OWNER_CALENDAR[key]}
    prefix = f"app.{app_id}."
    if not isinstance(key, str) or not key.startswith(prefix) or key == prefix:
        return None
    if key == prefix + AUTO_UPDATE:
        return None  # JimmyHQ's own, about the app, not the app's
    return {"key": key.removeprefix(prefix)}
