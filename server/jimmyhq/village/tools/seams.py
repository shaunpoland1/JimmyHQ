"""Where the tools meet the rest of the runtime.

The beat loop defines ``Tool``, ``Done`` and ``Refusal``; the night defines the diary and
the memory store. They are imported here, when first needed, so that a tool's rules can
be read and tested without the whole runtime in place.
"""

from __future__ import annotations

from typing import Any, NoReturn

from jimmyhq.village.comms import Refused
from jimmyhq.village.files import FilesError
from jimmyhq.village.tools import logged

WORK, EXPLORE, OUTLET, SOCIAL = "work", "explore", "outlet", "social"
WEO = (WORK, EXPLORE, OUTLET)


def done(result: str, args_logged: dict[str, Any] | None = None, category: str = "self") -> Any:
    """What a tool hands back when it did the thing. The logged arguments are scrubbed
    here, every time. A tool that will not do it calls ``refuse`` instead: see ``Done``
    for the one rule between the two."""
    from jimmyhq.village.catalog import Done

    return Done(result=result, args_logged=logged.args(args_logged or {}), category=category)


def refuse(rule: str, sentence: str, *, frustrate: bool = True,
           args_logged: dict[str, Any] | None = None) -> NoReturn:
    from jimmyhq.village.rules import Refusal

    raise Refusal(rule, sentence, frustrate=frustrate,
                  args_logged=logged.args(args_logged) if args_logged is not None else None)


def refuse_as(error: Refused | FilesError, args_logged: dict[str, Any] | None = None) -> NoReturn:
    """Pass on what a rule in ``comms`` or ``files`` said."""
    kept = getattr(error, "args_logged", None)
    refuse(error.rule, error.sentence, frustrate=getattr(error, "frustrate", True),
           args_logged=kept if kept is not None else args_logged)


def category_of(mode: str) -> str:
    """What a file action counts as for the body: the mode it was done in."""
    return mode if mode in WEO else WORK


def task_over(ctx: Any) -> None:
    """The task this beat started on is done or set aside, so the action that ended it
    must not be recorded against it: the body refuses actions on a task that is down."""
    ctx.task = None


def text(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    return "" if value is None else str(value)


def flag(args: dict[str, Any], name: str) -> bool:
    value = args.get(name)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)
