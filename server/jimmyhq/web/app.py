"""The web process: the API and the built web app.

Every route under ``/api`` needs a signed-in owner or an API token, except the
few listed in ``_OPEN``, and ``/api/in/``, where a device sends a message to an app
with a key of its own (``apps.inbound``). Before that, every request is checked for the
name it was addressed to and, for changes, the site it came from.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from jimmyhq import __version__, modules, nodes
from jimmyhq import tokens as api_tokens
from jimmyhq.apps import api as app_api
from jimmyhq.apps import files as app_files
from jimmyhq.apps import inbound as app_inbound
from jimmyhq.apps import sync as apps_sync
from jimmyhq.auth import MAX_PASSWORD, MAX_TOKEN_NAME, AuthError, Principal
from jimmyhq.core import Core, open_core
from jimmyhq.settings import MAX_FILE_BYTES, SettingError, SettingsFileError
from jimmyhq.web.deps import owner, reader
from jimmyhq.web.dist import NOT_BUILT, not_built_page, web_dist
from jimmyhq.web.locality import (
    LOCAL_NAMES,
    client_key,
    host_name,
    is_forwarded,
    is_local_name,
    is_local_request,
    is_public,
    over_https,
    public_refusal,
)

__all__ = ["COOKIE", "create_app", "is_local_request", "public_refusal"]

log = logging.getLogger("jimmyhq.web")

COOKIE = "jimmyhq_session"
PROCESSES = ("web", "worker", "village")
_OPEN = {"/api/health", "/api/auth/state", "/api/auth/setup", "/api/auth/login"}
_SAFE = {"GET", "HEAD", "OPTIONS"}
_MAX_SEQ = 2**63 - 1
# JimmyHQ's own pages may frame only addresses on this server, and the only ones this
# server puts in a frame are an app's files (``FrameGuard``). So a framed app that tries
# to send itself somewhere else, a form post included, goes nowhere.
SHELL_CSP = "frame-src 'self'"


def set_session_cookie(request: Request, response: Response, token: str, days: int) -> None:
    response.set_cookie(
        COOKIE, token, max_age=days * 86400, httponly=True,
        samesite="lax", secure=over_https(request), path="/",
    )


def _text(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("contains characters that cannot be used") from None
    return value


class SetupBody(BaseModel):
    name: str = Field(max_length=80)
    password: str = Field(max_length=MAX_PASSWORD)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = _text(value).strip()
        if not value:
            raise ValueError("cannot be empty")
        return value

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return _text(value)


class LoginBody(BaseModel):
    password: str = Field(max_length=MAX_PASSWORD)


class PasswordBody(BaseModel):
    current: str = Field(max_length=MAX_PASSWORD)
    new: str = Field(max_length=MAX_PASSWORD)

    @field_validator("new")
    @classmethod
    def _new(cls, value: str) -> str:
        return _text(value)


class ValueBody(BaseModel):
    value: object


class TokenBody(BaseModel):
    label: str = Field(max_length=MAX_TOKEN_NAME)
    level: Literal["reader", "writer"]

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return _text(value)


def create_app(core: Core | None = None) -> FastAPI:
    core = core or open_core()
    closing = asyncio.Event()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        async def beat() -> None:
            while True:
                await run_in_threadpool(core.metrics.beat, "web")
                await asyncio.sleep(10)

        closing.clear()
        task = asyncio.create_task(beat())
        try:
            yield
        finally:
            closing.set()  # lets open event streams end so shutdown is not held up
            task.cancel()
            # A reply being written when the stop arrives is recorded, not lost.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(modules.shutdown(core), 4)

    app = FastAPI(title="JimmyHQ", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.core = core

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        # One readable sentence, and never an echo of what was sent.
        first = exc.errors()[0] if exc.errors() else {}
        if first.get("type") == "json_invalid":
            return JSONResponse({"detail": "The request: is not valid JSON."}, 422)
        loc = list(first.get("loc", ()))
        if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
            loc = loc[1:]  # where it was sent, not what it is called
        where = [p for p in loc if isinstance(p, str)]
        message = str(first.get("msg", "is not valid")).removeprefix("Value error, ")
        field = where[-1] if where else "The request"
        return JSONResponse({"detail": f"{field}: {message}."}, 422)

    # --- who is asking --------------------------------------------------------

    def allowed_host(request: Request) -> bool:
        """Whether this server answers to the name the request was addressed to.

        The machine's own names count only for a request that nothing relayed. A tunnel
        on this computer, such as Tailscale Serve, passes on whatever Host the client
        wrote, so through it ``localhost`` would otherwise open the door to anyone who
        can reach the tunnel. A relayed request must use a name the owner added, and
        never one of this computer's own names, even one the owner listed."""
        name = host_name(request.headers.get("host"))
        if not is_forwarded(request):
            if name in LOCAL_NAMES or name == core.config.host.lower():
                return True
        elif is_local_name(name, core.config.host):
            return False
        if name in core.config.allowed_hosts:
            return True
        return name in {h.strip().lower() for h in core.settings.get("access.allowed_hosts")}

    def wrong_host(request: Request) -> str:
        """What a refused name is told. A relayed request at this computer's own name
        was not fixed by adding the name: say what happened instead."""
        name = host_name(request.headers.get("host"))
        if is_forwarded(request) and is_local_name(name, core.config.host):
            return (f"JimmyHQ does not answer this request: it was addressed to {name}, this "
                    "computer's own name, but it carries a forwarding header, so something "
                    "relayed it here: a proxy or tunnel, or a browser extension that adds "
                    "X-Forwarded-For. Nothing was done. Adding the name to the allowed "
                    "hosts will not change this. On this computer, open JimmyHQ directly "
                    "with the extension or proxy switched off; from another device, use the "
                    "name you added under Settings, Access.")
        return ("JimmyHQ does not answer to this address. Add the name under Settings, "
                "Access, or to allowed_hosts in config.toml.")

    listed = core.settings.stored("access.allowed_hosts")
    listed = listed if isinstance(listed, list) else []
    ignored = [h for h in (*core.config.allowed_hosts, *listed)
               if isinstance(h, str) and is_local_name(h, core.config.host)]
    if ignored:
        log.warning(
            "The allowed hosts list %s, this computer's own name. It is ignored there: "
            "this computer's own names always work from this computer and never through a "
            "tunnel. Remove it under Settings, Access, or from allowed_hosts in config.toml.",
            ", ".join(sorted(set(ignored))))

    def identify(request: Request) -> Principal | None:
        days = core.settings.get("access.session_days")
        if core.auth.session_valid(request.cookies.get(COOKIE), days):
            return Principal("owner", frozenset())
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            # A running app's token works on the app API alone, and only from this
            # machine; no other token works there.
            if request.url.path.startswith(app_api.PREFIX):
                if not app_api.from_this_machine(core, client_address(request)):
                    return None
                return core.auth.check_app_token(token)
            return core.auth.check_token(token)
        return None

    @app.middleware("http")
    async def gate(request: Request, call_next):
        # Not from the public internet, unless the owner said so in config.toml.
        if is_public(request) and not core.config.allow_public:
            return JSONResponse({"detail": public_refusal(core.config.port)}, 403)
        # Only answer to names this server is known by. Without this, another
        # site could point its own name at this machine and be treated as local.
        if not await run_in_threadpool(allowed_host, request):
            return JSONResponse({"detail": wrong_host(request)}, 421)
        path = request.url.path
        if path.startswith("/api/") and apps_sync.due_now(core):
            # An app installed, changed or removed by another process (the command
            # line, the worker) is in step here within apps_sync.STEP_S.
            await run_in_threadpool(apps_sync.keep_in_step, core)
        # Messages from the owner's devices carry a key of their own and take no other
        # credential: no cookie is looked at, and the route answers every refusal itself.
        from_device = path.startswith(app_inbound.PREFIX)
        if path.startswith("/api/") and not from_device:
            # A browser on another site must not be able to act with the owner's cookie.
            origin = request.headers.get("origin")
            if request.method not in _SAFE and origin:
                if urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower():
                    return JSONResponse({"detail": "Request came from another site."}, 403)
            # The node link has credentials of its own and takes no others.
            link = await run_in_threadpool(
                nodes.admit, core, path, request.headers.get("authorization"),
                client_address(request)) if path.startswith(nodes.PREFIX) else None
            if link is not None:
                if link.refusal is not None:
                    return JSONResponse({"detail": link.refusal[1]}, link.refusal[0])
                if not link.open:
                    request.state.principal = link.principal
                    request.state.node = link.node
            elif path not in _OPEN:
                principal = await run_in_threadpool(identify, request)
                if principal is None:
                    return JSONResponse({"detail": "Sign in to continue."}, 401)
                request.state.principal = principal
        response = await call_next(request)
        session = None if from_device else request.cookies.get(COOKIE)
        if session and response.status_code < 400:
            days = core.settings.get("access.session_days")
            if await run_in_threadpool(core.auth.renew_session, session, days):
                set_session_cookie(request, response, session, days)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if not path.startswith(app_files.PREFIX):  # an app's files carry their own policy
            response.headers.setdefault("Content-Security-Policy", SHELL_CSP)
        return response

    # Outermost, so it is answered before anything else looks at the request.
    app.add_middleware(app_files.FrameGuard)

    def start_session(request: Request, response: Response) -> None:
        days = core.settings.get("access.session_days")
        set_session_cookie(request, response, core.auth.create_session(days), days)

    def client_address(request: Request) -> str:
        """Who a request is counted under: see ``locality.client_key``. A request relayed
        by a tunnel on this computer is never counted as this computer."""
        return client_key(request, core.config.host)

    # --- health and access ----------------------------------------------------

    api = APIRouter(prefix="/api")

    @api.api_route("/health", methods=["GET", "HEAD"])  # HEAD for uptime checks
    def health():
        return {"ok": True, "version": __version__}

    @api.get("/auth/state")
    def auth_state(request: Request):
        exists = core.auth.owner_exists()
        signed_in = exists and core.auth.session_valid(
            request.cookies.get(COOKIE), core.settings.get("access.session_days"))
        return {
            "setup_needed": not exists,
            "signed_in": signed_in,
            "owner_name": core.settings.get("owner.name") if signed_in else None,
        }

    @api.post("/auth/setup")
    def auth_setup(body: SetupBody, request: Request, response: Response):
        if core.auth.owner_exists():
            raise HTTPException(409, "An owner already exists for this install.")
        # Whoever creates the owner chooses the password, so by default that
        # must happen on the machine JimmyHQ runs on.
        if not core.config.remote_setup and not is_local_request(request):
            raise HTTPException(
                403,
                "Create the owner account from the computer JimmyHQ runs on, at "
                "http://127.0.0.1 or http://localhost. To allow it from another machine or "
                "through a proxy, set remote_setup = true in config.toml and restart.",
            )
        try:
            core.auth.create_owner(body.password)
        except AuthError as exc:
            raise HTTPException(409 if core.auth.owner_exists() else 422, str(exc)) from exc
        core.tape.print("owner.created", {}, actor="owner")
        core.settings.set("owner.name", body.name, actor="owner")
        start_session(request, response)
        return {"ok": True}

    @api.post("/auth/login")
    def auth_login(body: LoginBody, request: Request, response: Response):
        outcome = core.auth.sign_in(body.password, client_address(request))
        if outcome == "locked":
            raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
        if outcome != "ok":
            raise HTTPException(401, "That password is not right.")
        start_session(request, response)
        return {"ok": True}

    @api.post("/auth/logout")
    def auth_logout(request: Request, response: Response):
        core.auth.end_session(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.post("/auth/password")
    def auth_password(body: PasswordBody, request: Request, response: Response,
                      _: Principal = Depends(owner)):  # noqa: B008
        """Change the owner's password. Every session ends, this browser's too, and this
        browser is handed a fresh one in the same step, so it stays signed in while the
        token it held stops working. A wrong current password counts as a failed
        sign-in."""
        days = core.settings.get("access.session_days")
        try:
            outcome, ended, fresh = core.auth.change_password(
                body.current, body.new, client_address(request),
                request.cookies.get(COOKIE), days)
        except AuthError as exc:
            raise HTTPException(422, str(exc)) from exc
        if outcome == "locked":
            raise HTTPException(429, "Too many wrong passwords. Nothing was changed. "
                                     "Try again in 15 minutes.")
        if outcome != "ok":
            # Not 401: the session is still good, and 401 means "sign in again".
            raise HTTPException(403, "That is not your current password. Nothing was "
                                     "changed. Check it and try again.")
        if fresh:
            set_session_cookie(request, response, fresh, days)
        core.tape.print("owner.password_changed",
                        {"via": "settings", "sessions_ended": ended}, actor="owner")
        return {"ok": True, "sessions_ended": ended}

    @api.post("/auth/sessions/end-others")
    def auth_end_other_sessions(request: Request, _: Principal = Depends(owner)):  # noqa: B008
        """Sign out every browser but the one that asks."""
        ended = core.auth.end_other_sessions(request.cookies.get(COOKIE))
        core.tape.print("owner.sessions_ended", {"sessions_ended": ended}, actor="owner")
        return {"ok": True, "sessions_ended": ended}

    # --- API tokens -----------------------------------------------------------
    #
    # Only the signed-in owner, never a token: a token cannot see, make or end tokens.

    @api.get("/auth/tokens")
    def tokens_list(_: Principal = Depends(owner)):  # noqa: B008
        return {"tokens": api_tokens.listed(core)}

    @api.post("/auth/tokens")
    def tokens_create(body: TokenBody, who: Principal = Depends(owner)):  # noqa: B008
        try:
            made, secret = api_tokens.create(core, body.label, body.level, actor=who.actor)
        except AuthError as exc:
            raise HTTPException(422, str(exc)) from exc
        # The only time the token itself is ever sent anywhere. It is not kept.
        return {"token": made, "secret": secret}

    @api.post("/auth/tokens/{token_id}/revoke")
    def tokens_revoke(token_id: str, who: Principal = Depends(owner)):  # noqa: B008
        try:
            ended = api_tokens.revoke(core, token_id, actor=who.actor)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"token": ended}

    # --- settings -------------------------------------------------------------

    @api.get("/settings")
    def settings_all(_: Principal = Depends(reader)):  # noqa: B008
        return core.settings.describe()

    @api.get("/settings/export")
    def settings_export(
        portable: bool = Query(False), changed: bool = Query(False),
        name: str = Query("", max_length=120), description: str = Query("", max_length=2000),
        _: Principal = Depends(reader),  # noqa: B008
    ):
        document = core.settings.export(
            portable_only=portable, changed_only=changed, name=name.strip(),
            description=description.strip())
        stamp = time.strftime("%Y-%m-%d", time.gmtime())
        filename = "jimmyhq-profile.json" if portable else f"jimmyhq-settings-{stamp}.json"
        return JSONResponse(
            document,
            headers={"content-disposition": f'attachment; filename="{filename}"'},
        )

    async def settings_document(request: Request):
        """The JSON document in a request body, refused if it is too big or not JSON."""
        declared_size = request.headers.get("content-length", "")
        too_big = HTTPException(
            413,
            f"A settings file cannot be larger than {MAX_FILE_BYTES // 1024} KB. "
            "Check that it is the right file.",
        )
        if declared_size.isdigit() and int(declared_size) > MAX_FILE_BYTES:
            raise too_big
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise too_big
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, RecursionError):
            raise HTTPException(
                422, "That is not valid JSON. Check the file for a missing comma or quote."
            ) from None

    @api.post("/settings/plan")
    async def settings_plan(request: Request, _: Principal = Depends(reader)):  # noqa: B008
        document = await settings_document(request)
        try:
            return (await run_in_threadpool(core.settings.plan, document)).to_dict()
        except SettingsFileError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.post("/settings/import")
    async def settings_import(
        request: Request, skip_invalid: bool = Query(False),
        who: Principal = Depends(owner),  # noqa: B008
    ):
        document = await settings_document(request)
        try:
            plan = await run_in_threadpool(
                lambda: core.settings.apply(document, actor=who.actor, skip_invalid=skip_invalid))
        except SettingsFileError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not plan.applied:
            n = len(plan.refused)
            raise HTTPException(
                422,
                f"Nothing was changed: {n} {'entry' if n == 1 else 'entries'} cannot be "
                "applied. Fix them, or import again and skip the invalid entries.",
            )
        return plan.to_dict()

    @api.put("/settings/{key}")
    def settings_set(key: str, body: ValueBody, who: Principal = Depends(owner)):  # noqa: B008
        try:
            return {"key": key, "value": core.settings.set(key, body.value, actor=who.actor)}
        except SettingError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.delete("/settings/{key}")
    def settings_reset(key: str, who: Principal = Depends(owner)):  # noqa: B008
        try:
            return {"key": key, "value": core.settings.reset(key, actor=who.actor)}
        except SettingError as exc:
            raise HTTPException(422, str(exc)) from exc

    # --- the Tape -------------------------------------------------------------

    @api.get("/tape")
    def tape_latest(
        limit: int = Query(50, ge=1, le=500),
        before: int | None = Query(None, ge=0, le=_MAX_SEQ),
        kind: str | None = Query(None, max_length=200),
        _: Principal = Depends(reader),  # noqa: B008
    ):
        events = core.tape.latest(limit=limit, before=before, kind=kind)
        return {"events": [e.model_dump() for e in events]}

    @api.get("/tape/stats")
    def tape_stats(_: Principal = Depends(reader)):  # noqa: B008
        return core.tape.stats()

    @api.get("/feed")
    async def feed(request: Request, _: Principal = Depends(reader)):  # noqa: B008
        def still_allowed() -> bool:
            who = identify(request)
            return who is not None and who.can("read")

        def stopping() -> bool:
            # The server's own flag is set the moment a stop is asked for, well
            # before the lifespan ends, so an open stream never holds up a stop.
            server = getattr(app.state, "server", None)
            return closing.is_set() or bool(getattr(server, "should_exit", False))

        async def stream():
            # SQLite calls run off the event loop: a writer waiting on another
            # process must not freeze every other request.
            last = await run_in_threadpool(core.tape.last_seq)
            idle = 0
            yield ": connected\n\n"
            while not stopping() and not await request.is_disconnected():
                # Checked on every pass, so signing out or revoking a token
                # ends a stream that is already open.
                if not await run_in_threadpool(still_allowed):
                    break
                events = await run_in_threadpool(core.tape.after, last, limit=200)
                for event in events:
                    last = event.seq or last
                    yield f"event: tape\ndata: {json.dumps(event.model_dump())}\n\n"
                idle = 0 if events else idle + 1
                if idle >= 15:
                    idle = 0
                    yield ": keep-alive\n\n"
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(closing.wait(), 1)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api.get("/status")
    def status(_: Principal = Depends(reader)):  # noqa: B008
        return {"version": __version__, "processes": core.metrics.processes(PROCESSES)}

    app.include_router(api)
    for router in modules.routers(core):
        app.include_router(router)

    @app.api_route("/api/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"])
    def api_not_found(rest: str):
        raise HTTPException(404, "There is nothing at this address.")

    # --- the built web app ----------------------------------------------------

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str, request: Request):
        # Looked for at each request, so a build made after the start is picked up.
        dist = web_dist()
        if dist is None:
            if "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(not_built_page(), 503, headers={"Cache-Control": "no-store"})
            return JSONResponse({"detail": NOT_BUILT}, 503)
        try:
            target = (dist / path).resolve()
            if path and target.is_file() and target.is_relative_to(dist):
                return FileResponse(target)
        except (ValueError, OSError):
            pass  # a path the file system cannot represent is just "not a file"
        if "." in path.rsplit("/", 1)[-1]:
            # A missing asset is a 404; only page addresses fall back to the app.
            raise HTTPException(404, "There is nothing at this address.")
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
