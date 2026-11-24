"""What every step shares: its error, the line it writes, and reading a model's answer."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from articles.context import Ctx


class StepError(Exception):
    """A step could not finish. ``str()`` is the sentence for the owner."""


def turn(ctx: Ctx, piece_id: int, step: str, gloss: str, **more: Any) -> str:
    ctx.store.add_turn(piece_id, step, gloss, **more)
    return gloss


def data_of(reply: Any, what: str) -> dict[str, Any]:
    """A model's answer as the object asked for, or ``StepError``."""
    data = reply.data
    if data is None:
        try:
            data = json.loads(reply.text)
        except (TypeError, ValueError):
            data = None
    if not isinstance(data, dict):
        raise StepError(f"The model's {what} could not be read as the shape Articles asked "
                        "for. Nothing was kept from it.")
    return data


def made_by(reply: Any) -> dict[str, Any]:
    """Which model answered, and whether it ran on the owner's own machines."""
    said = dataclasses.asdict(reply)
    return {"model": said.get("model") or "", "local": said.get("local")}


def cites_of(raw: Any, sentences: dict[tuple[int, int], str]) -> list[list[int]]:
    """The real (source, sentence) pairs in a model's ``cites``; anything else is dropped."""
    out: list[list[int]] = []
    for cite in raw if isinstance(raw, list) else []:
        if not isinstance(cite, dict):
            continue
        pair = (cite.get("source"), cite.get("sentence"))
        if all(isinstance(x, int) and not isinstance(x, bool) for x in pair) \
                and pair in sentences and list(pair) not in out:
            out.append(list(pair))
    return out
