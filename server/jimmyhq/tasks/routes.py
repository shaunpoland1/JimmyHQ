"""The task routes: thin wrappers that turn a request into one service call."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.tasks.service import TaskError, service
from jimmyhq.web.deps import reader, writer

_LONG = 20_000  # a ceiling on any one text field; the service gives the real limits
Filter = Annotated[str | None, Query(max_length=100)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=_LONG)


class NewTask(_Body):
    title: str
    notes: str = ""
    area: str | None = None
    due: str | None = None
    due_time: str | None = None
    repeat: str | None = None
    assignee: str = "owner"


class TaskChanges(_Body):
    title: str | None = None
    notes: str | None = None
    area: str | None = None
    due: str | None = None
    due_time: str | None = None
    repeat: str | None = None
    assignee: str | None = None


class PushTo(_Body):
    to: str


class RankNextTo(_Body):
    before: str | None = None
    after: str | None = None


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["tasks"])
    tasks = service(core)

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except TaskError as exc:
            raise HTTPException(exc.status, exc.detail) from None

    @router.get("")
    def board(_: Principal = Depends(reader), assignee: Filter = None,  # noqa: B008
              area: Filter = None):
        return call(tasks.board, assignee=assignee, area=area)

    @router.post("", status_code=201)
    def create(body: NewTask, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.create, actor=who.actor, **body.model_dump())

    @router.get("/{task_id}")
    def get(task_id: str, _: Principal = Depends(reader)):  # noqa: B008
        return call(tasks.get, task_id)

    @router.patch("/{task_id}")
    def edit(task_id: str, body: TaskChanges, who: Principal = Depends(writer)):  # noqa: B008
        changes = body.model_dump(exclude_unset=True)
        for field in ("title", "assignee"):
            if field in changes and changes[field] is None:
                raise HTTPException(422, f"{field}: cannot be empty.")
        return call(tasks.edit, task_id, actor=who.actor, **changes)

    @router.post("/{task_id}/done")
    def done(task_id: str, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.complete, task_id, actor=who.actor)

    @router.post("/{task_id}/reopen")
    def reopen(task_id: str, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.reopen, task_id, actor=who.actor)

    @router.post("/{task_id}/undo-round")
    def undo_round(task_id: str, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.undo_round, task_id, actor=who.actor)

    @router.post("/{task_id}/push")
    def push(task_id: str, body: PushTo, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.push, task_id, body.to, actor=who.actor)

    @router.post("/{task_id}/unpush")
    def unpush(task_id: str, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.unpush, task_id, actor=who.actor)

    @router.post("/{task_id}/drop")
    def drop(task_id: str, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.drop, task_id, actor=who.actor)

    @router.post("/{task_id}/restore")
    def restore(task_id: str, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.restore, task_id, actor=who.actor)

    @router.post("/{task_id}/rank")
    def rank(task_id: str, body: RankNextTo, who: Principal = Depends(writer)):  # noqa: B008
        return call(tasks.rank, task_id, actor=who.actor, before=body.before, after=body.after)

    return router
