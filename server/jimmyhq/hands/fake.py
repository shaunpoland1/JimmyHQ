"""A provider that talks to nobody. FOR TESTS AND DEMOS ONLY.

It implements the whole protocol, ``undo`` and ``inbound`` included, against a small
world held in memory, so the rules can be tested end to end and the packages built on
this one can be developed before a real adapter exists. It is never registered unless
something calls ``register()``: a normal install has no "fake" type.

A factory makes a new adapter for every use, so what the fake remembers lives in a
``World`` kept by provider id::

    fake.register()
    world = fake.world("demo")          # the same world the adapter will use
    world.fail["notes.list"] = "the service is busy"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jimmyhq.hands.provider import (
    Account,
    Action,
    Connect,
    Health,
    Inbound,
    Outcome,
    ProviderRecord,
    ProviderType,
    Wiring,
    register_type,
    unregister_type,
)

TYPE = "fake"
CONSENT_URL = "https://consent.example.com/approve"


def _schema(required: list[str], **props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


_TEXT = {"type": "string"}


def default_actions() -> list[Action]:
    return [
        Action("notes.list", "List notes", "Lists the notes in the account.",
               _schema([], query={"type": "string", "description": "Words to look for."},
                       limit={"type": "integer", "description": "How many, at most."}),
               effect="read", effect_from="adapter", version="1"),
        Action("notes.get", "Read a note", "Reads one note.",
               _schema(["id"], id={"type": "string", "description": "The note's id."}),
               effect="read", effect_from="adapter", version="1"),
        Action("messages.send", "Send a message", "Sends a message to someone.",
               _schema(["to", "text"], to={"type": "string", "description": "Who to send to."},
                       subject=dict(_TEXT, description="The subject line."),
                       text=dict(_TEXT, description="The message."),
                       signature=dict(_TEXT, description="Added under the message."),
                       priority={"type": "string", "enum": ["low", "normal", "high"]}),
               effect="send", effect_from="adapter", recipients=("to",), undo=True,
               version="1"),
        Action("payments.make", "Make a payment", "Pays an amount to someone.",
               _schema(["to", "amount"], to=_TEXT, amount={"type": "number"}),
               effect="spend", effect_from="adapter", recipients=("to",), amount="amount",
               version="1"),
        Action("records.import", "Import a record", "Creates a record from a whole object.",
               _schema(["record"], record={"type": "object"}),
               effect="create", effect_from="adapter", version="1"),
        Action("things.poke", "Poke a thing", "Nobody is sure what this does.",
               _schema([], thing=_TEXT), effect="change", effect_from="guess", version="1"),
        Action("status.peek", "Peek at the status", "The provider says this only reads.",
               _schema([]), effect="read", effect_from="guess", version="1"),
    ]


@dataclass
class World:
    """What one fake provider remembers, and how a test tells it to behave."""

    actions: list[Action] = field(default_factory=default_actions)
    accounts: list[Account] = field(default_factory=list)
    connect: str = "ready"  # ready | redirect | needs_key
    healthy: bool = True
    data: dict[str, Any] = field(default_factory=dict)  # action name -> what it answers
    fail: dict[str, str] = field(default_factory=dict)  # action name -> the error it gives
    raises: dict[str, BaseException] = field(default_factory=dict)  # action -> what it raises
    calls: list[dict[str, Any]] = field(default_factory=list)  # every run that arrived
    undone: list[str] = field(default_factory=list)
    inbox: list[Inbound] = field(default_factory=list)
    keys_seen: list[str | None] = field(default_factory=list)
    made: int = 0

    def set_version(self, name: str, version: str) -> None:
        self.actions = [Action.from_dict(a.as_dict() | {"version": version})
                        if a.name == name else a for a in self.actions]

    def new_account(self, app: str | None = None) -> Account:
        self.made += 1
        account = Account(id=f"acct-{self.made}", label=f"Account {self.made}", app=app)
        self.accounts.append(account)
        return account


WORLDS: dict[str, World] = {}


def world(provider_id: str) -> World:
    return WORLDS.setdefault(provider_id, World())


class FakeProvider:
    """FOR TESTS AND DEMOS ONLY. Does nothing outside this process."""

    type = TYPE

    def __init__(self, record: ProviderRecord, wiring: Wiring):
        self.record = record
        self.key = wiring.key
        self.world = world(record.id)
        self.world.keys_seen.append(wiring.key)

    async def check(self) -> Health:
        return Health(True) if self.world.healthy else Health(False, "The fake is unwell.")

    async def connect(self, app: str | None) -> Connect:
        if self.world.connect == "needs_key":
            return Connect("needs_key")
        if self.world.connect == "redirect":
            return Connect("redirect", url=CONSENT_URL)
        return Connect("ready", account=self.world.new_account(app))

    async def accounts(self) -> list[Account]:
        return list(self.world.accounts)

    async def actions(self, account: Account) -> list[Action]:
        return list(self.world.actions)

    async def run(self, account: Account, name: str, arguments: dict[str, Any], *,
                  key: str) -> Outcome:
        self.world.calls.append({"account": account.id, "name": name,
                                 "arguments": dict(arguments), "key": key})
        if name in self.world.raises:
            raise self.world.raises[name]
        if name in self.world.fail:
            return Outcome(False, error=self.world.fail[name])
        data = self.world.data.get(name, {"done": name})
        if callable(data):
            data = data(arguments)
        return Outcome(True, data=data, ref=f"ref-{len(self.world.calls)}")

    async def undo(self, account: Account, ref: str) -> Outcome:
        if "undo" in self.world.raises:
            raise self.world.raises["undo"]
        self.world.undone.append(ref)
        return Outcome(True, data={"undone": ref}, ref=ref)

    async def inbound(self, account: Account, cursor: str | None
                      ) -> tuple[list[Inbound], str]:
        seen = int(cursor or 0)
        return list(self.world.inbox[seen:]), str(len(self.world.inbox))


def register() -> ProviderType:
    """Make the "fake" type available. Tests and demos call this; nothing else does."""
    return register_type(ProviderType(
        TYPE, "Fake (tests and demos only)", FakeProvider, needs_key=True,
        note="Does nothing outside this machine.",
        description="A pretend provider held in memory, for tests and demos.",
        # Its actions are written in this file, so what they say they do is so.
        trusted_hints=True))


def unregister() -> None:
    unregister_type(TYPE)
    WORLDS.clear()
