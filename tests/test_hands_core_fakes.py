"""Shared set-up for the hands core tests: a fake provider, one account, one agent."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

from jimmyhq import hands
from jimmyhq.hands import fake

PROVIDER, ACCOUNT, AGENT = "demo", "notes", "ada"
KEY = "sk-test-SECRET-0123456789"
ADDRESS = "kit@example.com"
PHONE = "+44 20 7946 0958"
LEAKED = "sk-live-LEAKED-abcdef0123456789"


class Crash(BaseException):
    """The process dying mid-call: nothing catches it, as nothing catches a power cut."""


class Clock:
    def __init__(self, ms: int = 1_800_000_000_000):
        self.ms = ms

    def __call__(self) -> int:
        return self.ms

    def forward(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)


def run(coro):
    return asyncio.run(coro)


@contextmanager
def demo(core):
    """A saved fake provider with a key, and one connected account called ``notes``."""
    fake.register()
    try:
        h = hands.hands(core)
        h.now = Clock()
        h.save_provider({"id": PROVIDER, "type": "fake", "label": "Demo"}, actor="owner")
        h.set_secret(PROVIDER, KEY)
        answer = run(h.connect(provider_id=PROVIDER, slug=ACCOUNT, label="Notes",
                               actor="owner"))
        assert answer["status"] == "ready"
        yield h, fake.world(PROVIDER)
    finally:
        fake.unregister()
        hands.set_level_gate(None)


def switch_on(h, action, **how):
    return run(h.set_rule(agent=AGENT, account=ACCOUNT, action=action, actor="owner", **how))


def call(core, tool, arguments=None, agent=AGENT):
    return run(hands.call(core, agent=agent, tool=tool, arguments=arguments))


def events(core, kind):
    return [e for e in core.tape.after(0, limit=5000) if e.kind == kind]


def tape_text(core) -> str:
    return json.dumps([[e.kind, e.actor, e.subject, e.payload]
                       for e in core.tape.after(0, limit=5000)])


def allow_alone():
    """Stand in for the package that will decide when a cautious action may run alone."""
    hands.set_level_gate(lambda core, **asked: None)
