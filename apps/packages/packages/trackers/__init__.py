"""The tracker interface: what ``sync`` and ``ops`` call, and the trackers.

A tracker is the part that knows one tracking service. Nothing outside its own file knows
that service's addresses, headers, field names or statuses: it is given plain ``Item``s
and answers plain ``Reply``s whose statuses are Packages' own (``packages.status``). To
add a service, write a file with these and list it in ``made``; the setting
``tracker`` lists the choices.

    t.id, t.label            the setting's value, and the name the owner reads
    t.outside                True when it asks an outside service (so Refresh now keeps its gap)
    t.registers              True when a number is registered with the service, once, and
                             that may use up quota; False when nothing is registered
    t.batch_most             the most items one request may hold
    t.hand_statuses          the statuses the owner may set by hand
    t.carrier_help           one sentence for the carrier box on the Add form
    t.key_problem()          None, or the sentence that says what is missing to start
    t.check_carrier(code)    None, or the sentence that says why a carrier code cannot be used
    t.register(items)        make each number known to the service: ``charged`` says a place of
                             quota was used
    t.read(items)            what each item is now: a ``Reading``, or None for no news yet
    t.change_carrier(item, code)  ``item.carrier`` is the carrier it has now, when known
    t.forget(item)           stop the service tracking it
    t.quota()                a ``Reply`` whose ``quota`` is the owner's remaining quota, or None

Each request is answered once and sorted into a definite yes, a definite no (with the
service's own words) or no clear answer (a timeout, a server error, a body that cannot be
read). Nothing here ever tries again: ``sync`` decides when to try again, and what a
no clear answer changes is nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

YES, NO, NONE = "yes", "no", "none"


@dataclass(frozen=True)
class Item:
    """One package as a tracker is told of it."""

    id: int
    number: str
    carrier: str = ""  # the carrier sent with it; "" means the service detects it
    prefer: str = ""  # of several answers for the number, the carrier to read first


@dataclass(frozen=True)
class Reading:
    """What a tracker found out about one package. ``status`` is one of
    ``packages.status.STATUSES``."""

    status: str
    event: str = ""
    event_ms: int | None = None


@dataclass(frozen=True)
class Result:
    """A tracker's answer about one item. ``kind`` is ``yes``, ``no`` (a definite
    refusal; ``said`` has the service's words) or ``none`` (no clear answer about this
    one). ``gone`` says the service has never heard of the number."""

    id: int
    kind: str
    said: str = ""
    reading: Reading | None = None
    carrier: str = ""  # the carrier the service reports, when it says
    guessed: bool = False  # the service guessed it
    charged: bool = False  # registering used a place of quota
    gone: bool = False


@dataclass(frozen=True)
class Quota:
    total: int
    used: int
    left: int


@dataclass(frozen=True)
class Reply:
    """A request's answer. ``kind`` is ``yes``, ``no`` or ``none`` for the whole request;
    ``items`` holds the answer about each item when it was a yes."""

    kind: str
    said: str = ""
    items: tuple[Result, ...] = field(default_factory=tuple)
    quota: Quota | None = None


class Tracker(Protocol):
    id: str
    label: str
    outside: bool
    registers: bool
    batch_most: int
    hand_statuses: tuple[str, ...]
    carrier_help: str

    def key_problem(self) -> str | None: ...
    def check_carrier(self, code: str) -> str | None: ...
    def register(self, items: list[Item]) -> Reply: ...
    def read(self, items: list[Item]) -> Reply: ...
    def change_carrier(self, item: Item, code: str) -> Reply: ...
    def forget(self, item: Item) -> Reply: ...
    def quota(self) -> Reply: ...


def made(app: Any, cfg: Any = None) -> dict[str, Tracker]:
    """Every tracker, by the id the setting uses. ``cfg`` is a callable that returns the
    owner's settings now (a tracker reads its own timeout from it)."""
    from packages.trackers.practice import Practice  # noqa: PLC0415
    from packages.trackers.seventeentrack import SeventeenTrack  # noqa: PLC0415

    return {t.id: t for t in (Practice(), SeventeenTrack(app, cfg))}
