"""Routes for nodes, in three groups with three different keys.

- ``/api/nodes``, ``/api/nodes/join-codes``, ``/api/nodes/<id>``: the signed-in
  owner only. An API token cannot list nodes, make a join code or remove a node.
- ``/api/nodes/join`` and ``/api/nodes/link/...``: a second machine. The join takes
  a one-time code; everything after takes the node token it was given.
- ``/api/nodes/relay/...``: this install's own processes, calling a model that
  lives on a node.

The gate (``nodes.admit``) has already decided who is asking before any of these run.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from jimmyhq import __version__
from jimmyhq.auth import AuthError, Principal
from jimmyhq.core import Core
from jimmyhq.nodes import protocol
from jimmyhq.nodes.service import MAX_BODY, POLL_S, Hub, NodeError
from jimmyhq.web.deps import node, owner, relay
from jimmyhq.web.locality import client_key

_JOIN_REFUSALS = {
    "wrong": (401, "That join code is not right. Nothing was joined. Check it against the "
                   "one shown under Settings, Nodes on the hub."),
    "expired": (410, "That join code has run out. Nothing was joined. Make a new one under "
                     "Settings, Nodes on the hub."),
    "used": (409, "That join code has already been used. Nothing was joined. Make a new one "
                  "under Settings, Nodes on the hub."),
    "locked": (429, "Too many wrong join codes from this machine. Nothing was joined. Try "
                    "again in 15 minutes."),
}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JoinBody(_Body):
    code: str = Field(max_length=64)
    name: str = Field(max_length=64)
    version: str | None = Field(None, max_length=40)
    protocol: int | None = None


def _error(exc: NodeError) -> JSONResponse:
    # The shape an OpenAI-compatible client reads an error from.
    return JSONResponse({"error": {"message": exc.detail}, "detail": exc.detail}, exc.status)


def build_router(core: Core, hub: Hub) -> APIRouter:
    router = APIRouter(tags=["nodes"])
    if core.auth.list_nodes():
        # A changed port moves the relay; point the nodes' providers at it again.
        # An install with no nodes does nothing here.
        hub.sync_providers()

    # --- the owner --------------------------------------------------------------

    @router.get("/api/nodes")
    def nodes_list(_: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return hub.describe()

    @router.post("/api/nodes/join-codes", status_code=201)
    def join_code(_: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        minutes = core.settings.get("nodes.join_code_minutes")
        code, expires = core.auth.create_node_join_code(minutes)
        # Shown once. Nothing about it reaches the Tape.
        return {"code": code, "expires_ms": expires, "minutes": minutes}

    @router.delete("/api/nodes/{node_id}")
    async def nodes_remove(node_id: str, who: Principal = Depends(owner)  # noqa: B008
                           ) -> dict[str, Any]:
        done = await run_in_threadpool(hub.remove, node_id, actor=who.actor, reason="removed")
        if done is None:
            raise HTTPException(404, "There is no node with that id. Reload the page.")
        hub.cut_off(node_id)
        return {"ok": True, **done}

    # --- a machine joining ------------------------------------------------------

    @router.post(protocol.JOIN)
    def join(body: JoinBody, request: Request) -> dict[str, Any]:
        # A join relayed by a tunnel on this computer is counted apart from the computer
        # itself (``locality.client_key``), so wrong codes through it never stop a join
        # at the machine, and they count towards the overall ceiling for remote callers.
        client = client_key(request, core.config.host)
        name = body.name.strip().lower()
        try:
            outcome, node_id, token = core.auth.redeem_node_join_code(body.code, name, client)
        except AuthError as exc:
            raise HTTPException(422, f"{exc} Nothing was joined.") from None
        if outcome == "name_taken":
            raise HTTPException(
                409, f"A machine called {name} has already joined. Nothing was joined. Run "
                     "the command again with --name and another name.")
        if outcome != "ok":
            status, detail = _JOIN_REFUSALS[outcome]
            raise HTTPException(status, detail)
        version = body.version if protocol.VERSION.fullmatch(body.version or "") else None
        hub.joined(node_id, name, version)
        return {"node_id": node_id, "name": name, "token": token,
                "hub_version": __version__, "protocol": protocol.PROTOCOL}

    # --- a joined node ----------------------------------------------------------

    @router.post(f"{protocol.LINK}/heartbeat")
    async def heartbeat(request: Request, who: tuple[str, str] = Depends(node)  # noqa: B008
                        ) -> dict[str, Any]:
        try:
            body = json.loads(await request.body())
            if not isinstance(body, dict):
                raise ValueError("A heartbeat must be a JSON object.")
            return await run_in_threadpool(hub.heartbeat, who[0], who[1], body)
        except ValueError as exc:
            raise HTTPException(422, f"{exc} Nothing was recorded.") from None

    @router.get(f"{protocol.LINK}/jobs")
    async def jobs(wait: float = Query(POLL_S, ge=0, le=60),
                   who: tuple[str, str] = Depends(node)) -> Response:  # noqa: B008
        try:
            job = await hub.next_job(who[0], wait)
        except NodeError as exc:
            raise HTTPException(exc.status, exc.detail) from None
        if job is None:
            return Response(status_code=204)
        return JSONResponse(job.as_dict())

    @router.post(f"{protocol.LINK}/jobs/{{job_id}}/reply")
    async def reply(job_id: str, request: Request,
                    who: tuple[str, str] = Depends(node)) -> dict[str, Any]:  # noqa: B008
        job = hub.job_for(who[0], job_id)
        if job is None or not await hub.receive(job, request.stream()):
            # Its reader gave up, or it was never this node's. The node stops.
            raise HTTPException(410, "Nobody is waiting for this answer any more.")
        return {"ok": True}

    @router.post(f"{protocol.LINK}/leave")
    async def leave(who: tuple[str, str] = Depends(node)) -> dict[str, Any]:  # noqa: B008
        await run_in_threadpool(hub.remove, who[0], actor=f"node:{who[1]}", reason="left")
        hub.cut_off(who[0])
        return {"ok": True}

    # --- this machine's own processes calling a model on a node -----------------

    @router.api_route(protocol.RELAY + "/{node_id}/{offer}/{rest:path}",
                      methods=["GET", "POST"])
    async def relay_call(node_id: str, offer: str, rest: str, request: Request,
                         _: Principal = Depends(relay)) -> Response:  # noqa: B008
        path = f"/{rest}"
        if protocol.MODEL_PATHS.get(path) != request.method:
            raise HTTPException(404, "There is nothing at this address.")
        size, parts = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                raise HTTPException(413, "The request is too large to pass to a node.")
            parts.append(chunk)
        payload = {"method": request.method, "path": path,
                   "body": b"".join(parts).decode("utf-8", "replace")}
        try:
            job = await hub.submit(node_id, "model", offer, payload)
            status, content_type = await hub.head(job, request.is_disconnected)
            if status != 200 or content_type != "text/event-stream":
                body = await hub.body(job, request.is_disconnected)
                return Response(body, status, media_type=content_type)
        except NodeError as exc:
            return _error(exc)

        async def events():
            rest_of = b""
            try:
                async for chunk in hub.chunks(job):
                    ready, rest_of = protocol.complete_events(rest_of + chunk)
                    if ready:
                        yield ready
                if rest_of:
                    yield rest_of
            except NodeError as exc:
                # Said inside the stream, where a reader of server-sent events
                # looks for it; the unfinished event before it is dropped.
                said = json.dumps({"error": {"message": exc.detail}})
                yield f"data: {said}\n\n".encode()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    return router
