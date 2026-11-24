"""Backups over HTTP, under ``/api/backup``. Who may do what:

- reading the status (each folder's last backup, failure and test; never a path): the
  owner or a read token;
- ``Back up now`` and ``Test the newest backup``: the signed-in owner only. Each starts in
  a thread of its own and answers at once; the status and the Tape say how it went.

No route restores a backup. Putting one in place replaces everything JimmyHQ keeps, so it
is the owner's act at the computer itself: ``jimmyhq backup restore``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from jimmyhq.auth import Principal
from jimmyhq.backup import service
from jimmyhq.core import Core
from jimmyhq.web.deps import owner, reader


class Runs:
    """The threads started from this process, so a test can wait for one."""

    def __init__(self) -> None:
        self.last = None


def runs(core: Core) -> Runs:
    return core.service("backup.runs", lambda _core: Runs())


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/backup", tags=["backup"])

    @router.get("/status")
    def read_status(_: Principal = Depends(reader)):  # noqa: B008
        return service.status(core)

    def begin(who: Principal, *, test: bool) -> JSONResponse:
        if not service.is_on(core):
            raise HTTPException(409, service.OFF)
        try:
            runs(core).last = service.start(core, actor=who.actor, test=test)
        except service.Busy as exc:
            raise HTTPException(409, str(exc)) from None
        return JSONResponse({"started": True, "status": service.status(core)},
                            status_code=202)

    @router.post("/now")
    def back_up_now(who: Principal = Depends(owner)):  # noqa: B008
        return begin(who, test=False)

    @router.post("/test")
    def test_newest(who: Principal = Depends(owner)):  # noqa: B008
        return begin(who, test=True)

    return router
