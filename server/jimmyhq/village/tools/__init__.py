"""What an agent can do: the tool catalog.

``ALL`` is the list of tools, built when first asked for from each module's ``SPECS``.
A spec is the plain description of one tool: its name, kind, the modes that offer it, its
words, its arguments and the function that runs it. Descriptions say ``{owner}`` where the
owner's name goes; the runtime fills it in.

Every tool's ``run(ctx, args)`` returns what happened or raises the runtime's refusal with
the name of the rule that said no. The rules are named functions, listed in ``RULES`` with
the setting each one reads, and each has its own test.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

log = logging.getLogger("jimmyhq.village.tools")

_MODULES = ("files", "self", "tasks", "work", "findings", "talk", "web", "outside", "hands",
            "browser")
_all: list[Any] | None = None


def specs() -> list[dict[str, Any]]:
    """Every tool as a plain dict, in catalog order."""
    out: list[dict[str, Any]] = []
    for name in _MODULES:
        out.extend(importlib.import_module(f"{__name__}.{name}").SPECS)
    return out


def rules() -> list[tuple[str, str | None, Any]]:
    """Every rule the tools check: ``(name, setting it reads or None, function)``."""
    out: list[tuple[str, str | None, Any]] = []
    for name in _MODULES:
        out.extend(importlib.import_module(f"{__name__}.{name}").RULES)
    return out


def _register_rules() -> None:
    """Tell the runtime's rule registry about ours. It may be a plain registration or a
    decorator; either way each rule is named once."""
    try:
        from jimmyhq.village.rules import rule
    except ImportError:
        return
    for name, setting, fn in rules():
        try:
            made = rule(name, setting)
            if callable(made):
                made(fn)
        except Exception:  # noqa: BLE001 - a registry that already has it is no fault
            log.debug("rule %s was not registered", name, exc_info=True)


def build() -> list[Any]:
    from jimmyhq.village.catalog import Tool

    _register_rules()
    return [Tool(**spec) for spec in specs()]


def __getattr__(name: str) -> Any:
    global _all
    if name == "ALL":
        if _all is None:
            _all = build()
        return _all
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
