"""The routes for messages from the owner's devices (``inbound``).

From a device, with a key and no session (the web app's gate lets ``/api/in/`` through to
here and nowhere else, and honours no cookie on it)::

    POST /api/in/<app>/<name>   Authorization: Bearer <key>, Content-Type: application/json
    POST /api/in/<app>/<name>/k/<key>    the same, for a key the owner made for an address
         202 {"ok": true}                        queued for the app
         401 one body for every refusal of who  (``inbound.REFUSED``)
         403 an Origin header: this is not for web pages
         415 not JSON · 413 too large · 400 not a JSON object (or list, where declared),
             or more of the sender's own ?parameters than the owner allows
         429 too many, with Retry-After

Whatever the sender adds after ``?`` is passed to the app as ``query``.

For the signed-in owner, on the app's page::

    GET    /api/apps/<id>/inbound               the names, the switch, the keys, the counts
    POST   /api/apps/<id>/inbound/keys          {label, in_address?} -> {inbound, key,
                                                  paths?}   the key (and addresses), once
    DELETE /api/apps/<id>/inbound/keys/<key>    -> inbound
    PUT    /api/apps/<id>/inbound/open          {open} -> inbound
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool

from jimmyhq.apps import inbound
from jimmyhq.apps.bridge import BridgeError
from jimmyhq.apps.store import AppError
from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.door.protocol import bearer, read_body
from jimmyhq.web.deps import owner
from jimmyhq.web.locality import client_key

REFUSED = ("Nothing was taken. Check the address and the key, and that the app's owner has "
           "opened it to messages from devices.")
FROM_A_PAGE = ("Nothing was taken. This address is for devices and services, not for web "
               "pages: send without an Origin header.")
NOT_JSON_TYPE = "Nothing was taken. Send the message as Content-Type: application/json."
TOO_LARGE = "Nothing was taken. A message can be at most {kb} KB. Send less."
NOT_JSON = "Nothing was taken. The message is not valid JSON."
NOT_OBJECT = "Nothing was taken. Send one JSON object, such as {\"state\": \"on\"}."
NOT_OBJECT_OR_LIST = ("Nothing was taken. Send one JSON object or one JSON list, such as "
                      "[{\"state\": \"on\"}].")
BAD_QUERY = ("Nothing was taken. The address carries more than {most} parameters after \"?\", "
             "or one longer than {chars} characters. Send fewer or shorter ones, or ask the "
             "owner to allow more under Settings, Apps.")
SLOW_DOWN = "Nothing was taken. You are sending faster than this key may. Wait, then send again."
FULL_UP = ("Nothing was taken. The app has too many messages waiting for it. Wait, then send "
           "again.")
TURNED_AWAY = ("Nothing was taken. Too many refused messages came from your address. Wait, "
               "then send again with the right key.")
ONLY_POST = "Nothing was taken. Send a message with POST."
# no-referrer: an address may hold a key (``inbound_address_key_never_kept``).
_BASE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
         "Referrer-Policy": "no-referrer"}


def answer(status: int, body: dict[str, Any], headers: dict[str, str] | None = None
           ) -> Response:
    """Every answer the device door gives: compact JSON, never cached, nothing else."""
    return Response(json.dumps(body, separators=(",", ":")), status_code=status,
                    media_type="application/json", headers={**_BASE, **(headers or {})})


def refusal(status: int, sentence: str, headers: dict[str, str] | None = None) -> Response:
    return answer(status, {"ok": False, "error": sentence}, headers)


def unauthorized() -> Response:
    """Rule ``inbound_one_refusal``: one answer, byte for byte, for every refusal of who."""
    return refusal(401, REFUSED, {"WWW-Authenticate": "Bearer"})


def turned_away(wait: float) -> Response:
    """Rule ``inbound_paced``: an address that kept getting it wrong, without a right key."""
    return refusal(429, TURNED_AWAY, {"Retry-After": str(int(wait) + 1)})


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def parse(raw: bytes) -> Any:
    """The body as JSON, refusing NaN and Infinity, which JSON does not have, and what
    cannot be passed on as JSON text: a number too large for a float (``1e400``) and a
    lone surrogate (``"\\ud800"``). Each raises ValueError."""
    value = json.loads(raw, parse_constant=_no_constant)
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return value


async def receive(core: Core, request: Request, app_id: str, name: str,  # noqa: C901, PLR0911
                  in_address: str | None = None) -> Response:
    """One message from a device, from the first header to the answer. ``in_address``: the
    key the address carried, on the address route; then only that key counts."""
    if request.headers.get("origin") is not None:
        return refusal(403, FROM_A_PAGE)
    svc = inbound.service(core)
    # Who to count a refusal under. A request relayed by a tunnel on this computer is
    # counted apart from this computer's own (``locality.client_key``), so knocking through
    # the tunnel never turns away a device on the machine itself. A right key always passes.
    source = client_key(request, core.config.host)
    key = bearer(request.headers.get("authorization"))
    if in_address is not None:  # the address route: the key in it and no other
        key = in_address
    ceiling = int(await run_in_threadpool(core.settings.get,
                                          "apps.inbound_bad_all_per_minute"))
    if key is None and (wait := svc.knocks.blocked(source, svc.clock(), ceiling)):
        return turned_away(wait)  # no key at all: nothing to look up
    who = await run_in_threadpool(partial(inbound.admit, core, app_id, name, key,
                                          from_address=in_address is not None))
    if who is None:
        if wait := svc.knocks.blocked(source, svc.clock(), ceiling):
            return turned_away(wait)
        most = int(await run_in_threadpool(core.settings.get, "apps.inbound_bad_per_minute"))
        block = int(await run_in_threadpool(core.settings.get, "apps.inbound_block_s"))
        svc.knocks.record(source, most, block, svc.clock())
        return unauthorized()

    declared = request.headers.get("content-length", "")
    size = int(declared) if declared.isdigit() else 0
    per_minute = int(await run_in_threadpool(core.settings.get, "apps.inbound_per_minute"))
    if not svc.pace.allow(who.key_id, per_minute, svc.clock()):
        await run_in_threadpool(inbound.receipt, core, who, "too_many", size)
        return refusal(429, SLOW_DOWN, {"Retry-After": str(inbound.WINDOW_S)})
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind != "application/json":
        await run_in_threadpool(inbound.receipt, core, who, "wrong_type", size)
        return refusal(415, NOT_JSON_TYPE)
    most = int(await run_in_threadpool(core.settings.get, "apps.inbound_query_max"))
    chars = int(await run_in_threadpool(core.settings.get, "apps.inbound_query_chars"))
    query = inbound.sender_query(request.query_params.multi_items(), most, chars)
    if query is None:
        await run_in_threadpool(inbound.receipt, core, who, "bad_query", size)
        return refusal(400, BAD_QUERY.format(most=most, chars=chars))
    kb = await run_in_threadpool(inbound.largest_kb, core, who)
    raw = await read_body(request, kb * 1024)  # a declared size too large is not read
    if raw is None:
        await run_in_threadpool(inbound.receipt, core, who, "too_large", size)
        return refusal(413, TOO_LARGE.format(kb=kb))
    try:
        message = parse(raw)
    except (ValueError, RecursionError):
        await run_in_threadpool(inbound.receipt, core, who, "not_json", len(raw))
        return refusal(400, NOT_JSON)
    if not (isinstance(message, dict) or (who.array and isinstance(message, list))):
        await run_in_threadpool(inbound.receipt, core, who, "not_object", len(raw))
        return refusal(400, NOT_OBJECT_OR_LIST if who.array else NOT_OBJECT)
    try:
        queued = await run_in_threadpool(inbound.queue, core, who, message, len(raw), query)
    except BridgeError:
        await run_in_threadpool(inbound.receipt, core, who, "too_large", len(raw))
        return refusal(413, TOO_LARGE.format(kb=kb))
    if queued is None:
        return refusal(429, FULL_UP, {"Retry-After": str(inbound.WINDOW_S)})
    return answer(202, {"ok": True})  # nothing the app does ever comes back this way


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewKey(_Body):
    label: str = Field(max_length=200)
    in_address: StrictBool = False  # for a sender that can only be given an address


class OpenBody(_Body):
    open: bool


def build_router(core: Core) -> APIRouter:
    router = APIRouter(tags=["apps"])

    @router.post(inbound.PREFIX + "{app_id}/{name}", include_in_schema=False)
    async def from_a_device(app_id: str, name: str, request: Request) -> Response:
        return await receive(core, request, app_id, name)

    @router.post(inbound.PREFIX + "{app_id}/{name}/" + inbound.ADDRESS_MARK + "/{key}",
                 include_in_schema=False)
    async def from_an_address(app_id: str, name: str, key: str, request: Request) -> Response:
        return await receive(core, request, app_id, name, in_address=key)

    @router.api_route(inbound.PREFIX + "{app_id}/{name}", include_in_schema=False,
                      methods=["GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"])
    @router.api_route(inbound.PREFIX + "{app_id}/{name}/" + inbound.ADDRESS_MARK + "/{key}",
                      include_in_schema=False,
                      methods=["GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"])
    async def not_post(app_id: str, name: str) -> Response:
        # Only POST, and no answer to a browser's preflight: this is not for web pages.
        return refusal(405, ONLY_POST, {"Allow": "POST"})

    def run(fn: Any, *args: Any, **kw: Any) -> Any:
        try:
            return fn(*args, **kw)
        except AppError as error:
            raise HTTPException(error.status, error.detail) from None

    @router.get("/api/apps/{app_id}/inbound")
    def read(app_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return run(inbound.state, core, app_id)

    @router.post("/api/apps/{app_id}/inbound/keys")
    def make(app_id: str, body: NewKey, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        now, key = run(inbound.make_key, core, app_id, body.label, actor=who.actor,
                       in_address=body.in_address)
        # The only time the key is ever sent anywhere. It is not kept.
        if not body.in_address:
            return {"inbound": now, "key": key}
        names = inbound.declared(core, app_id)
        return {"inbound": now, "key": key, "paths": inbound.address_paths(app_id, names, key)}

    @router.delete("/api/apps/{app_id}/inbound/keys/{key_id}")
    def remove(app_id: str, key_id: str, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return run(inbound.remove_key, core, app_id, key_id, actor=who.actor)

    @router.put("/api/apps/{app_id}/inbound/open")
    def switch(app_id: str, body: OpenBody, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return run(inbound.set_open, core, app_id, body.open, actor=who.actor)

    return router
