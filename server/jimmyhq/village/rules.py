"""The village's mechanical rules, by name.

Every rule the runtime enforces without asking a model is one named function, so it can
be found, read and tested on its own::

    @rule("heartbeat")
    def heartbeat_seconds(...): ...

``RULES`` maps each name to its function and, where a number decides it, the setting
that holds the number.

A tool that will not do what was asked raises ``Refusal``. The sentence is for the
agent: second person, says that nothing happened, gives the number, and says what to do
instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# The kind of ``ModelError`` the model layer raises when a reply ran out of room
# (``jimmyhq.models.shapes.FAILURES``). The beat and the night both read it from here.
CUT_OFF = "truncated"


@dataclass(frozen=True)
class Rule:
    name: str
    setting: str | None
    fn: Callable[..., Any]


RULES: dict[str, Rule] = {}


Fn = Callable[..., Any]


def rule(name: str, setting: str | None = None) -> Callable[[Fn], Fn]:
    """Register the decorated function as the rule called ``name``."""

    def wrap(fn: Fn) -> Fn:
        known = RULES.get(name)
        if known is not None and (known.fn.__module__, known.fn.__qualname__) != (
                fn.__module__, fn.__qualname__):
            raise ValueError(f"the rule {name!r} is already {known.fn.__qualname__}")
        RULES[name] = Rule(name, setting, fn)
        return fn

    return wrap


class Refusal(Exception):  # noqa: N818 - it is an answer to the agent, not a fault
    """An action the village will not do. Nothing happened.

    ``frustrate=False`` is for a village limit or a slip in the reply: the agent did
    nothing wrong, so its body is not charged. ``args_logged`` replaces the arguments
    in the log (leave out long text and anything private)."""

    def __init__(self, rule: str, sentence: str, frustrate: bool = True,
                 args_logged: dict[str, Any] | None = None):
        super().__init__(sentence)
        self.rule = rule
        self.sentence = sentence
        self.frustrate = frustrate
        self.args_logged = args_logged
