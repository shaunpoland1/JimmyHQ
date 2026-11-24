"""The Bench over HTTP. Who may do what:

- reading the Bench, a card or one of its files: the owner or a read token;
- filing: the owner or a write token, and a token files only as itself;
- updating or withdrawing: whoever filed the card, or the owner;
- deciding, snoozing and the ladder's levels: the signed-in owner only.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from jimmyhq.auth import Principal
from jimmyhq.bench import cards, ladder, rules
from jimmyhq.bench.rules import BenchError
from jimmyhq.core import Core
from jimmyhq.web.deps import owner, reader, writer

# Types a browser may be told about. Anything else is sent as plain bytes, and
# everything is sent as a download, so a file can never run as a page.
_SAFE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
               "text/plain", "text/markdown", "text/csv", "application/json"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileBody(cards.NewCard):
    filed_by: str | None = Field(None, max_length=80)


class UpdateBody(_Body):
    note: str | None = Field(None, max_length=rules.MAX_NOTE)
    title: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=rules.MAX_BODY)


class WithdrawBody(_Body):
    note: str | None = Field(None, max_length=rules.MAX_NOTE)


class DecideBody(_Body):
    action: str = Field(max_length=40)
    note: str | None = Field(None, max_length=rules.MAX_NOTE)
    reason: str | None = Field(None, max_length=rules.MAX_NOTE)
    # The card's `updates` count as the owner saw it. A decision is for the
    # words that were on screen, so it is refused if the card changed since.
    seen_updates: int = Field(strict=True, ge=0, le=rules.MAX_UPDATES)


class SnoozeBody(_Body):
    until: int | None = Field(None, strict=True, ge=0, le=2**53)


class LadderBody(_Body):
    actor: str = Field(max_length=80)
    tool: str = Field(max_length=60)
    action: str = Field(max_length=60)
    level: Literal["read", "approve", "alone"]
    category: str | None = Field(None, max_length=40)


def _do[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except BenchError as exc:
        raise HTTPException(exc.status, exc.detail) from None


def _disposition(name: str) -> str:
    plain = "".join(ch if ch.isascii() and ch.isprintable() and ch not in '"\\/;' else "_"
                    for ch in name) or "file"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/bench", tags=["bench"])

    # --- the ladder (declared first, so "ladder" is never read as a card id) ---

    @router.get("/ladder")
    def ladder_table(_: Principal = Depends(reader)):  # noqa: B008
        return ladder.table(core)

    @router.put("/ladder")
    def ladder_set(body: LadderBody, who: Principal = Depends(owner)):  # noqa: B008
        return _do(ladder.set_level, core, body.actor, body.tool, body.action, body.level,
                   by=who.actor, category=body.category)

    # --- reading ---------------------------------------------------------------

    @router.get("")
    def bench(_: Principal = Depends(reader)):  # noqa: B008
        return cards.board(core)

    @router.get("/files/{card_id}/{index}")
    def card_file(card_id: str, index: str, _: Principal = Depends(reader)):  # noqa: B008
        position = int(index) if index.isascii() and index.isdigit() and len(index) < 6 else -1
        name, data = _do(cards.read_file, core, card_id, position)
        guessed = mimetypes.guess_type(name)[0]
        return Response(
            data, media_type=guessed if guessed in _SAFE_TYPES else "application/octet-stream",
            headers={"Content-Disposition": _disposition(name), "Cache-Control": "private"},
        )

    @router.get("/{card_id}")
    def card(card_id: str, _: Principal = Depends(reader)):  # noqa: B008
        found = cards.get_card(core, card_id)
        if found is None:
            raise HTTPException(404, "There is no card with this id. Reload the Bench.")
        return found

    # --- the filer's side ------------------------------------------------------

    @router.post("", status_code=201)
    def file(body: FileBody, who: Principal = Depends(writer)):  # noqa: B008
        fields = body.model_dump(exclude={"filed_by"})
        filed_by = body.filed_by or who.actor
        if not who.is_owner:
            # A token speaks for itself only: not for another filer, and not
            # for another actor's row on the ladder.
            if filed_by != who.actor:
                raise HTTPException(403, "A token can only file cards as itself. Leave out "
                                         "filed_by.")
            if body.ladder and body.ladder.actor != who.actor:
                raise HTTPException(403, "A token can only file cards for its own actions. "
                                         f"Set ladder.actor to {who.actor!r}.")
            # No route lets a token store a Vault blob, so any reference it
            # sends is to someone else's: attaching it would hand that over.
            if body.files:
                raise HTTPException(403, "A token cannot attach files by Vault reference "
                                         "yet. File the card without files.")
        card_id = _do(cards.file_card, core, filed_by=filed_by, **fields)
        return cards.get_card(core, card_id)

    @router.post("/{card_id}/update")
    def update(card_id: str, body: UpdateBody, who: Principal = Depends(writer)):  # noqa: B008
        _do(cards.update_card, core, card_id, by=who.actor, **body.model_dump())
        return cards.get_card(core, card_id)

    @router.post("/{card_id}/withdraw")
    def withdraw(card_id: str, body: WithdrawBody | None = None,
                 who: Principal = Depends(writer)):  # noqa: B008
        _do(cards.withdraw_card, core, card_id, by=who.actor, note=body.note if body else None)
        return cards.get_card(core, card_id)

    # --- the owner's side ------------------------------------------------------

    @router.post("/{card_id}/decide")
    def decide(card_id: str, body: DecideBody, _: Principal = Depends(owner)):  # noqa: B008
        return _do(cards.decide, core, card_id, action=body.action, note=body.note,
                   reason=body.reason, seen_updates=body.seen_updates)

    @router.post("/{card_id}/snooze")
    def snooze(card_id: str, body: SnoozeBody | None = None,
               _: Principal = Depends(owner)):  # noqa: B008
        return _do(cards.snooze, core, card_id, body.until if body else None)

    @router.post("/{card_id}/unsnooze")
    def unsnooze(card_id: str, _: Principal = Depends(owner)):  # noqa: B008
        return _do(cards.unsnooze, core, card_id)

    return router
