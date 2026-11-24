"""Settings: declared once, stored as events, read through a lens.

The rule for the whole product is that anything which could differ between two
users is a setting with a sensible default. A setting is declared with its
type, default, range, label and help line; the settings screens are generated
from those declarations, so adding a setting never needs UI work.

Core modules declare theirs in ``settings_core``. An app declares its own
under ``app.<id>.``.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from jimmyhq.lens import Lens, LensRunner
from jimmyhq.tape.registry import Event
from jimmyhq.tape.store import Tape

TYPES = ("bool", "choice", "int", "float", "str", "list")
# What a text setting holds, when that is more exact than "any text". The
# settings screen offers a picker for it, and the value is checked here, so a
# settings file is held to the same rule as the screen.
FORMATS = ("timezone",)
_MAX_TEXT = 2000
_MAX_ITEMS = 200


class SettingError(ValueError):
    """The value offered for a setting is not acceptable. The message says why."""


def _time_zone(label: str, value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        ZoneInfo(value)
    except (KeyError, ValueError, OSError):
        raise SettingError(
            f"{label} is not a name this computer knows. Use an IANA name such as "
            "Europe/Paris, or leave it empty to use this computer's time zone."
        ) from None
    return value


def _storable(text: str) -> bool:
    """False for text that cannot be saved, such as half of a surrogate pair."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in text


@dataclass(frozen=True)
class Setting:
    key: str
    section: str
    type: str
    label: str
    help: str
    default: Any
    min: float | None = None
    max: float | None = None
    choices: tuple[tuple[str, str], ...] | None = None  # (value, label)
    # For a list: the only values it may hold, as (value, label). The settings
    # screen then offers them to pick from instead of asking for free text.
    items: tuple[tuple[str, str], ...] | None = None
    unique: bool = False  # for a list: no value twice
    # For text: one of FORMATS. "timezone" is an IANA time zone name, or empty.
    format: str | None = None
    # An extra check for what a type alone cannot say (a real time zone, an
    # address with no password in it). It returns the value to store, or
    # raises SettingError with a sentence that says what to do.
    validate: Callable[[Any], Any] | None = None
    # For a value stored before a rule was added: what to keep of it when it no longer
    # passes ``check``. It returns the value cleaned, which is checked again; without it,
    # such a value is read as the default. Never used for a new value: that is refused.
    tidy: Callable[[Any], Any] | None = None
    # False for what is personal or tied to this computer (a name, a time zone,
    # a server address, a folder). A shared profile leaves these out; a personal
    # backup of all settings keeps them.
    portable: bool = True
    # True for a tuning number most people never need (a margin, a timeout, a
    # cap on size). The settings screen keeps these behind "Show advanced
    # settings" until one is changed. Never for anything about safety,
    # permissions, privacy or money: those stay in plain sight.
    advanced: bool = False

    def check(self, value: Any) -> Any:
        """Return the value in its stored form, or raise SettingError."""
        value = self._check_type(value)
        if self.type == "list":
            if self.items is not None:
                allowed = [i[0] for i in self.items]
                unknown = [v for v in value if v not in allowed]
                if unknown:
                    raise SettingError(f"{self.label} can only contain: {', '.join(allowed)}.")
            if self.unique and len(set(value)) != len(value):
                raise SettingError(f"{self.label} cannot contain the same item twice.")
        if self.format == "timezone":
            value = _time_zone(self.label, value)
        if self.validate is not None:
            value = self.validate(value)
        return value

    def _check_type(self, value: Any) -> Any:
        if self.type == "bool":
            if not isinstance(value, bool):
                raise SettingError(f"{self.label} must be on or off.")
            return value
        if self.type in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise SettingError(f"{self.label} must be a number.")
            if isinstance(value, float) and not math.isfinite(value):
                raise SettingError(f"{self.label} must be an ordinary number.")
            if abs(value) > 2**53:
                raise SettingError(f"{self.label} is too large.")
            if self.type == "int":
                if isinstance(value, float) and not value.is_integer():
                    raise SettingError(f"{self.label} must be a whole number.")
                value = int(value)
            else:
                value = float(value)
            if self.min is not None and value < self.min:
                raise SettingError(f"{self.label} cannot be less than {self.min:g}.")
            if self.max is not None and value > self.max:
                raise SettingError(f"{self.label} cannot be more than {self.max:g}.")
            return value
        if self.type == "str":
            if not isinstance(value, str):
                raise SettingError(f"{self.label} must be text.")
            if len(value) > _MAX_TEXT:
                raise SettingError(f"{self.label} cannot be longer than {_MAX_TEXT} characters.")
            if not _storable(value):
                raise SettingError(f"{self.label} contains characters that cannot be saved.")
            return value
        if self.type == "choice":
            allowed = [c[0] for c in self.choices or ()]
            if value not in allowed:
                raise SettingError(f"{self.label} must be one of: {', '.join(allowed)}.")
            return value
        if self.type == "list":
            if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
                raise SettingError(f"{self.label} must be a list of text items.")
            if len(value) > _MAX_ITEMS or any(len(i) > _MAX_TEXT for i in value):
                raise SettingError(f"{self.label} is too long.")
            if not all(_storable(i) for i in value):
                raise SettingError(f"{self.label} contains characters that cannot be saved.")
            return list(value)
        raise SettingError(f"{self.key} has an unknown type {self.type!r}.")


def _read_document(document: Any) -> tuple[str, str, dict[str, Any]]:
    """The name, description and settings of a document, or SettingsFileError."""
    if not isinstance(document, dict) or FILE_MARKER not in document:
        raise SettingsFileError(
            f"This is not a JimmyHQ settings file: it has no \"{FILE_MARKER}\" line. A "
            f"settings file starts {{\"{FILE_MARKER}\": {FILE_VERSION}, \"settings\": {{...}}}}. "
            "Export one from the Settings screen to see an example.")
    version = document[FILE_MARKER]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SettingsFileError(
            f"\"{FILE_MARKER}\" must be a whole number such as {FILE_VERSION}.")
    if version > FILE_VERSION:
        raise SettingsFileError(
            f"This file uses settings file format {version}, which is newer than this version "
            f"of JimmyHQ reads (format {FILE_VERSION}). Update JimmyHQ, then try again.")
    texts = []
    for field_name, limit in (("name", _MAX_NAME), ("description", _MAX_DESCRIPTION)):
        text = document.get(field_name, "")
        if not isinstance(text, str):
            raise SettingsFileError(f"\"{field_name}\" must be text.")
        if len(text) > limit:
            raise SettingsFileError(f"\"{field_name}\" cannot be longer than {limit} characters.")
        texts.append(text)
    raw = document.get("settings")
    if not isinstance(raw, dict):
        raise SettingsFileError(
            "The file has no \"settings\" section. It must hold the settings as "
            "\"key\": value pairs.")
    return texts[0], texts[1], raw


def _unknown_reason(key: str, secret: bool) -> str:
    if secret:
        return ("This looks like a secret. Keys, tokens and passwords are never kept in a "
                "settings file; save it from the screen that uses it, and remove this line.")
    if key.startswith("app."):
        app_id = key.split(".")[1] if key.count(".") >= 2 else key
        return (f"This belongs to the app {app_id!r}, which is not installed here, so it was "
                "skipped. Install the app first, or leave this line out.")
    head = key.split(".")[0]
    if any(k.startswith(head + ".") for k in _DECLARED):
        return ("This version of JimmyHQ has no setting by this name. It may have been "
                "renamed or removed, or be misspelled. Check the name, or leave this line out.")
    return ("This is not a setting here. It probably belongs to a module or app that is not "
            "installed, so it was skipped. Leave this line out, or install that module first.")


# --- sections: where each setting sits on the Settings screen -------------------
#
# The Settings screen lists sections on the left under a few quiet group
# headings. The order is decided here, not by the order modules happen to be
# imported in: a section names its group and its place within it. A section
# with a parent is not a page of its own; it is a headed part of its parent's
# page.

# (id, label), in the order the screen shows them. "more" catches a section
# that names no group, so nothing a module or an app declares is ever lost.
GROUPS: tuple[tuple[str, str], ...] = (
    ("you", "You"),
    ("models", "Models"),
    ("work", "Your work"),
    ("agents", "Agents"),
    ("apps", "Apps"),
    ("more", "More"),
    ("system", "This installation"),
)
DEFAULT_GROUP = "more"
DEFAULT_ORDER = 500


@dataclass
class Section:
    id: str
    label: str
    group: str | None = None
    order: int = DEFAULT_ORDER
    parent: str | None = None
    description: str = ""  # one line, shown under the page heading
    # True for a page the web app draws itself. It is listed even when it
    # declares no settings.
    own_screen: bool = False


_SECTIONS: dict[str, Section] = {}
_DECLARED: dict[str, Setting] = {}


def section(
    section_id: str, label: str, *, group: str | None = None, order: int | None = None,
    parent: str | None = None, description: str | None = None, own_screen: bool | None = None,
) -> None:
    """Declare a section of the Settings screen.

    ``group`` is one of ``GROUPS`` and ``order`` the place within it (lower comes
    first; equal numbers keep the order they were declared in). ``parent`` makes
    this a headed part of another section's page. Declaring a section again
    changes only what is passed, so a module can add to a section without
    knowing where it sits.
    """
    if group is not None and group not in dict(GROUPS):
        raise ValueError(f"section {section_id!r}: unknown group {group!r}")
    if parent is not None:
        if group is not None:
            raise ValueError(f"section {section_id!r}: a section with a parent takes its group")
        if parent == section_id or parent not in _SECTIONS:
            raise ValueError(f"section {section_id!r}: declare its parent {parent!r} first")
        if _SECTIONS[parent].parent is not None:
            raise ValueError(f"section {section_id!r}: its parent cannot have a parent too")
        if any(s.parent == section_id for s in _SECTIONS.values()):
            raise ValueError(f"section {section_id!r} has parts of its own, so it cannot be one")
    known = _SECTIONS.get(section_id)
    if known is None:
        known = _SECTIONS[section_id] = Section(section_id, label)
    known.label = label
    if group is not None:
        known.group, known.parent = group, None
    if parent is not None:
        known.parent, known.group = parent, None
    if order is not None:
        known.order = order
    if description is not None:
        known.description = description
    if own_screen is not None:
        known.own_screen = own_screen


def group_of(spec: Section) -> str:
    """The group a section is listed under. An app's own section goes with the apps."""
    if spec.parent is not None:
        return group_of(_SECTIONS[spec.parent])
    if spec.group is not None:
        return spec.group
    return "apps" if spec.id.startswith("app.") else DEFAULT_GROUP


def ordered_sections() -> list[Section]:
    """Every section in the order the screen shows them: each page, then its parts."""
    # One copy, read once: another thread may be declaring or withdrawing an app's page.
    every = list(_SECTIONS.values())
    place = {spec.id: n for n, spec in enumerate(every)}

    def by_order(spec: Section) -> tuple[int, int]:
        return spec.order, place[spec.id]

    out: list[Section] = []
    for group_id, _ in GROUPS:
        pages = [s for s in every if s.parent is None and group_of(s) == group_id]
        for page in sorted(pages, key=by_order):
            out.append(page)
            out.extend(sorted((s for s in every if s.parent == page.id), key=by_order))
    return out


def declare(setting: Setting) -> Setting:
    if setting.type not in TYPES:
        raise ValueError(f"{setting.key}: unknown setting type {setting.type!r}")
    if setting.section not in _SECTIONS:
        raise ValueError(f"{setting.key}: unknown section {setting.section!r}")
    if setting.key in _DECLARED:
        raise ValueError(f"setting {setting.key!r} is already declared")
    if setting.type == "choice" and not setting.choices:
        raise ValueError(f"{setting.key}: a choice setting needs choices")
    if (setting.items is not None or setting.unique) and setting.type != "list":
        raise ValueError(f"{setting.key}: items and unique only apply to a list setting")
    if setting.format is not None and (setting.format not in FORMATS or setting.type != "str"):
        raise ValueError(f"{setting.key}: a format is one of {FORMATS} and only applies to text")
    setting.check(setting.default)  # a default that fails its own rules is a bug
    _DECLARED[setting.key] = setting
    return setting


def withdraw_section(section_id: str) -> list[str]:
    """Take a page and every setting on it off the Settings screen, as when the app that
    declared them is removed. What was stored stays on the record. Returns the keys."""
    if any(s.parent == section_id for s in _SECTIONS.values()):
        raise ValueError(f"section {section_id!r} has parts; withdraw those first")
    keys = [k for k, spec in _DECLARED.items() if spec.section == section_id]
    for key in keys:
        del _DECLARED[key]
    _SECTIONS.pop(section_id, None)
    return keys


def withdraw(key: str) -> bool:
    """Take one setting off the Settings screen, as when what declared it is gone or is
    declared again in another shape. What was stored stays on the record. Returns whether
    it was declared."""
    return _DECLARED.pop(key, None) is not None


def declared() -> list[Setting]:
    return list(_DECLARED.values())


def sections() -> list[tuple[str, str]]:
    """Each section's id and label, in the order the screen shows them."""
    return [(s.id, s.label) for s in ordered_sections()]


class _SettingsLens(Lens):
    name = "settings"
    version = 1
    kinds = ("setting.updated", "setting.reset")

    def initial(self) -> dict[str, Any]:
        return {"values": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        if event.kind == "setting.updated":
            state["values"][event.payload["key"]] = event.payload["value"]
        else:
            state["values"].pop(event.payload["key"], None)
        return state


# --- settings as files ----------------------------------------------------------

FILE_MARKER = "jimmyhq_settings"
FILE_VERSION = 1
MAX_FILE_BYTES = 256 * 1024
_MAX_NAME = 120
_MAX_DESCRIPTION = 2000
# Words that mark a name as a credential. No declared setting is one (those live
# in the secret store), so a file entry like this is refused and never echoed.
_SECRET_WORDS = re.compile(
    r"(secret|passw|api[_-]?key|apikey|credential|private[_-]?key|access[_-]?key|"
    r"auth[_-]?token|bearer|\btokens?\b|[_.-]token$)", re.IGNORECASE)


class SettingsFileError(SettingError):
    """The whole file cannot be used. The message says what to do."""


@dataclass(frozen=True)
class PlanEntry:
    """What one line of a settings file would do."""

    key: str
    status: str  # "change", "same" or "refused"
    label: str | None = None
    old: Any = None
    new: Any = None
    reset: bool = False  # the file said null: go back to the default
    was_stored: bool = False  # the old value was chosen, not just the default
    portable: bool | None = None
    reason: str | None = None  # for a refused entry: what is wrong and what to do

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "status": self.status, "label": self.label, "old": self.old,
            "new": self.new, "reset": self.reset, "portable": self.portable,
            "reason": self.reason,
        }


@dataclass
class Plan:
    """What a settings file would change, and what it cannot."""

    name: str = ""
    description: str = ""
    entries: list[PlanEntry] = field(default_factory=list)
    applied: bool = False
    skipped_invalid: bool = False  # refused entries were left out on purpose

    @property
    def changes(self) -> list[PlanEntry]:
        return [e for e in self.entries if e.status == "change"]

    @property
    def unchanged(self) -> list[PlanEntry]:
        return [e for e in self.entries if e.status == "same"]

    @property
    def refused(self) -> list[PlanEntry]:
        return [e for e in self.entries if e.status == "refused"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "applied": self.applied,
            "changes": [e.to_dict() for e in self.changes],
            "unchanged": [e.key for e in self.unchanged],
            "refused": [e.to_dict() for e in self.refused],
            "undo": self.undo_document(),
        }

    def undo_document(self) -> dict[str, Any]:
        """A settings file that puts every changed setting back as it was."""
        return {FILE_MARKER: FILE_VERSION, "settings": {
            e.key: (e.old if e.was_stored else None) for e in self.changes
        }}


class Settings:
    def __init__(self, tape: Tape, runner: LensRunner):
        self._tape = tape
        self._runner = runner
        self._lens = _SettingsLens()

    def _spec(self, key: str) -> Setting:
        try:
            return _DECLARED[key]
        except KeyError:
            raise SettingError(f"There is no setting called {key!r}.") from None

    def _stored(self) -> dict[str, Any]:
        return self._runner.read(self._lens)["values"]

    def get(self, key: str) -> Any:
        spec = self._spec(key)
        stored = self._stored()
        if key not in stored:
            return copy.deepcopy(spec.default)
        try:
            return spec.check(stored[key])
        except SettingError:
            pass
        if spec.tidy is not None:  # the rules changed since it was stored: keep the rest
            try:
                return spec.check(spec.tidy(stored[key]))
            except (SettingError, TypeError, ValueError, AttributeError):
                pass
        return copy.deepcopy(spec.default)  # the rules changed since it was stored

    def stored(self, key: str) -> Any:
        """The value as it was stored, before any check, or None if it was never set."""
        self._spec(key)
        return copy.deepcopy(self._stored().get(key))

    def set(self, key: str, value: Any, *, actor: str) -> Any:
        spec = self._spec(key)
        value = spec.check(value)
        if value != self.get(key):
            self._tape.print(
                "setting.updated", {"key": key, "value": value}, actor=actor,
                subject=f"setting:{key}",
            )
        return value

    def reset(self, key: str, *, actor: str) -> Any:
        spec = self._spec(key)
        if key in self._stored():
            self._tape.print("setting.reset", {"key": key}, actor=actor, subject=f"setting:{key}")
        return copy.deepcopy(spec.default)

    # --- settings as files ---------------------------------------------------

    def export(
        self, *, portable_only: bool = False, changed_only: bool = False,
        name: str = "", description: str = "",
    ) -> dict[str, Any]:
        """The settings as a document that ``plan`` and ``apply`` read back.

        ``portable_only`` leaves out what is personal or tied to this computer,
        so the result is safe to share as a profile. ``changed_only`` keeps only
        what differs from the defaults. Only declared settings are ever written:
        secrets live in the secret store and cannot appear here.
        """
        values: dict[str, Any] = {}
        for spec in declared():
            if portable_only and not spec.portable:
                continue
            if _SECRET_WORDS.search(spec.key):  # a safety net; none is declared
                continue
            value = self.get(spec.key)
            if changed_only and value == spec.default:
                continue
            values[spec.key] = value
        document: dict[str, Any] = {FILE_MARKER: FILE_VERSION}
        if name:
            document["name"] = name
        if description:
            document["description"] = description
        document["settings"] = values
        return document

    def plan(self, document: Any) -> Plan:
        """What applying this document would do. Changes nothing.

        Raises ``SettingsFileError`` when the file as a whole cannot be used (not
        a settings file, or from a newer format). Entries that cannot be applied
        come back as ``refused``, each with a sentence that says what to do.
        """
        name, description, raw = _read_document(document)
        stored = self._stored()
        entries: list[PlanEntry] = []
        for key, value in raw.items():
            entries.append(self._plan_entry(key, value, stored))
        return Plan(name=name, description=description, entries=entries)

    def _plan_entry(self, key: str, value: Any, stored: dict[str, Any]) -> PlanEntry:
        spec = _DECLARED.get(key)
        if spec is None:
            # A value is never echoed back for a name that looks like a credential.
            secret = bool(_SECRET_WORDS.search(key))
            return PlanEntry(key=key, status="refused", new=None if secret else value,
                             reason=_unknown_reason(key, secret))
        old = self.get(key)
        was_stored = key in stored
        if value is None:  # null means "go back to the default"
            new = copy.deepcopy(spec.default)
            return PlanEntry(
                key=key, label=spec.label, old=old, new=new, reset=True, was_stored=was_stored,
                portable=spec.portable, status="change" if was_stored else "same")
        try:
            new = spec.check(value)
        except SettingError as exc:
            return PlanEntry(key=key, status="refused", label=spec.label, old=old, new=value,
                             was_stored=was_stored, portable=spec.portable, reason=str(exc))
        return PlanEntry(
            key=key, label=spec.label, old=old, new=new, was_stored=was_stored,
            portable=spec.portable, status="change" if new != old else "same")

    def apply(self, document: Any, *, actor: str, skip_invalid: bool = False) -> Plan:
        """Apply a settings file as ordinary changes, each recorded on the Tape.

        Everything is checked before anything is written. If any entry is refused
        and ``skip_invalid`` is false, nothing is written and the returned plan has
        ``applied`` false. With ``skip_invalid`` the refused entries are left out
        and the rest is applied.
        """
        plan = self.plan(document)
        if plan.refused and not skip_invalid:
            return plan
        for entry in plan.changes:
            if entry.reset:
                self.reset(entry.key, actor=actor)
            else:
                self.set(entry.key, entry.new, actor=actor)
        plan.applied = True
        plan.skipped_invalid = bool(plan.refused)
        return plan

    def describe(self) -> dict[str, Any]:
        """The schema and current values, in the shape the settings screen reads."""
        out = []
        for spec in declared():
            item: dict[str, Any] = {
                "key": spec.key,
                "section": spec.section,
                "type": spec.type,
                "label": spec.label,
                "help": spec.help,
                "default": copy.deepcopy(spec.default),
                "value": self.get(spec.key),
            }
            if spec.min is not None:
                item["min"] = spec.min
            if spec.max is not None:
                item["max"] = spec.max
            if spec.choices:
                item["choices"] = [{"value": v, "label": lbl} for v, lbl in spec.choices]
            if spec.items is not None:
                item["items"] = [{"value": v, "label": lbl} for v, lbl in spec.items]
            if spec.unique:
                item["unique"] = True
            if spec.format is not None:
                item["format"] = spec.format
            if not spec.portable:
                item["portable"] = False
            if spec.advanced:
                item["advanced"] = True
            out.append(item)
        used = {s["section"] for s in out}
        # A page is listed when it has settings, when one of its parts has, or
        # when the web app draws it itself.
        shown = [s for s in ordered_sections() if s.id in used or s.own_screen]
        parents = {s.parent for s in shown if s.parent is not None}
        listed = [s for s in ordered_sections()
                  if s.id in used or s.own_screen or s.id in parents]
        groups = {group_of(s) for s in listed}
        return {
            "sections": [{"id": s.id, "label": s.label} for s in listed],
            # Where each section sits: its group, the page it is a part of (if
            # any) and the line shown under its heading. "sections" above is
            # already in the order to show.
            "layout": {
                "groups": [{"id": i, "label": lbl} for i, lbl in GROUPS if i in groups],
                "sections": {
                    s.id: {"group": group_of(s), "parent": s.parent,
                           "description": s.description}
                    for s in listed
                },
            },
            "settings": out,
        }
