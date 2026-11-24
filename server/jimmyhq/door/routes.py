"""The owner's API for the door, under ``/api/door``. Every route is for the signed-in
owner only: an API token cannot name a client, read a client's activity or see the
door's address.

Also here: ``/mcp`` on JimmyHQ's own address. It answers only when the owner has both
switched the door on and chosen to mount it there (``door.listen``); otherwise it is a
404 like any address that does not exist.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from jimmyhq import door as _door
from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.door import protocol
from jimmyhq.door.service import DoorError
from jimmyhq.web.deps import owner


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewClient(_Body):
    name: str = Field(max_length=64)
    about: str = Field("", max_length=1000)


class ClientChange(_Body):
    about: str | None = Field(None, max_length=1000)
    tools: dict[str, int | None] | None = None
    options: dict[str, Any] | None = None


class UseAddress(_Body):
    use: Literal["tailnet", "local"]


def build_router(core: Core) -> APIRouter:
    router = APIRouter(tags=["door"])
    d = _door.door(core)

    def run(fn: Any, *args: Any, **kw: Any) -> Any:
        try:
            return fn(*args, **kw)
        except DoorError as exc:
            raise HTTPException(exc.status, exc.detail) from None

    def one(client_id: str) -> dict[str, Any]:
        found = next((c for c in d.clients() if c["id"] == client_id), None)
        if found is None:
            raise HTTPException(404, "There is no client with that id. Reload the page.")
        return found

    @router.get("/api/door/status")
    def status(_: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return d.status()

    @router.get("/api/door/tailnet")
    def tailnet(_: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return d.tailnet()

    @router.post("/api/door/address")
    def use_address(body: UseAddress, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return run(d.use_address, body.use, actor=who.actor)

    @router.get("/api/door/clients")
    def clients(_: Principal = Depends(owner)) -> list[dict[str, Any]]:  # noqa: B008
        return d.clients()

    @router.post("/api/door/clients")
    def add(body: NewClient, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        client, token = run(d.add_client, body.name, body.about, by=who.actor)
        # The only time the token is ever sent anywhere. It is not stored.
        return {"client": one(client["id"]), "token": token, **d.address()}

    @router.put("/api/door/clients/{client_id}")
    def change(client_id: str, body: ClientChange,
               who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        run(d.change_client, client_id, by=who.actor, about=body.about, tools_=body.tools,
            options=body.options)
        return one(client_id)

    @router.post("/api/door/clients/{client_id}/rotate")
    def rotate(client_id: str, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        token = run(d.rotate_client, client_id, by=who.actor)
        return {"client": one(client_id), "token": token, **d.address()}

    @router.post("/api/door/clients/{client_id}/revoke")
    def revoke(client_id: str, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        run(d.revoke_client, client_id, by=who.actor)
        return one(client_id)

    @router.get("/api/door/calls")
    def calls(client_id: str | None = Query(None, max_length=40),
              limit: int = Query(100, ge=1, le=300),
              _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return {"calls": d.calls(client_id, limit)}

    @router.api_route(protocol.PATH, methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH",
                                              "OPTIONS"], include_in_schema=False)
    async def mcp(request: Request) -> Response:
        if not (d.enabled() and d.on_main_port()):
            return Response(status_code=404)
        return await protocol.handle(d, request)

    return router
