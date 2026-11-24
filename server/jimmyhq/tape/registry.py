"""The registry: every event kind is a typed model, checked when it is written.

Rules:

- A kind is declared once per version with ``@kind("name", v=N, example={...})``.
  The example is validated at import, so a kind cannot exist without one.
- Old payloads are upgraded when they are read, by registered upcasters. Stored
  bytes are never rewritten (only ``Tape.erase`` empties a payload, for the owner).
- Kinds are namespaced by owner. Core modules use their own prefix
  (``task.``, ``setting.``); an app's kinds must start with ``app.<app id>.``.
- A full app's kinds are *schema-backed*: they arrive as JSON Schema with an example
  (``register_schema``), because the core never runs app code. A registered
  ``(name, v)`` never changes. Such a kind is never upcast here: an event is read back
  at the version it was written in, and the app's own SDK upgrades it.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jimmyhq.ids import now_ms, ulid
from jimmyhq.tape import schema as schema_check

_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

_KINDS: dict[str, dict[int, Any]] = {}  # a pydantic model, or a SchemaModel
_OWNERS: dict[str, str] = {}
_EXAMPLES: dict[str, dict] = {}
_UPCASTERS: dict[tuple[str, int], Callable[[dict], dict]] = {}
_SCHEMA_BACKED: set[str] = set()
_SCHEMA_EXAMPLES: dict[tuple[str, int], dict] = {}


class UnknownKindError(ValueError):
    """The kind, or this version of it, is not registered in this process."""


class MissingUpcasterError(RuntimeError):
    """A known kind has no upgrade path from a stored version. This is a bug in
    the code that registered the kind, so it is never passed over quietly."""


class Payload(BaseModel):
    """Base class for event payloads. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


def check_name(name: str, owner: str) -> None:
    """A kind's name has the house shape, and only an app's own kinds start with
    ``app.<its id>.``."""
    if not _NAME.match(name):
        raise ValueError(f"event kind {name!r} must look like 'area.verb' in lower case")
    if owner != "core" and not name.startswith(f"app.{owner}."):
        raise ValueError(f"kind {name!r} from app {owner!r} must start with 'app.{owner}.'")
    if owner == "core" and name.startswith("app."):
        raise ValueError(f"kind {name!r}: the 'app.' prefix is reserved for apps")


def kind(
    name: str, v: int = 1, *, example: dict, owner: str = "core"
) -> Callable[[type[BaseModel]], type[BaseModel]]:
    """Register a payload model for an event kind and version."""

    def deco(model: type[BaseModel]) -> type[BaseModel]:
        check_name(name, owner)
        if name in _SCHEMA_BACKED:
            raise ValueError(f"kind {name!r} is already registered from a schema")
        if _OWNERS.setdefault(name, owner) != owner:
            raise ValueError(f"kind {name!r} is already owned by {_OWNERS[name]!r}")
        if v in _KINDS.get(name, {}):
            raise ValueError(f"kind {name!r} v{v} is already registered")
        model.model_validate(example)  # a kind without a valid example cannot exist
        _KINDS.setdefault(name, {})[v] = model
        if v >= max(_KINDS[name]):
            _EXAMPLES[name] = example
        return model

    return deco


class SchemaModel:
    """Stands where a pydantic model would for a kind that arrived as JSON Schema: the
    registry asks it the same two questions."""

    def __init__(self, name: str, v: int, schema: dict[str, Any]):
        self.name, self.v, self.schema = name, v, copy.deepcopy(schema)

    def model_validate(self, payload: Any) -> _Checked:
        try:
            schema_check.validate(self.schema, payload)
        except schema_check.PayloadError as exc:
            raise ValueError(f"{self.name} v{self.v}: {exc}") from None
        return _Checked(payload)

    def model_json_schema(self) -> dict[str, Any]:
        return copy.deepcopy(self.schema)


class _Checked:
    def __init__(self, payload: Any):
        self._payload = payload

    def model_dump(self, mode: str = "json") -> Any:
        return copy.deepcopy(self._payload)


def register_schema(name: str, v: int, *, schema: dict[str, Any], example: dict,
                    owner: str) -> bool:
    """Register one version of an app's kind from JSON Schema and its example.

    Returns True if it was new here and False if exactly this was registered already.
    Raises ``ValueError`` with a sentence when the name is not the app's, the schema
    cannot be used, the example does not fit, or ``(name, v)`` was registered before
    with anything different: a registered version never changes.
    """
    if owner == "core":
        raise ValueError("only an app's kinds are registered from a schema")
    check_name(name, owner)
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10_000:
        raise ValueError(f"kind {name!r}: the version must be a whole number from 1")
    if name in _KINDS and name not in _SCHEMA_BACKED:
        raise ValueError(f"kind {name!r} is already registered by the core")
    try:
        schema_check.check_schema(schema)
    except schema_check.SchemaError as exc:
        raise ValueError(f"kind {name!r} v{v}: {exc}") from None
    model = SchemaModel(name, v, schema)
    model.model_validate(example)
    have = _KINDS.get(name, {}).get(v)
    if have is not None:
        if isinstance(have, SchemaModel) and have.schema == schema \
                and _SCHEMA_EXAMPLES.get((name, v)) == example:
            return False
        raise ValueError(f"kind {name!r} v{v} is already registered with a different "
                         "schema or example; a registered version never changes")
    if _OWNERS.setdefault(name, owner) != owner:
        raise ValueError(f"kind {name!r} is already owned by {_OWNERS[name]!r}")
    _SCHEMA_BACKED.add(name)
    _KINDS.setdefault(name, {})[v] = model  # type: ignore[assignment]
    _SCHEMA_EXAMPLES[(name, v)] = copy.deepcopy(example)
    if v >= max(_KINDS[name]):
        _EXAMPLES[name] = copy.deepcopy(example)
    return True


def is_schema_backed(name: str) -> bool:
    return name in _SCHEMA_BACKED


def versions_of(name: str) -> list[int]:
    return sorted(_KINDS.get(name, {}))


def upcaster(name: str, from_v: int) -> Callable[[Callable[[dict], dict]], Callable[[dict], dict]]:
    """Register an upgrader that turns a payload at ``from_v`` into ``from_v + 1``."""

    def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        _UPCASTERS[(name, from_v)] = fn
        return fn

    return deco


def registered_kinds() -> list[str]:
    return sorted(_KINDS)


def owner_of(name: str) -> str:
    return _OWNERS[name]


def owner_of_or_none(name: str) -> str | None:
    return _OWNERS.get(name)


def example_of(name: str) -> dict:
    return _EXAMPLES[name]


def latest_version(name: str) -> int:
    if name not in _KINDS:
        raise UnknownKindError(f"unknown event kind: {name!r}")
    return max(_KINDS[name])


def validate_payload(name: str, v: int, payload: dict) -> dict:
    if name not in _KINDS:
        raise UnknownKindError(f"unknown event kind: {name!r}")
    model = _KINDS[name].get(v)
    if model is None:
        raise UnknownKindError(f"kind {name!r} has no schema for v{v}")
    return model.model_validate(payload).model_dump(mode="json")


def upcast(name: str, v: int, payload: dict) -> tuple[int, dict]:
    """Walk the upcaster chain from the stored version to the latest.

    A missing link raises. A silent partial upgrade would be quiet corruption.
    """
    latest = latest_version(name)
    if name in _SCHEMA_BACKED:
        return v, validate_payload(name, v, payload)  # the app's SDK upgrades its own
    while v < latest:
        fn = _UPCASTERS.get((name, v))
        if fn is None:
            raise MissingUpcasterError(f"no upcaster for {name!r} v{v} -> v{v + 1}")
        payload = fn(dict(payload))
        v += 1
    return v, validate_payload(name, v, payload)


def json_schemas() -> dict[str, Any]:
    """Latest JSON Schema per core kind: the input to TypeScript type generation. An
    app's schema-backed kinds are left out: they belong to the app, not to the web
    app's types."""
    return {n: _KINDS[n][latest_version(n)].model_json_schema() for n in registered_kinds()
            if n not in _SCHEMA_BACKED}


class Event(BaseModel):
    """One line on the Tape."""

    model_config = ConfigDict(extra="forbid")

    seq: int | None = None  # assigned by the Tape on append; the only ordering that matters
    id: str
    ts: int  # unix milliseconds, UTC
    kind: str
    v: int
    actor: str  # who wrote it: "owner", "system", "agent:<id>", "app:<id>", "token:<name>"
    cause: str | None = None  # id of the event that directly caused this one
    root: str | None = None  # correlation root shared by a whole chain of work
    subject: str | None = None  # the entity it is about, e.g. "task:01J..."
    payload: dict = Field(default_factory=dict)
    # False when this process could not check or upgrade the payload: the kind
    # is unknown here, the version is newer, or the stored payload no longer
    # validates. The payload is then exactly what was stored. Lenses skip these.
    readable: bool = True
    # True when the owner had this event's payload erased (``Tape.erase``). The
    # payload is then empty and ``readable`` is false, so every reader that
    # already steps over an unreadable event steps over this one.
    erased: bool = False


def make_event(
    kind_name: str,
    payload: dict,
    *,
    actor: str,
    cause: str | None = None,
    root: str | None = None,
    subject: str | None = None,
) -> Event:
    v = latest_version(kind_name)
    return Event(
        id=ulid(),
        ts=now_ms(),
        kind=kind_name,
        v=v,
        actor=actor,
        cause=cause,
        root=root,
        subject=subject,
        payload=validate_payload(kind_name, v, payload),
    )
