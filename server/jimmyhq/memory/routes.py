"""Memory over HTTP, under ``/api/memory``. Who may do what:

- reading the tree, a note, its history, facts, status, sources, proposed
  facts and the timeline, and searching: the owner or a read token;
- anything that changes the Library (saving, moving or deleting a note,
  adding, changing or forgetting a fact, keeping or declining a proposed
  fact, mining now, scanning, rebuilding): the signed-in owner only. A token
  cannot rewrite the owner's memory in this release.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

import jimmyhq.memory as memory
from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.memory.errors import LibraryError
from jimmyhq.memory.library import MAX_FACT, MAX_REASON
from jimmyhq.memory.paths import MAX_PATH
from jimmyhq.memory.search import EVERYWHERE, MAX_QUERY
from jimmyhq.memory.settings import KIND
from jimmyhq.web.deps import owner, reader

_MAX_TEXT = 4096 * 1024 + 1024  # the largest the note size setting can allow, and a little


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaveBody(_Body):
    text: str = Field(max_length=_MAX_TEXT)
    revision: str | None = Field(None, min_length=64, max_length=64, pattern="^[0-9a-f]+$")


class MoveBody(_Body):
    from_: str = Field(alias="from", max_length=MAX_PATH)
    to: str = Field(max_length=MAX_PATH)


class NewFactBody(_Body):
    text: str = Field(max_length=MAX_FACT * 2)
    kind: str = Field(max_length=40)
    note: str | None = Field(None, max_length=MAX_PATH)


class FactChange(_Body):
    text: str | None = Field(None, max_length=MAX_FACT * 2)
    kind: str | None = Field(None, max_length=40)
    confirmed: bool | None = Field(None, strict=True)


class SearchBody(_Body):
    q: str = Field(max_length=MAX_QUERY)
    scope: list[str] = Field(default_factory=lambda: ["shared"], min_length=1, max_length=20)
    k: int | None = Field(None, strict=True, ge=1, le=50)


class ForgetBody(_Body):
    reason: str | None = Field(None, max_length=MAX_REASON)


class DeclineBody(_Body):
    reason: str = Field(max_length=MAX_REASON)


class MineBody(_Body):
    source: str | None = Field(None, max_length=40)


def _do[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except LibraryError as exc:
        raise HTTPException(exc.status, exc.detail) from None


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/memory", tags=["memory"])

    def lib():
        return memory.library(core)

    # --- the Library ----------------------------------------------------------

    @router.get("/tree")
    def tree(_: Principal = Depends(reader)):  # noqa: B008
        return _do(lib().tree)

    @router.get("/status")
    def status(who: Principal = Depends(reader)):  # noqa: B008
        # Where the Library is on disk is the owner's to know, not a token's.
        return _do(memory.status, core, show_path=who.is_owner)

    @router.post("/scan")
    def scan(_: Principal = Depends(owner)):  # noqa: B008
        return {"scan": _do(memory.scan, core)}

    @router.post("/reindex")
    def reindex(_: Principal = Depends(owner)):  # noqa: B008
        return {"scan": _do(memory.reindex, core)}

    @router.post("/notes/move")
    def note_move(body: MoveBody, who: Principal = Depends(owner)):  # noqa: B008
        return _do(memory.move_note, core, body.from_, body.to, actor=who.actor)

    # The history routes come first: a note's own path may contain slashes.
    @router.get("/notes/{path:path}/history/{version}")
    def note_version(path: str, version: str, _: Principal = Depends(reader)):  # noqa: B008
        if version.lower().endswith(".md"):
            # Not a version at all: a note that lives in a folder called "history".
            return _do(memory.read_note, core, f"{path}/history/{version}")
        return _do(lib().version, path, version)

    @router.get("/notes/{path:path}/history")
    def note_history(path: str, limit: int = Query(100, ge=1, le=500),
                     _: Principal = Depends(reader)):  # noqa: B008
        return {"path": path, "versions": _do(lib().history, path, limit=limit)}

    @router.get("/notes/{path:path}")
    def note_read(path: str, _: Principal = Depends(reader)):  # noqa: B008
        return _do(memory.read_note, core, path)

    @router.put("/notes/{path:path}")
    def note_save(path: str, body: SaveBody, who: Principal = Depends(owner)):  # noqa: B008
        return _do(memory.save_note, core, path, body.text, actor=who.actor,
                   revision=body.revision)

    @router.delete("/notes/{path:path}")
    def note_delete(path: str, history: bool = Query(False),
                    who: Principal = Depends(owner)):  # noqa: B008
        purged = _do(memory.delete_note, core, path, actor=who.actor, history=history)
        return {"ok": True} | ({"purged": purged} if history else {})

    # --- facts ----------------------------------------------------------------

    @router.get("/facts")
    def facts_list(
        kind: str | None = Query(None, max_length=40),
        q: str | None = Query(None, max_length=200),
        confirmed: bool | None = Query(None),
        note: str | None = Query(None, max_length=MAX_PATH),
        limit: int = Query(50, ge=1, le=200),
        before: str | None = Query(None, max_length=60),
        _: Principal = Depends(reader),  # noqa: B008
    ):
        if kind is not None and not KIND.match(kind):
            raise HTTPException(422, "kind is a short lower-case word such as preference.")
        return _do(memory.list_facts, core, kind=kind, q=q, confirmed=confirmed, note=note,
                   limit=limit, before=before)

    @router.post("/facts", status_code=201)
    def facts_add(body: NewFactBody, who: Principal = Depends(owner)):  # noqa: B008
        # The owner typing a fact in is the owner confirming it.
        fact = _do(memory.remember, core, body.text, kind=body.kind, note=body.note,
                   actor=who.actor, confirmed=True)
        return {"fact": fact.as_dict(), "created": fact.created}

    @router.get("/facts/{fact_id}")
    def facts_get(fact_id: str, _: Principal = Depends(reader)):  # noqa: B008
        return {"fact": _do(memory.get_fact, core, fact_id, live_only=False).as_dict()}

    @router.patch("/facts/{fact_id}")
    def facts_change(fact_id: str, body: FactChange,
                     who: Principal = Depends(owner)):  # noqa: B008
        fact = _do(memory.edit_fact, core, fact_id, actor=who.actor, text=body.text,
                   kind=body.kind, confirmed=body.confirmed)
        return {"fact": fact.as_dict()}

    @router.delete("/facts/{fact_id}")
    def facts_forget(fact_id: str, body: ForgetBody | None = None,
                     everywhere: bool = Query(False),
                     who: Principal = Depends(owner)):  # noqa: B008
        reason = body.reason if body else None
        if everywhere:
            done = _do(memory.forget_everywhere, core, fact_id, actor=who.actor, reason=reason)
            return {"fact": done["fact"].as_dict(), "purged": done["purged"]}
        fact = _do(memory.forget, core, fact_id, actor=who.actor, reason=reason)
        return {"fact": fact.as_dict()}

    # --- mining ---------------------------------------------------------------

    @router.get("/sources")
    def sources(_: Principal = Depends(reader)):  # noqa: B008
        return _do(memory.sources_status, core)

    @router.get("/candidates")
    def candidates(
        state: str | None = Query(None, max_length=20),
        source: str | None = Query(None, max_length=40),
        limit: int = Query(50, ge=1, le=200),
        before: str | None = Query(None, max_length=60),
        _: Principal = Depends(reader),  # noqa: B008
    ):
        return _do(memory.list_candidates, core, state=state, source=source, limit=limit,
                   before=before)

    @router.post("/candidates/{candidate_id}/keep")
    def candidate_keep(candidate_id: str, _: Principal = Depends(owner)):  # noqa: B008
        return {"candidate": _do(memory.keep_candidate, core, candidate_id)}

    @router.post("/candidates/{candidate_id}/decline")
    def candidate_decline(candidate_id: str, body: DeclineBody,
                          _: Principal = Depends(owner)):  # noqa: B008
        return {"candidate": _do(memory.decline_candidate, core, candidate_id, body.reason)}

    @router.delete("/candidates/{candidate_id}")
    def candidate_erase(candidate_id: str, _: Principal = Depends(owner)):  # noqa: B008
        return {"candidate": _do(memory.erase_candidate, core, candidate_id)}

    @router.post("/mine")
    def mine(body: MineBody | None = None, _: Principal = Depends(owner)):  # noqa: B008
        # Not async: the pass waits on a model, and runs on a worker thread.
        return _do(memory.mine, core, source=body.source if body else None, trigger="owner")

    @router.get("/activity")
    def activity(limit: int = Query(50, ge=1, le=200),
                 before: str | None = Query(None, max_length=26),
                 _: Principal = Depends(reader)):  # noqa: B008
        return _do(memory.activity, core, limit=limit, before=before)

    # --- search ---------------------------------------------------------------

    async def run_search(q: str, folders: list[str], k: int | None, who: Principal):
        if EVERYWHERE in folders:
            folders = [EVERYWHERE]
        try:
            found = await memory.arecall(core, q, actor=who.actor, caller="memory.search",
                                         scope=folders, k=k,
                                         related_max=core.settings.get("memory.related_max"))
        except LibraryError as exc:
            raise HTTPException(exc.status, exc.detail) from None
        return found.as_dict()

    @router.get("/search")
    async def search(
        q: str = Query(max_length=MAX_QUERY),
        scope: str = Query("shared", max_length=MAX_PATH),
        k: int | None = Query(None, ge=1, le=50),
        who: Principal = Depends(reader),  # noqa: B008
    ):
        folders = [s.strip() for s in scope.split(",") if s.strip()] or ["shared"]
        return await run_search(q, folders, k, who)

    @router.post("/search")
    async def search_privately(body: SearchBody,
                               who: Principal = Depends(reader)):  # noqa: B008
        """The same search with the question in the body, where the web
        server's access log does not record it. Screens should use this one."""
        return await run_search(body.q, body.scope, body.k, who)

    return router
