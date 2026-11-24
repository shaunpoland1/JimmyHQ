"""The provider protocol: what an integration must be able to do to become a hand.

A provider is the outside service (or a local program) that holds accounts and can act
on them. An adapter is the class that speaks to one type of provider. Everything above
this file (rules, limits, result shaping, the run ledger) is the same for all of them.

An adapter is made by the factory registered for its type::

    register_type(ProviderType("example", "Example", make, needs_key=True))

    def make(record: ProviderRecord, wiring: Wiring) -> HandProvider: ...

The factory is handed the saved record and a ``Wiring``: the provider's key, a way to
read its other secrets and the module's settings, and the HTTP client to use (tests pass
a fake). The adapters that ship with JimmyHQ are registered when ``jimmyhq.hands`` is
imported; the fake is not.

A type also says what the settings screen needs to offer it: a label, a plain
description, the fields its record takes, whether it needs a key and where the sign-in
tokens of its accounts are kept.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any, Literal, Protocol, get_args, runtime_checkable

Effect = Literal["read", "send", "create", "update", "delete", "spend", "access", "change"]
EFFECTS: tuple[str, ...] = get_args(Effect)
READ = "read"
# Where an action's effect came from. Only "owner" is a decision; the rest are suggestions.
EFFECT_FROM = ("provider", "adapter", "guess", "owner")

ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


@dataclass(frozen=True)
class Action:
    name: str  # the provider's own name, never shown to a model
    label: str
    description: str  # plain words, for the owner
    arguments: dict[str, Any]  # JSON Schema of an object
    effect: Effect = "change"  # "change" = it changes something, kind unknown
    effect_from: str = "guess"
    recipients: tuple[str, ...] = ()  # argument names that say who receives it
    amount: str | None = None  # argument name that carries money
    undo: bool = False
    version: str = ""  # schema fingerprint or provider version
    # True for mail that leaves an agent's own inbox: who receives it (you, someone
    # written to before, or someone new) picks the level, through the settings under
    # Integrations, Mail. See ``jimmyhq.hands.mail``.
    recipient_classes: bool = False

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["recipients"] = list(self.recipients)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        return cls(**{**data, "recipients": tuple(data.get("recipients") or ())})


@dataclass(frozen=True)
class Health:
    ok: bool
    error: str | None = None  # one sentence for the owner


@dataclass(frozen=True)
class Account:
    """An account as the provider knows it. ``id`` is the provider's own reference; it
    may be an address, so it is kept in the private store and never printed."""

    id: str
    label: str = ""
    app: str | None = None
    active: bool = True
    # The account's own address, when it has one the owner should see (an agent's
    # inbox). Kept in the private store beside ``id``.
    address: str | None = None


@dataclass(frozen=True)
class Connect:
    """The answer to "connect an account": it is ready, the owner must open a page, or
    the provider has no key yet."""

    status: Literal["ready", "redirect", "needs_key"]
    account: Account | None = None  # with "ready"
    url: str | None = None  # with "redirect": the provider's own consent page


@dataclass(frozen=True)
class Outcome:
    """What a run came to. ``error`` is the provider's words: shown to the caller only
    after scrubbing, and never printed."""

    ok: bool
    data: Any = None
    ref: str | None = None  # the provider's reference for what was done
    error: str | None = None
    # A sentence the ADAPTER wrote from fixed wording (never the provider's words or
    # anything a sender wrote). It is shown to the agent outside the frame that holds
    # the data, so outside text cannot forge it.
    note: str | None = None


@dataclass(frozen=True)
class Inbound:
    """Something that arrived for an account. ``sender`` is what the provider reports;
    JimmyHQ never checks it itself. ``sender_check`` is the receiving provider's own
    verdict on it: "passed", "failed" or "unknown" (see ``jimmyhq.hands.mail``)."""

    ref: str
    sender: str = ""
    subject: str = ""
    text: str = ""
    received_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    sender_check: str = "unknown"


@runtime_checkable
class HandProvider(Protocol):
    type: str

    async def check(self) -> Health: ...

    async def connect(self, app: str | None) -> Connect: ...

    async def accounts(self) -> list[Account]: ...

    async def actions(self, account: Account) -> list[Action]: ...

    async def run(self, account: Account, name: str, arguments: dict[str, Any], *,
                  key: str) -> Outcome:
        """Do the action once. ``key`` names this run; a provider that can refuse a
        repeat of the same key should be given it."""
        ...


def can_undo(provider: Any) -> bool:
    return callable(getattr(provider, "undo", None))


def can_receive(provider: Any) -> bool:
    return callable(getattr(provider, "inbound", None))


@dataclass(frozen=True)
class ProviderRecord:
    """A provider as the owner saved it. The key is not here: it is in the secret store."""

    id: str
    type: str
    label: str
    base_url: str | None = None
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def declared_default(name: str) -> Any:
    """The default of the setting ``hands.<name>``, for a ``Wiring`` made without a
    Core (a test, a script)."""
    from jimmyhq import settings  # noqa: PLC0415 - only needed for this fallback
    from jimmyhq.hands import settings as _declared  # noqa: F401, PLC0415 - declares them

    key = f"hands.{name}"
    found = next((s for s in settings.declared() if s.key == key), None)
    if found is None:
        raise KeyError(key)
    return found.default


@dataclass(frozen=True)
class Wiring:
    """What a factory is handed besides the record."""

    key: str | None
    secret: Callable[[str], str | None]  # another secret of this provider, by short name
    http: Any = None  # an injected HTTP client or transport; None means make your own
    # The value of the setting ``hands.<name>``. An adapter reads its limits here when
    # the provider's own options do not set them; it never invents a default.
    setting: Callable[[str], Any] = declared_default

    def limit(self, options: dict[str, Any], option: str, setting: str, *, low: float,
              high: float) -> float | None:
        """One limit of a provider: its own option when that is a number in range, else
        the owner's setting ``hands.<setting>``. None when the option is there and is
        not a number in range, so the adapter can say what to fix."""
        value = options.get(option)
        if value is None:
            return float(self.setting(setting))
        if isinstance(value, bool) or not isinstance(value, int | float) or not (
                low <= value <= high):
            return None
        return float(value)


Factory = Callable[[ProviderRecord, Wiring], HandProvider]

FIELD_KINDS = ("text", "number", "path", "argv", "names")
TOKENS_AT = ("provider", "here", "none")


@dataclass(frozen=True)
class RecordField:
    """One thing a provider's record takes, as the settings screen offers it.

    ``kind`` is ``text``, ``number``, ``path`` (a program or a folder, stored as given),
    ``argv`` (a list of a program's arguments, stored as given unless one looks like a
    credential) or ``names`` (a list of names whose VALUES are secrets of the provider,
    saved under ``secret_prefix`` + the name in lower case)."""

    name: str  # "base_url", or the option's name
    label: str
    kind: str = "text"
    help: str = ""
    required: bool = False
    where: str = "options"  # "record" for a field of the record itself
    secret_prefix: str | None = None
    # Only offered when the record has (or has not) an address: "address", "program".
    only_for: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderType:
    type: str
    label: str
    factory: Factory
    needs_key: bool = True
    needs_address: bool = False
    note: str = ""  # one sentence the screen shows when the owner adds one
    description: str = ""  # plain words: what this type of provider is
    fields: tuple[RecordField, ...] = ()
    # Where the sign-in tokens of an account live: with the "provider", "here" (this
    # machine's secret store) or "none" (the type has no sign-in).
    tokens: str = "none"
    tokens_note: str = ""
    # Whether what this adapter says an action does may pick "alone" as the level a
    # reading starts at. False for any type whose actions are described by whoever runs
    # the far end: their word is a suggestion and the owner decides.
    trusted_hints: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The type as the API shows it. Never the factory."""
        out = {f.name: getattr(self, f.name) for f in dataclass_fields(self)
               if f.name not in ("factory", "fields")}
        out["fields"] = [f.as_dict() for f in self.fields]
        return out

    def fields_of(self, kind: str) -> list[str]:
        return [f.name for f in self.fields if f.kind == kind and f.where == "options"]


TYPES: dict[str, ProviderType] = {}


def register_type(kind: ProviderType) -> ProviderType:
    """Make a type of provider available. An adapter module calls this when imported."""
    if not ID.match(kind.type):
        raise ValueError(f"provider type {kind.type!r} must be a short lower-case name")
    if kind.tokens not in TOKENS_AT or any(f.kind not in FIELD_KINDS for f in kind.fields):
        raise ValueError(f"provider type {kind.type!r} declares a field or a place for "
                         "tokens that the settings screen does not know")
    TYPES[kind.type] = kind
    return kind


def unregister_type(name: str) -> None:
    TYPES.pop(name, None)


def hint_trusted(type_name: str, effect_from: str) -> bool:
    """Rule ``hand_hint_trust``: whether an action's stated effect may be relied on to
    pick a level. The owner's own word always may. An adapter's or a provider's word may
    only when the type declares its hints trusted. A guess never may."""
    if effect_from == "owner":
        return True
    kind = TYPES.get(type_name)
    return bool(kind and kind.trusted_hints and effect_from in ("provider", "adapter"))
