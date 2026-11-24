"""The Bench: the owner's single inbox for everything waiting on their decision,
and the permission ladder that says what may happen without asking.

Other modules use the functions exported here. They do not print ``bench.*``
or ``ladder.*`` events themselves.

    from jimmyhq import bench

    card_id = bench.file_card(core, filed_by="agent:scout", kind="posts.draft",
                              source="Posts", title="Draft ready", body=text)
    bench.on_decision("posts.", lambda core, decision: ...)

See jimmyhq/modules.py for how a module plugs in, and docs/bench.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from jimmyhq.bench.cards import (
    Decision,
    board,
    file_card,
    get_card,
    on_decision,
    update_card,
    withdraw_card,
)
from jimmyhq.bench.ladder import (
    LadderError,
    cautious,
    check,
    record_alone_action,
    set_level,
)
from jimmyhq.bench.rules import BenchError
from jimmyhq.core import Core

__all__ = [
    "BenchError", "Decision", "LadderError", "board", "build", "cautious", "check",
    "file_card", "get_card", "on_decision", "record_alone_action", "set_level",
    "update_card", "withdraw_card",
]


def build(core: Core) -> APIRouter:
    from jimmyhq.bench.routes import build_router

    return build_router(core)
