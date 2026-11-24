"""The app API: how a full app's program talks to JimmyHQ. Local HTTP, never import.

Every route is under ``/api/app/v1/`` and takes one credential: the app token the
supervisor handed the program when it started it (``runtime``). The gate in ``web.app``
accepts that token here and nowhere else, only from this machine, and no other
credential here: not the owner's session, not an API token. The token says which app is
asking; nothing the app sends can say otherwise.

On every request, before anything else: the app is installed, switched on and full, and
the call is within its rates and what *the owner granted* (the record of consent, read
afresh each time, so switching an app off or removing it takes effect at once even
before its token is revoked).

    GET  /api/app/v1/hello             who it is, what it was granted, its settings,
                                       the owner's time zone and week start
    POST /api/app/v1/call/<call>       {args}  one call, as below
    PUT  /api/app/v1/kinds             {kinds: [{name, v, schema, example}]}
    PUT  /api/app/v1/plugs             {jobs, decisions, calls, tools, sources, inbound?}
    GET  /api/app/v1/inbox?wait=25     {deliveries: [{id, type, data, offers}]}
    POST /api/app/v1/inbox/<id>        {ok: true, result} | {ok: false, error}

Every answer is HTTP 200 and the bridge's envelope, ``{ok: true, result}`` or
``{ok: false, error: {code, message, retry_after_s?}}``: a refusal is an answer, not a
transport failure. Only a missing or wrong credential is an HTTP error (401 or 403).

**Calls.** Every bridge call a page can make (``bridge.CALLS``) except ``app.call``, plus
the ones only a program may make: ``tape.print``, ``tape.read``, ``secrets.get`` and
``vault.put``. A plug point adds its own with ``add_call``: ``ui.count`` (``counts``) is
one, the number beside the app in the sidebar.

**Plugs.** ``PUT /plugs`` says what the program offers: jobs, the card kinds whose
decisions it wants, the calls its page may make, its agent tools and memory sources.
Each is checked against the manifest and the grant and kept (``inbox.plugs``). The plug
points (package J) act on them; until then they are only kept.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import ipaddress
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from jimmyhq.apps import bridge, inbox, schemas
from jimmyhq.apps.bridge import BridgeError, Call, Ctx, Installed, only
from jimmyhq.apps.manifest import APP_API
from jimmyhq.apps.permissions import FULL, allows
from jimmyhq.auth import APP_SCOPE, Principal
from jimmyhq.bookmark import within_tape
from jimmyhq.tape import registry
from jimmyhq.web.deps import principal

if TYPE_CHECKING:
    from jimmyhq.core import Core

PREFIX = "/api/app/v1/"
API_VERSION = f"{APP_API[0]}.{APP_API[1]}"
PAGE_ONLY = ("app.call",)  # a program does not call itself through its own page's door
READ_MOST = 500
_SCAN_PAGES = 10  # tape.read looks at most this many pages for an app's own events
_ENVELOPE_ROOM = 16 * 1024
_EVENT_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}\Z")
_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")
_KIND = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\Z")
_PLUG_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}\Z")
_CALL_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}\Z")
_MAX_JOB_S = 7 * 24 * 60 * 60
_MIN_JOB_S = 5  # the worker's clock ticks every five seconds


# --- who may use it --------------------------------------------------------------------------


def from_this_machine(core: Core, client: str) -> bool:
    """An app token is only taken from this computer: a loopback address, or the one
    address the server is bound to when that is not every address. ``client`` is what
    ``web.locality.client_key`` says, so a request relayed by a tunnel on this computer
    (``forwarded:...``) is not an address and is refused."""
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if (mapped or address).is_loopback:
        return True
    bound = core.config.host
    return bound not in ("0.0.0.0", "::", "") and client == bound  # noqa: S104


def app_of(who: Principal) -> str:
    """The app id an app principal stands for, or a refusal for anyone else."""
    if not who.actor.startswith("app:") or APP_SCOPE not in who.scopes:
        raise HTTPException(403, "This address is for a full app's own program, with the "
                                 "token JimmyHQ gave it when it started it. Nothing was "
                                 "done. Use /api/ with your own credential instead.")
    return who.actor.removeprefix("app:")


def check_full(app: Installed) -> None:
    """Only a full app has a program, so only a full app talks here."""
    if app.manifest.tier != FULL:
        raise BridgeError("not_allowed", f"\"{app.manifest.id}\" is a sandboxed app, and only "
                                         "a full app's program uses this API. Nothing was "
                                         "done. Use the bridge from its page.")


def admit_app(core: Core, app_id: str) -> Installed:
    """The asking app, if it is installed, switched on and full."""
    app = bridge.check_installed(core, bridge.check_app_id(app_id))
    check_full(app)
    return app


# --- the calls only a program makes ----------------------------------------------------------


def own_subject(ctx: Ctx, subject: Any) -> str | None:
    """An event's subject, kept inside the app's own names: ``app.<id>:<what was sent>``."""
    if subject is None:
        return None
    if not isinstance(subject, str) or not _SUBJECT.match(subject):
        raise BridgeError("invalid", "\"subject\" must be a short name of letters, digits, "
                                     "dots, colons and hyphens, such as \"post:12\". Nothing "
                                     "was written.")
    return f"app.{ctx.id}:{subject}"


def check_not_private(ctx: Ctx, what: str) -> None:
    """A private app keeps what it knows off the record."""
    if ctx.app.manifest.private:
        raise BridgeError("not_allowed", f"Your app is marked private, so it cannot {what}. "
                                         "Nothing was written. Keep it in app.private_db() "
                                         "instead.")


def tape_print(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    only("tape.print", args, ("kind", "payload", "subject", "cause"), ("kind", "payload"),
         note="Who wrote it is set by JimmyHQ. ")
    check_not_private(ctx, "write on the owner's record")
    try:
        name = schemas.full_kind_name(ctx.app.manifest, args["kind"])
    except schemas.KindRefused as exc:
        raise BridgeError("invalid", str(exc).replace("kept", "written")) from None
    if not registry.is_schema_backed(name):
        schemas.load_all(ctx.core)  # declared through another process
    if not registry.is_schema_backed(name):
        raise BridgeError("not_found", f"Your app has not declared the kind \"{name}\", so "
                                       "nothing was written. Declare it first with PUT "
                                       "/api/app/v1/kinds (the SDK's @app.kind does).")
    cause = args.get("cause")
    if cause is not None and (not isinstance(cause, str) or not _EVENT_ID.match(cause)):
        raise BridgeError("invalid", "\"cause\" must be the id of an event on the record. "
                                     "Nothing was written.")
    if not isinstance(args["payload"], dict):
        raise BridgeError("invalid", "\"payload\" must be an object. Nothing was written.")
    try:
        event = ctx.core.tape.print(name, args["payload"], actor=ctx.actor, cause=cause,
                                    subject=own_subject(ctx, args.get("subject")))
    except ValueError as exc:
        raise BridgeError("invalid", f"{exc} Nothing was written.") from None
    return {"id": event.id, "seq": event.seq, "kind": name, "v": event.v}


def _event(event: Any) -> dict[str, Any]:
    return {"seq": event.seq, "id": event.id, "ts": event.ts, "kind": event.kind,
            "v": event.v, "actor": event.actor, "cause": event.cause, "root": event.root,
            "subject": event.subject, "payload": event.payload, "readable": event.readable,
            "erased": event.erased}


def read_scope(ctx: Ctx) -> Callable[[Any], bool]:
    """Which events an app may read: all of them with ``tape.read``, otherwise only the
    ones it wrote itself."""
    if allows(ctx.app.granted, "tape.read"):
        return lambda _event: True
    return lambda event: event.actor == ctx.actor


def tape_read(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    only("tape.read", args, ("after", "kinds", "limit"),
         note="Without \"tape.read\" an app reads only what it wrote. ")
    after = args.get("after", 0)
    limit = args.get("limit", 100)
    kinds = args.get("kinds")
    for value, name in ((after, "after"), (limit, "limit")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BridgeError("invalid", f"\"{name}\" for tape.read must be a whole number. "
                                         "Nothing was read.")
    if not 1 <= limit <= READ_MOST:
        raise BridgeError("invalid", f"\"limit\" for tape.read is from 1 to {READ_MOST}. "
                                     "Nothing was read.")
    if kinds is not None and (not isinstance(kinds, list) or not 1 <= len(kinds) <= 50
                              or not all(isinstance(k, str) and _KIND.match(k) for k in kinds)):
        raise BridgeError("invalid", "\"kinds\" for tape.read is a list of up to 50 full kind "
                                     "names, such as \"app.drafts.drafted\". Nothing was read.")
    schemas.load_all(ctx.core)
    keep = read_scope(ctx)
    found: list[dict[str, Any]] = []
    # An app that kept a place past the Tape's end (the Tape was put back from an older
    # backup) is told the end as its next place.
    cursor = within_tape(ctx.core.tape, after, "an app reading the Tape")
    for _ in range(_SCAN_PAGES):
        page = ctx.core.tape.after(cursor, limit=limit, kinds=tuple(kinds) if kinds else None)
        for event in page:
            cursor = event.seq
            if keep(event):
                found.append(_event(event))
                if len(found) >= limit:
                    return {"events": found, "next": cursor}
        if len(page) < limit:
            break
    return {"events": found, "next": cursor}


def secrets_get(ctx: Ctx, args: dict[str, Any]) -> str | None:
    """One of the app's own secrets. A secret it keeps for each host the owner gave it
    (``"for_each": "owner_host"``) is read with ``host``, for a host it holds now, and only
    the one kept for that host."""
    only("secrets.get", args, ("name", "host"), ("name",))
    manifest = ctx.app.manifest
    secret = manifest.secret(args["name"]) if isinstance(args["name"], str) else None
    if secret is None:
        declared = [s.name for s in manifest.secrets]
        listed = ", ".join(declared) or "none: add \"secrets\" to jimmyhq.app.json"
        raise BridgeError("not_found", "Your app declares no secret of that name, and it can "
                                       f"read only its own. Nothing was read. Its secrets "
                                       f"are: {listed}.")
    host = args.get("host")
    if not secret.per_host:
        if host is not None:
            raise BridgeError("invalid", f"The secret \"{secret.name}\" is one for the whole "
                                         "app, not one for each host. Nothing was read. Leave "
                                         "out \"host\".")
        return ctx.core.secrets.get(manifest.secret_name(secret.name))
    if host is None:
        raise BridgeError("invalid", f"The secret \"{secret.name}\" is kept for each host the "
                                     "owner gave your app. Nothing was read. Say which with "
                                     "\"host\", one that hosts.list returns.")
    if not isinstance(host, str) or host not in ctx.app.owner_hosts:
        raise BridgeError("not_found", "The owner has not given your app that host, so it "
                                       "has no secret there. Nothing was read. hosts.list "
                                       "says which it has.")
    return ctx.core.secrets.get(manifest.secret_name(secret.name, host))


def vault_put(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    only("vault.put", args, ("base64",), ("base64",))
    check_not_private(ctx, "put files in the owner's Vault")
    most = ctx.core.settings.get("apps.call_max_kb") * 1024
    text = args["base64"]
    if not isinstance(text, str) or len(text) > most * 4 // 3 + 4:
        raise BridgeError("too_large", f"A file put in the vault through the app API can be "
                                       f"at most {most // 1024} KB. Nothing was kept.")
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise BridgeError("invalid", "\"base64\" for vault.put must be base64 text. Nothing "
                                     "was kept.") from None
    return {"ref": ctx.core.vault.put(data), "size": len(data)}


API_CALLS: dict[str, Call] = {c.name: c for c in (
    Call("tape.print", tape_print, tiers=(FULL,)),
    Call("tape.read", tape_read, tiers=(FULL,)),
    Call("secrets.get", secrets_get, tiers=(FULL,)),
    Call("vault.put", vault_put, tiers=(FULL,)),
)}


def add_call(call: Call) -> Call:
    """For a plug point: one more call a full app's program may make. A name is never
    taken twice, and never one a page already has."""
    if call.name in API_CALLS or call.name in bridge.CALLS:
        raise ValueError(f"the call {call.name!r} exists already")
    API_CALLS[call.name] = call
    return call


def check_call(name: Any) -> Call:
    """The call of that name for a program, or a refusal that lists the ones there are."""
    if name in PAGE_ONLY:
        raise BridgeError("invalid", f"{name} is how your app's page reaches your program; "
                                     "the program itself does not use it. Nothing was done.")
    if isinstance(name, str) and name in API_CALLS:
        return API_CALLS[name]
    try:
        return bridge.check_call(name)
    except BridgeError:
        listed = sorted(n for n in (*bridge.CALLS, *API_CALLS) if n not in PAGE_ONLY)
        raise BridgeError("invalid", "There is no app API call by that name. Nothing was "
                                     f"done. The calls are: {', '.join(listed)}.") from None


def admit(core: Core, app_id: str, name: Any) -> tuple[Installed, Call]:
    """Every check before a program's call is carried out, in order."""
    call = check_call(name)
    app = admit_app(core, app_id)
    bridge.check_rates(core, app_id)
    bridge.check_permission(app, call)
    # --- roles: a record tool reaches nothing outside the app (record_effect.py) ---
    from jimmyhq.apps import record_effect  # noqa: PLC0415

    record_effect.check_outside_call(core, app_id, call.name)
    return app, call


async def dispatch(core: Core, app_id: str, name: Any, args: Any) -> Any:
    """Carry out one call for the app a token stands for, or raise ``BridgeError``."""
    try:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise BridgeError("invalid", "A call's arguments must be an object. Nothing was "
                                         "done.")
        app, call = await run_in_threadpool(admit, core, app_id, name)
        ctx = Ctx(core, app)
        if inspect.iscoroutinefunction(call.run):
            return await call.run(ctx, args)
        return await run_in_threadpool(call.run, ctx, args)
    except Exception as exc:  # noqa: BLE001 - every failure becomes one error shape
        raise bridge.refusal(exc) from None


# --- what a program offers -------------------------------------------------------------------


def _names(value: Any, what: str, pattern: re.Pattern[str], most: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > most \
            or not all(isinstance(v, str) and pattern.match(v) for v in value):
        raise BridgeError("invalid", f"\"{what}\" is a list of at most {most} short lower-case "
                                     "names. Nothing was kept.")
    if len(set(value)) != len(value):
        raise BridgeError("invalid", f"\"{what}\" names the same thing twice. Nothing was "
                                     "kept.")
    return value


def needs(app: Installed, permission: str, what: str) -> None:
    if not allows(app.granted, permission):
        raise BridgeError("not_allowed", f"Your app offers {what}, but has not been granted "
                                         f"\"{permission}\". Nothing was kept. Add it to "
                                         "\"permissions\" in jimmyhq.app.json; the owner is "
                                         "asked when they install or update it.")


def check_jobs(app: Installed, value: Any) -> list[dict[str, Any]]:
    """Jobs need ``jobs``. Each runs every so many seconds, or every so many minutes as an
    ``int`` setting the app declares says."""
    if value is None or value == []:
        return []
    needs(app, "jobs", "background jobs")
    if not isinstance(value, list) or len(value) > 50:
        raise BridgeError("invalid", "\"jobs\" is a list of at most 50 objects {name, every} or "
                                     "{name, every_setting}. Nothing was kept.")
    int_settings = {s.key for s in app.manifest.settings if s.type == "int"}
    out: list[dict[str, Any]] = []
    for job in value:
        if not isinstance(job, dict) or set(job) - {"name", "every", "every_setting"} \
                or not isinstance(job.get("name"), str) or not _PLUG_NAME.match(job["name"]):
            raise BridgeError("invalid", "Each job is {name, every} or {name, every_setting}, "
                                         "with a short lower-case name. Nothing was kept.")
        every, setting = job.get("every"), job.get("every_setting")
        if (every is None) == (setting is None):
            raise BridgeError("invalid", f"Job \"{job['name']}\" needs one of \"every\" "
                                         "(seconds) or \"every_setting\". Nothing was kept.")
        if every is not None and (isinstance(every, bool) or not isinstance(every, int)
                                  or not _MIN_JOB_S <= every <= _MAX_JOB_S):
            raise BridgeError("invalid", f"\"every\" for job \"{job['name']}\" is a whole "
                                         f"number of seconds from {_MIN_JOB_S} to a week. "
                                         "Nothing was kept.")
        if setting is not None and setting not in int_settings:
            raise BridgeError("invalid", f"\"every_setting\" for job \"{job['name']}\" must be "
                                         "a whole-number setting your manifest declares. "
                                         "Nothing was kept.")
        out.append({"name": job["name"], "every": every, "every_setting": setting})
    if len({j["name"] for j in out}) != len(out):
        raise BridgeError("invalid", "Two jobs have the same name. Nothing was kept.")
    return out


def check_decisions(app: Installed, value: Any) -> list[str]:
    """Decisions are wanted only on the card kinds the app declares, and need
    ``bench.file``."""
    names = _names(value, "decisions", _PLUG_NAME, 50)
    if names:
        needs(app, "bench.file", "to hear what the owner decided on its cards")
    declared = {c.kind for c in app.manifest.cards}
    for name in names:
        if name not in declared:
            raise BridgeError("invalid", f"\"{name}\" is not a card kind your manifest "
                                         "declares, so no decision on it can reach your app. "
                                         "Nothing was kept.")
    return names


def check_calls(app: Installed, value: Any) -> list[str]:
    """The calls the app's page may make of its program."""
    return _names(value, "calls", _CALL_NAME, 100)


def check_tools(app: Installed, value: Any) -> list[str]:
    """Tools need ``agents.tools`` and must be the ones the manifest declares."""
    names = _names(value, "tools", _PLUG_NAME, 50)
    if names:
        needs(app, "agents.tools", "tools to agents")
        if app.manifest.private:
            raise BridgeError("not_allowed", "Your app is marked private, so what it holds "
                                             "is never shown to agents and it cannot offer "
                                             "them tools. Nothing was kept.")
    declared = {t.name for t in app.manifest.tools}
    for name in names:
        if name not in declared:
            raise BridgeError("invalid", f"\"{name}\" is not a tool your manifest declares. "
                                         "Nothing was kept.")
    return names


def check_sources(app: Installed, value: Any) -> list[str]:
    """Memory sources need ``memory.source``, must be declared, and a private app offers
    none."""
    names = _names(value, "sources", _PLUG_NAME, 20)
    if names:
        needs(app, "memory.source", "memory sources")
        if app.manifest.private:
            raise BridgeError("not_allowed", "Your app is marked private, so it cannot offer "
                                             "what it collects to Memory. Nothing was kept.")
    declared = {s.id for s in app.manifest.memory_sources}
    for name in names:
        if name not in declared:
            raise BridgeError("invalid", f"\"{name}\" is not a memory source your manifest "
                                         "declares. Nothing was kept.")
    return names


def check_inbound(app: Installed, value: Any) -> list[str]:
    """Handlers for messages from devices need ``inbound`` and must be for names the
    manifest declares."""
    names = _names(value, "inbound", _PLUG_NAME, 8)
    if names:
        needs(app, "inbound", "to take messages from devices")
    declared = {i.name for i in app.manifest.inbound}
    for name in names:
        if name not in declared:
            raise BridgeError("invalid", f"\"{name}\" is not a kind of message your "
                                         "manifest's \"inbound\" declares. Nothing was kept.")
    return names


def check_plugs(app: Installed, body: Any) -> dict[str, Any]:
    keys = ("jobs", "decisions", "calls", "tools", "sources", "inbound")
    if not isinstance(body, dict) or set(body) - set(keys):
        raise BridgeError("invalid", f"Plugs are an object with {', '.join(keys)}. Nothing "
                                     "was kept.")
    out = {"jobs": check_jobs(app, body.get("jobs")),
           "decisions": check_decisions(app, body.get("decisions")),
           "calls": check_calls(app, body.get("calls")),
           "tools": check_tools(app, body.get("tools")),
           "sources": check_sources(app, body.get("sources"))}
    if "inbound" in body:  # said only by an SDK that knows it, so older answers stay as they were
        out["inbound"] = check_inbound(app, body.get("inbound"))
    return out


# --- the routes ------------------------------------------------------------------------------


def body_limit(core: Core, app_id: str) -> int:
    """The most bytes one request may weigh: the larger of a value and an answer."""
    return max(bridge.body_limit(core, app_id),
               core.settings.get("apps.call_max_kb") * 1024 + _ENVELOPE_ROOM)


async def read_json(core: Core, app_id: str, request: Request) -> Any:
    most = await run_in_threadpool(body_limit, core, app_id)
    declared = request.headers.get("content-length", "")
    too_large = BridgeError("too_large", f"One request can be at most {most // 1024} KB. "
                                         "Nothing was done. Send less in one call.")
    if declared.isdigit() and int(declared) > most:
        raise too_large
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > most:
            raise too_large
        chunks.append(chunk)
    raw = b"".join(chunks)
    return bridge.parse_body(raw) if raw else {}


def caller(who: Principal = Depends(principal)) -> str:  # noqa: B008
    return app_of(who)


def ok(result: Any) -> dict[str, Any]:
    return {"ok": True, "result": result}


def failed(error: BridgeError) -> dict[str, Any]:
    inbox.issued_to_a_program(error.owner_message)
    return {k: v for k, v in bridge.failed(None, error).items() if k != "id"}


async def answered(work: Callable[[], Any]) -> dict[str, Any]:
    """Whatever the work returns, or whatever stopped it, as the one envelope."""
    try:
        result = work()
        if inspect.isawaitable(result):
            result = await result
        return ok(result)
    except BridgeError as error:
        return failed(error)
    except Exception as exc:  # noqa: BLE001 - every failure becomes one error shape
        return failed(bridge.refusal(exc))


def hello(core: Core, app_id: str) -> dict[str, Any]:
    """Who the app is and what it was granted, and the two things it is told about the
    owner: their time zone (an IANA name, or empty for this computer's) and the day their
    week starts on, as a page is told them in ``hq.ready``."""
    from jimmyhq.apps.files import owner_calendar  # noqa: PLC0415

    app = admit_app(core, app_id)
    m = app.manifest
    return {"app": m.id, "name": m.name, "version": m.version, "api": API_VERSION,
            "permissions": list(app.granted), "private": m.private,
            "settings": bridge.settings_get(Ctx(core, app), {}), **owner_calendar(core)}


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix=PREFIX.rstrip("/"), tags=["app api"])

    @router.get("/hello")
    async def hello_route(app_id: str = Depends(caller)) -> dict[str, Any]:  # noqa: B008
        return await answered(lambda: run_in_threadpool(hello, core, app_id))

    @router.post("/call/{name}")
    async def call_route(name: str, request: Request,
                         app_id: str = Depends(caller)) -> dict[str, Any]:  # noqa: B008
        async def work() -> Any:
            body = await read_json(core, app_id, request)
            if not isinstance(body, dict) or set(body) - {"args"}:
                raise BridgeError("invalid", "A call's body is {\"args\": {...}} only. "
                                             "Nothing was done.")
            return await dispatch(core, app_id, name, body.get("args"))
        return await answered(work)

    @router.put("/kinds")
    async def kinds_route(request: Request,
                          app_id: str = Depends(caller)) -> dict[str, Any]:  # noqa: B008
        async def work() -> Any:
            body = await read_json(core, app_id, request)
            if not isinstance(body, dict) or set(body) != {"kinds"}:
                raise BridgeError("invalid", "Send {\"kinds\": [...]}. Nothing was kept.")

            def run() -> Any:
                app = admit_app(core, app_id)
                bridge.check_rates(core, app_id)
                try:
                    return {"kinds": schemas.declare(core, app.manifest, body["kinds"])}
                except schemas.KindRefused as exc:
                    raise BridgeError("invalid", str(exc)) from None
            return await run_in_threadpool(run)
        return await answered(work)

    @router.put("/plugs")
    async def plugs_route(request: Request,
                          app_id: str = Depends(caller)) -> dict[str, Any]:  # noqa: B008
        async def work() -> Any:
            body = await read_json(core, app_id, request)

            def run() -> Any:
                app = admit_app(core, app_id)
                bridge.check_rates(core, app_id)
                plugs = check_plugs(app, body)
                inbox.set_plugs(core, app_id, plugs)
                return plugs
            return await run_in_threadpool(run)
        return await answered(work)

    @router.get("/inbox")
    async def inbox_route(request: Request, wait: int = Query(0, ge=0, le=3600),  # noqa: B008
                          app_id: str = Depends(caller)) -> dict[str, Any]:  # noqa: B008
        async def work() -> Any:
            await run_in_threadpool(admit_app, core, app_id)
            most = await run_in_threadpool(core.settings.get, "apps.inbox_wait_s")
            deadline = asyncio.get_running_loop().time() + min(wait, most)
            while True:
                found = await run_in_threadpool(inbox.take, core, app_id)
                if found or asyncio.get_running_loop().time() >= deadline:
                    return {"deliveries": found}
                await asyncio.sleep(0.25)
                if await request.is_disconnected():
                    return {"deliveries": []}
                app = await run_in_threadpool(bridge.find_app, core, app_id)
                if app is None or not app.enabled:  # switched off while it waited
                    return {"deliveries": []}
        return await answered(work)

    @router.post("/inbox/{delivery_id}")
    async def answer_route(delivery_id: str, request: Request,
                           app_id: str = Depends(caller)) -> dict[str, Any]:  # noqa: B008
        async def work() -> Any:
            body = await read_json(core, app_id, request)
            if not isinstance(body, dict) or set(body) - {
                    "ok", "result", "error", "owner_message"}:
                raise BridgeError("invalid", "An answer is {\"ok\": true, \"result\": ...} or "
                                             "{\"ok\": false, \"error\": \"...\"}. Nothing "
                                             "was taken.")

            def run() -> Any:
                admit_app(core, app_id)
                return {"taken": inbox.answer(core, app_id, delivery_id, ok=body.get("ok"),
                                              result=body.get("result"),
                                              error=body.get("error"),
                                              owner_message=body.get("owner_message"))}
            return await run_in_threadpool(run)
        return await answered(work)

    return router
