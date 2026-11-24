"""``App``: everything a full app's program does with JimmyHQ.

    app = App()
    app.settings.get(key); app.secrets.get(name); app.secrets.get(name, host=host)
    app.hosts.list() -> OwnerHosts                    hosts the owner gave it himself
    @app.kind(name, v=1, example={...})              class Drafted(Payload): ...
    @app.upcaster(name, from_v)                       fn(payload) -> payload at from_v + 1
    app.tape.print(kind, payload, *, subject=None, cause=None) -> str
    app.tape.read(*, after=0, kinds=None, limit=500) -> list[Event]
    @app.job(name, *, every=None, every_setting=None)
    app.bench.file(kind, title, *, body="", actions=None, subject=None) -> str
    app.bench.update(card_id, **changes); app.bench.withdraw(card_id)
    @app.on_decision(kind)                            fn(decision)
    app.ladder.check(tool, action, category) -> str
    app.model.ask(prompt, *, role=None, system=None, json_schema=None, images=None,
                  untrusted=None, stay_local=False, off_record=False) -> Reply
    app.model.embed(texts) -> Embedding;  jimmyhq_app.similar(query, vectors, top=, least=)
    app.web.search(query, *, limit=None) -> list[WebResult]
    app.web.read(url, *, start=0, chars=None) -> WebPage;  app.web.read_all(url) -> str
    app.calendar -> Calendar(timezone, week_start)    @app.on_settings  fn(key)
    app.limits() -> Limits                            what the owner lets it save, and how big
                                                      an answer may be, as plain numbers
    app.memory.recall(query, *, k=None) -> Recall
    @app.memory_source(id)                            fn(cursor, limit) -> (items, cursor)
    @app.tool(name)                                   fn(args, agent[, role]) -> str
    @app.record_tool(name)                            the same, for a tool whose effect is
                                                      "record": it only writes your records
    app.roles.get() -> {role: RoleState}              who holds your roles, by label only
    app.work.give(role, title, *, details=None, subject=None, due_ms=None) -> str
    app.give_work(...)                                the same; app.work.withdraw(id)
    @app.on_work                                      fn(work: Work), when work comes back
    @app.call(name)                                   fn(args) -> JSON, for the app's page
    @app.inbound(name)                                fn(message: Inbound) -> None, a message
                                                      from one of the owner's devices
    app.db(name="main"); app.private_db(name)
    app.vault.put(data) -> str; app.tasks.create(title); app.emit(event, data); app.run()
    app.net.fetch(url, *, method="GET", headers=None, body=None, json=None, file=None,
                  multipart=None, fresh_connection=False, as_="text") -> dict
    app.files.path(ref) -> Path; app.files.open(ref) -> BinaryIO   a picture the owner gave
    app.feeds.read(app_id, feed, *, after=0, limit=None) -> FeedPage   another app's feed,
                                                      with "feed:<app>.<feed>" granted
    app.ui.count(n)                                   the number beside the app in the sidebar:
                                                      how many things wait on the owner, 0-999
    @app.ui.counter                                   fn() -> int, said for you after every
                                                      delivery and at start, when it changed

``app.run()`` tells JimmyHQ what the app offers, then waits for work (its inbox) until
JimmyHQ stops it. Work is delivered at least once: a handler may see the same delivery
again after a crash, so it should be safe to repeat.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from jimmyhq_app._transport import HttpTransport, Transport
from jimmyhq_app._types import (
    Calendar,
    CallError,
    Decision,
    Embedding,
    Event,
    FeedItem,
    FeedPage,
    Inbound,
    Item,
    Limits,
    Payload,
    Recall,
    Reply,
    RoleState,
    TokenRevoked,
    WebPage,
    WebResult,
    Work,
)

log = logging.getLogger("jimmyhq_app")

INBOX_WAIT_S = 25
# The longest JimmyHQ may let one fetch take (apps.net_fetch_file_timeout_s at its most):
# the SDK waits that long, and a little more, so JimmyHQ's own limit is the one that ends it.
FETCH_WAIT_MOST_S = 3600
FETCH_WAIT_MARGIN_S = 15
# The first contact with JimmyHQ waits this long for it to answer, since JimmyHQ may still
# be starting, and then exits with CORE_UNREACHABLE_EXIT, which JimmyHQ's supervisor does
# not count as a crash (the same number as jimmyhq.apps.runtime.CORE_UNREACHABLE_EXIT).
FIRST_CONTACT_S = 30.0
CORE_UNREACHABLE_EXIT = 75
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}\Z")
_KIND = re.compile(r"^[a-z][a-z0-9_]{0,40}(\.[a-z][a-z0-9_]{0,40}){0,3}\Z")
_DB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}\Z")
_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
_UPLOAD_REF = re.compile(r"^uploads/[0-9a-f]{32}\.(png|jpg|gif|webp|avif|heic)\Z")
_DONE_KEEP = 500
_STOP_GRACE_S = 5.0

_default_transport: Transport | None = None


def use(transport: Transport | None) -> None:
    """Make every ``App()`` made after this talk to ``transport``: a fake core in an app
    author's tests (``jimmyhq_app.testing``). ``None`` goes back to the real one."""
    global _default_transport
    _default_transport = transport


class App:
    def __init__(self, *, transport: Transport | None = None, app_id: str | None = None,
                 data_dir: str | Path | None = None, client: httpx.Client | None = None):
        chosen = transport or _default_transport
        if chosen is None:
            chosen = HttpTransport(client=client)
        self._transport = chosen
        self.id = app_id or getattr(chosen, "app_id", None) or os.environ.get(
            "JIMMYHQ_APP_ID", "")
        given = data_dir or getattr(chosen, "data_dir", None) or os.environ.get(
            "JIMMYHQ_APP_DATA")
        self.data_dir = Path(given) if given else None
        self._kinds: dict[str, dict[int, tuple[type[Payload], dict[str, Any]]]] = {}
        self._upcasters: dict[tuple[str, int], Callable[[dict], dict]] = {}
        self._jobs: dict[str, tuple[Callable[[], Any], int | None, str | None]] = {}
        self._decisions: dict[str, Callable[[Decision], Any]] = {}
        self._sources: dict[str, Callable[[str | None, int], Any]] = {}
        self._tools: dict[str, Callable[..., Any]] = {}
        self.record_tools: set[str] = set()  # declared "effect": "record" in the manifest
        self._on_work: Callable[[Work], Any] | None = None
        self._calls: dict[str, Callable[[dict], Any]] = {}
        self._inbound: dict[str, Callable[[Inbound], Any]] = {}
        self._on_settings: list[Callable[[str], Any]] = []
        self._hello: dict[str, Any] | None = None
        self._kinds_sent: set[tuple[str, int]] = set()
        self._done: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()
        self._running = False
        self.stop = threading.Event()
        self.settings = _Settings(self)
        self.secrets = _Secrets(self)
        self.hosts = _Hosts(self)
        self.tape = _Tape(self)
        self.bench = _Bench(self)
        self.ladder = _Ladder(self)
        self.model = _Model(self)
        self.memory = _Memory(self)
        self.vault = _Vault(self)
        self.tasks = _Tasks(self)
        self.roles = _Roles(self)
        self.work = _Work(self)
        self.net = _Net(self)
        self.files = _Files(self)
        self.web = _Web(self)
        self.feeds = _Feeds(self)
        self.ui = _Ui(self)
        attach = getattr(chosen, "attach", None)
        if callable(attach):
            attach(self)

    # --- talking to JimmyHQ -------------------------------------------------------------------

    def _ask(self, call: str, args: dict[str, Any] | None = None, *,
             timeout: float | None = None) -> Any:
        """One call, as ``POST /call/<call>``. ``timeout``: how long to wait for JimmyHQ's
        answer, when the call may take longer than the transport's usual wait."""
        clean = {k: v for k, v in (args or {}).items() if v is not None}
        if timeout is None:
            return self._transport.request("POST", f"/call/{call}", body={"args": clean})
        return self._transport.request("POST", f"/call/{call}", body={"args": clean},
                                       timeout=timeout)

    @property
    def prefix(self) -> str:
        return f"app.{self.id}."

    def own_subject(self, subject: str | None) -> str | None:
        """A subject as the app wrote it, from the form JimmyHQ keeps it in."""
        mark = f"app.{self.id}:"
        if subject and subject.startswith(mark):
            return subject[len(mark):]
        return subject

    def hello(self, *, fresh: bool = False) -> dict[str, Any]:
        """What ``GET /hello`` says: who the app is, what it was granted, its settings and
        the owner's calendar. Asked once and kept, until a settings delivery says the
        owner's calendar changed."""
        if self._hello is None or fresh:
            self._hello = self._transport.request("GET", "/hello")
        return self._hello

    @property
    def calendar(self) -> Calendar:
        """The owner's time zone and week start, as they are now."""
        said = self.hello()
        return Calendar(timezone=str(said.get("timezone") or ""),
                        week_start=str(said.get("week_start") or "monday"))

    def limits(self) -> Limits:
        """The owner's limits for what an app may save and how big its answers may be, as
        they are now: ``save_max_mb``, ``save_parts``, ``save_per_minute``, ``save_html``,
        ``call_max_kb`` and ``picture_px``. It needs no permission. An older JimmyHQ refuses
        it with a ``CallError``: keep to the numbers your app always used then."""
        told = self._ask("ui.limits")
        known = Limits.__dataclass_fields__
        return Limits(**{k: v for k, v in told.items() if k in known}) \
            if isinstance(told, dict) else Limits()

    @property
    def timezone(self) -> str:
        return self.calendar.timezone

    @property
    def week_start(self) -> str:
        return self.calendar.week_start

    # --- declaring ------------------------------------------------------------------------------

    def kind(self, name: str, v: int = 1, *, example: dict[str, Any]
             ) -> Callable[[type[Payload]], type[Payload]]:
        """Declare one version of a kind of event this app writes. The example is
        checked against the model now, and both are sent to JimmyHQ at start."""
        if not _KIND.match(name):
            raise ValueError(f"A kind's name is a short lower-case word, not {name!r}.")

        def deco(model: type[Payload]) -> type[Payload]:
            model.model_validate(example)
            versions = self._kinds.setdefault(name, {})
            if v in versions:
                raise ValueError(f"{name} v{v} is declared twice.")
            versions[v] = (model, example)
            return model
        return deco

    def upcaster(self, name: str, from_v: int) -> Callable[[Callable[[dict], dict]],
                                                           Callable[[dict], dict]]:
        """Turn a payload of ``name`` written at ``from_v`` into ``from_v + 1``. JimmyHQ
        never upgrades an app's payloads; ``tape.read`` does it here."""
        def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
            self._upcasters[(name, from_v)] = fn
            return fn
        return deco

    def job(self, name: str, *, every: int | None = None, every_setting: str | None = None
            ) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
        """Run ``fn()`` in the background, every ``every`` seconds, or every so many
        minutes as the app's own whole-number setting ``every_setting`` says."""
        if not _NAME.match(name) or (every is None) == (every_setting is None):
            raise ValueError("A job has a short lower-case name and one of every= or "
                             "every_setting=.")

        def deco(fn: Callable[[], Any]) -> Callable[[], Any]:
            self._jobs[name] = (fn, every, every_setting)
            return fn
        return deco

    def on_decision(self, kind: str) -> Callable[[Callable[[Decision], Any]],
                                                 Callable[[Decision], Any]]:
        """Hear what the owner decided on the cards of ``kind`` this app filed."""
        def deco(fn: Callable[[Decision], Any]) -> Callable[[Decision], Any]:
            self._decisions[kind] = fn
            return fn
        return deco

    def memory_source(self, source_id: str) -> Callable[[Callable], Callable]:
        def deco(fn: Callable[[str | None, int], Any]) -> Callable:
            self._sources[source_id] = fn
            return fn
        return deco

    def tool(self, name: str) -> Callable[[Callable], Callable]:
        """An agent tool. ``fn(args, agent)``, or ``fn(args, agent, role)`` to hear which of
        your roles it was called through (None through the whole-app capability)."""
        def deco(fn: Callable[..., Any]) -> Callable:
            self._tools[name] = fn
            return fn
        return deco

    def record_tool(self, name: str) -> Callable[[Callable], Callable]:
        """A tool whose manifest ``effect`` is ``"record"``: it only writes into your
        app's own records (\"submit a draft\"). It runs without the ladder; while it is
        being handled JimmyHQ refuses every call that reaches outside your app, so keep
        anything that leaves the building for later."""
        def deco(fn: Callable[..., Any]) -> Callable:
            self._tools[name] = fn
            self.record_tools.add(name)
            return fn
        return deco

    def on_work(self, fn: Callable[[Work], Any]) -> Callable[[Work], Any]:
        """Hear when a piece of work given with ``work.give`` is done, set aside, dropped
        or reassigned."""
        self._on_work = fn
        return fn

    def give_work(self, role: str, title: str, **more: Any) -> str:
        return self.work.give(role, title, **more)

    def on_settings(self, fn: Callable[[str], Any]) -> Callable[[str], Any]:
        """Hear that a setting changed: ``fn(key)``, with one of the app's own keys, or
        ``"owner.timezone"`` or ``"owner.week_start"`` for the owner's calendar (read the
        new value with ``app.calendar``)."""
        self._on_settings.append(fn)
        return fn

    def call(self, name: str) -> Callable[[Callable], Callable]:
        """Answer ``hq.app.call(name, args)`` from the app's own page."""
        def deco(fn: Callable[[dict], Any]) -> Callable:
            self._calls[name] = fn
            return fn
        return deco

    def inbound(self, name: str) -> Callable[[Callable[[Inbound], Any]],
                                             Callable[[Inbound], Any]]:
        """Take the messages a device sends to ``POST /api/in/<app>/<name>``: ``fn(message)``
        with an ``Inbound``. ``name`` is one of the manifest's ``inbound`` names, and the app
        needs the ``inbound`` permission. Whatever ``fn`` returns is dropped: the device is
        only ever told the message was taken. If it raises, the owner sees a count and the
        sentence on the app's page; the message is not handed over again."""
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"An inbound name is a short lower-case word, not {name!r}.")

        def deco(fn: Callable[[Inbound], Any]) -> Callable[[Inbound], Any]:
            self._inbound[name] = fn
            return fn
        return deco

    def plugs(self) -> dict[str, Any]:
        """What this app offers, as it tells JimmyHQ at start."""
        out: dict[str, Any] = {
            "jobs": [{"name": n, **({"every": e} if e is not None else {"every_setting": s})}
                     for n, (_, e, s) in self._jobs.items()],
            "decisions": sorted(self._decisions), "calls": sorted(self._calls),
            "tools": sorted(self._tools), "sources": sorted(self._sources),
        }
        if self._inbound:  # said only when there are some, so an older JimmyHQ is not puzzled
            out["inbound"] = sorted(self._inbound)
        return out

    def send_kinds(self) -> None:
        """Send every declared kind JimmyHQ has not been sent yet."""
        waiting = [(n, v) for n, versions in self._kinds.items() for v in versions
                   if (n, v) not in self._kinds_sent]
        if not waiting:
            return
        kinds = [{"name": n, "v": v, "schema": self._kinds[n][v][0].model_json_schema(),
                  "example": self._kinds[n][v][1]} for n, v in waiting]
        self._transport.request("PUT", "/kinds", body={"kinds": kinds})
        self._kinds_sent.update(waiting)

    def first_contact(self, *, patience_s: float | None = None,
                      clock: Callable[[], float] = time.monotonic) -> None:
        """``register``, patiently: JimmyHQ may still be starting, so a refused or timed-out
        connection is tried again, a little later each time, for ``FIRST_CONTACT_S``.
        After that the program exits with ``CORE_UNREACHABLE_EXIT``, and JimmyHQ starts it
        again once it answers. Any other refusal is not waited out."""
        limit = FIRST_CONTACT_S if patience_s is None else patience_s
        deadline = clock() + limit
        pause = 0.25
        while True:
            try:
                self.register()
                return
            except httpx.TransportError as exc:
                if self.stop.is_set():
                    raise SystemExit(0) from None
                left = deadline - clock()
                if left <= 0:
                    log.warning("JimmyHQ did not answer within %.0fs (%s), so this program "
                                "is stopping. JimmyHQ starts it again once it answers.",
                                limit, exc)
                    raise SystemExit(CORE_UNREACHABLE_EXIT) from None
                self.stop.wait(min(pause, left))
                pause = min(pause * 2, 4.0)

    def register(self) -> None:
        self.send_kinds()
        self._transport.request("PUT", "/plugs", body=self.plugs())
        self.ui.refresh()  # the sidebar's number is right from the first moment

    def emit(self, event: str, data: Any = None) -> None:
        """Tell this app's own open pages that something changed: they hear it with
        ``hq.on(event, fn)``. A nudge, not the record; a page that is closed misses it."""
        self._ask("app.emit", {"event": event, "data": data})

    # --- own databases --------------------------------------------------------------------------

    def _db_path(self, name: str) -> Path:
        if not _DB_NAME.match(name) or name == "app":  # app.db is the core's, for storage.*
            raise ValueError(f"A database's name is a short lower-case word, not {name!r}.")
        if self.data_dir is None:
            raise RuntimeError("This app has no data folder: start it from JimmyHQ, or set "
                               "JIMMYHQ_APP_DATA while developing.")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / f"{name}.db"

    def db(self, name: str = "main") -> sqlite3.Connection:
        """The app's own SQLite database, in its data folder."""
        conn = sqlite3.connect(self._db_path(name))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def private_db(self, name: str) -> PrivateDB:
        """A database for what must be deletable for good: deleted rows are overwritten
        and the log is emptied after each ``purge``."""
        return PrivateDB(self._db_path(name))

    # --- the inbox --------------------------------------------------------------------------

    def handle(self, delivery: dict[str, Any]) -> dict[str, Any]:
        """Carry out one delivery and return the answer for JimmyHQ. A delivery seen
        again is answered as before without running twice in this process."""
        delivery_id = str(delivery.get("id"))
        if delivery_id in self._done:
            return self._done[delivery_id]
        try:
            result = self._run(str(delivery.get("type")), delivery.get("data") or {})
            answer: dict[str, Any] = {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001 - reported to JimmyHQ, not lost
            log.exception("delivery %s failed", delivery_id)
            answer = {"ok": False, "error": _sentence(exc)}
            # A refusal from JimmyHQ that the handler let through carries the words JimmyHQ
            # wrote for the owner: the page that asked shows them (``why()`` in ``_ui.js``).
            words = exc.owner_message if isinstance(exc, CallError) else None
            if isinstance(words, str) and words.strip():
                answer["owner_message"] = words.strip()[:_OWNER_MESSAGE_MAX]
        self._done[delivery_id] = answer
        while len(self._done) > _DONE_KEEP:
            self._done.popitem(last=False)
        self.ui.refresh()  # whatever that changed, the sidebar's number follows
        return answer

    def _run(self, kind: str, data: dict[str, Any]) -> Any:
        if kind == "job":
            return _need(self._jobs, data.get("name"), "job")[0]()
        if kind == "decision":
            card_kind = str(data.get("card_kind") or data.get("kind") or "")
            short = card_kind.removeprefix(self.prefix)
            fn = _need(self._decisions, short, "decision handler")
            return fn(Decision(card_id=data["card_id"], kind=short, action=data["action"],
                               subject=self.own_subject(data.get("subject")),
                               note=data.get("note"), reason=data.get("reason"),
                               event_id=data.get("event_id"), ts=data.get("ts")))
        if kind == "tool":
            fn = _need(self._tools, data.get("name"), "tool")
            given = [data.get("args") or {}, data.get("agent") or ""]
            if _takes_role(fn):
                given.append(data.get("role"))
            out = fn(*given)
            return out if isinstance(out, dict) else {"result": str(out), "ok": True}
        if kind == "work":
            if self._on_work is None:
                return None  # nobody listens: the work's end is on the owner's record
            return self._on_work(Work(
                id=data["id"], role=data["role"], state=data["state"],
                subject=self.own_subject(data.get("subject")), note=data.get("note"),
                agent_label=data.get("agent_label")))
        if kind == "collect":
            fn = _need(self._sources, data.get("source"), "memory source")
            items, cursor = fn(data.get("cursor"), int(data.get("limit") or 50))
            return {"items": [i.__dict__ if isinstance(i, Item) else i for i in items],
                    "cursor": cursor}
        if kind == "call":
            return _need(self._calls, data.get("name"), "call")(data.get("args") or {})
        if kind == "inbound":
            fn = _need(self._inbound, data.get("name"), "inbound handler")
            body = data.get("body")
            query = data.get("query")
            fn(Inbound(body=body if isinstance(body, dict | list) else {},
                       received_ms=int(data.get("received_ms") or 0),
                       key_name=str(data.get("key_name") or ""), name=str(data["name"]),
                       query={str(k): str(v) for k, v in query.items()}
                       if isinstance(query, dict) else {}))
            return None  # what the handler returns never goes anywhere
        if kind == "settings":
            key = str(data.get("key") or "")
            if key.startswith("owner."):
                self._hello = None  # the calendar is asked for again when next read
            for fn in self._on_settings:
                fn(key)
            return None  # settings are read afresh on every get
        raise ValueError(f"This version of the SDK does not know deliveries of type {kind!r}.")

    def run(self) -> None:
        """Tell JimmyHQ what the app offers, then do the work it hands over until JimmyHQ
        stops the program. Calling it again does nothing. With a fake core it only
        registers: the test drives the deliveries."""
        if self._running:
            return
        self._running = True
        if getattr(self._transport, "fake", False):
            self.register()
            return
        prepare = getattr(self._transport, "prepare", None)
        if callable(prepare):
            prepare()  # the token is read off the pipe before anything else reads it
        if os.environ.get("JIMMYHQ_SUPERVISED") == "1":
            _watch_parent(self.stop)
        self.first_contact()
        pause = 1.0
        while not self.stop.is_set():
            try:
                found = self._transport.request("GET", "/inbox",
                                                params={"wait": INBOX_WAIT_S},
                                                timeout=INBOX_WAIT_S + 15)
                pause = 1.0
            except TokenRevoked:
                log.warning("JimmyHQ no longer accepts this app's token; stopping.")
                raise SystemExit(3) from None
            except (CallError, httpx.HTTPError) as exc:
                log.warning("could not ask JimmyHQ for work (%s); trying again in %.0fs",
                            exc, pause)
                self.stop.wait(pause)
                pause = min(pause * 2, 30.0)
                continue
            for delivery in found.get("deliveries", []):
                self._answer(delivery["id"], self.handle(delivery))

    def _answer(self, delivery_id: str, answer: dict[str, Any]) -> None:
        for attempt in range(5):
            try:
                self._transport.request("POST", f"/inbox/{delivery_id}", body=answer)
                return
            except TokenRevoked:
                raise SystemExit(3) from None
            except CallError as exc:
                if exc.code in ("too_large", "invalid"):  # sending it again will not help
                    self._transport.request("POST", f"/inbox/{delivery_id}", body={
                        "ok": False, "error": f"The app's answer was refused: {exc.message}"})
                    return
                time.sleep(2 ** attempt)
            except httpx.HTTPError:
                time.sleep(2 ** attempt)
        log.warning("could not answer delivery %s; JimmyHQ will hand it over again",
                    delivery_id)


def _takes_role(fn: Callable[..., Any]) -> bool:
    import inspect  # noqa: PLC0415

    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return True
    return sum(1 for p in params if p.kind in (p.POSITIONAL_ONLY,
                                                p.POSITIONAL_OR_KEYWORD)) >= 3


def _need(table: dict[str, Any], name: Any, what: str) -> Any:
    if name not in table:
        raise LookupError(f"This app has no {what} called {name!r}.")
    return table[name]


_OWNER_MESSAGE_MAX = 500  # the most of it JimmyHQ takes

# Faults in the program's own code: their text ("'name'") is not a sentence for anyone.
_FAULTS = (KeyError, IndexError, TypeError, AttributeError, NameError)


def _sentence(exc: Exception) -> str:
    """What the owner reads when a handler fails: the sentence the handler raised with,
    as it is, so `raise ValueError("The list is empty. Nothing was changed.")` shows
    exactly that. (Earlier versions put "The app could not do that: " in front; a page
    that strips that lead-in still works, as the sentence no longer starts with it.) A
    fault in the program's own code, or an error with no words, gets one plain sentence
    naming the kind of fault instead."""
    text = str(exc).strip()
    if not text or isinstance(exc, _FAULTS):
        return (f"The app's program hit a fault ({type(exc).__name__}) and could not do "
                "that. Nothing more is known; tell the app's author.")
    return text[:1900]


def _watch_parent(stop: threading.Event) -> None:
    """Stop when JimmyHQ's supervisor closes the pipe on standard input."""
    def wait() -> None:
        with contextlib.suppress(Exception):
            sys.stdin.read()
        stop.set()
        time.sleep(_STOP_GRACE_S)
        os._exit(0)  # the long-poll did not return in time; do not linger
    threading.Thread(target=wait, name="watch-jimmyhq", daemon=True).start()


class PrivateDB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA secure_delete=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def purge(self, table: str, where: str, params: Any = ()) -> int:
        """Delete rows for good: overwritten in the file, and gone from the log."""
        if not _TABLE.match(table):
            raise ValueError(f"{table!r} is not a table name.")
        count = self.conn.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount  # noqa: S608
        self.conn.commit()
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return count

    def close(self) -> None:
        self.conn.close()


# --- the parts of App ---------------------------------------------------------------------------


class _Part:
    def __init__(self, app: App):
        self._app = app


class _Settings(_Part):
    def get(self, key: str) -> Any:
        return self._app._ask("settings.get", {"key": key})  # noqa: SLF001

    def all(self) -> dict[str, Any]:
        return self._app._ask("settings.get")  # noqa: SLF001


class _Secrets(_Part):
    def get(self, name: str, *, host: str | None = None) -> str | None:
        """One of the app's secrets, or None when the owner has not typed it in. A secret
        the manifest keeps for each host the owner grants (``"for_each": "owner_host"``)
        is read with ``host``: one of ``app.hosts.list().hosts``."""
        return self._app._ask("secrets.get", {"name": name, "host": host})  # noqa: SLF001


class OwnerHosts:
    """The hosts the owner gave the app himself (``owner_hosts`` in its manifest), oldest
    first, with how many it may have and why it said it asks."""

    def __init__(self, data: dict[str, Any]):
        self.hosts: list[str] = [str(h) for h in data.get("hosts") or []]
        self.max: int = int(data.get("max") or 0)
        self.purpose: str = str(data.get("purpose") or "")

    def __repr__(self) -> str:
        return f"OwnerHosts(hosts={self.hosts!r}, max={self.max})"


class _Hosts(_Part):
    def list(self) -> OwnerHosts:  # noqa: A003
        """The hosts the owner gave the app on its page. An app can never add one: only
        the owner can, so ask him to with a sentence on your page."""
        return OwnerHosts(self._app._ask("hosts.list"))  # noqa: SLF001


class _Tape(_Part):
    def print(self, kind: str, payload: Payload | dict[str, Any], *,  # noqa: A003
              subject: str | None = None, cause: str | None = None) -> str:
        """Write one event of the app's own kind on the owner's record. Returns its id."""
        app = self._app
        versions = app._kinds.get(kind)  # noqa: SLF001
        if versions:
            model = versions[max(versions)][0]
            if isinstance(payload, Payload) and not isinstance(payload, model):
                raise TypeError(f"{kind} takes a {model.__name__}.")
            checked = payload if isinstance(payload, model) else model.model_validate(payload)
            payload = checked.model_dump(mode="json")
        elif isinstance(payload, Payload):
            payload = payload.model_dump(mode="json")
        app.send_kinds()
        written = app._ask("tape.print", {"kind": kind, "payload": payload,  # noqa: SLF001
                                          "subject": subject, "cause": cause})
        return written["id"]

    def read(self, *, after: int = 0, kinds: list[str] | None = None,
             limit: int = 500) -> list[Event]:
        """Events after ``after``, oldest first: what this app wrote, or with the
        ``tape.read`` permission the whole record. A kind of the app's own may be named
        short. The app's own payloads are upgraded to the latest version it declares."""
        app = self._app
        own = app._kinds  # noqa: SLF001
        full = [k if "." in k and k not in own else app.prefix + k
                for k in kinds] if kinds else None
        cursor = after
        for _ in range(20):  # past stretches of the record that hold nothing for this app
            page = app._ask("tape.read", {"after": cursor, "kinds": full,  # noqa: SLF001
                                          "limit": max(1, min(limit, 500))})
            if page["events"] or page["next"] == cursor:
                return [self._event(e) for e in page["events"]]
            cursor = page["next"]
        return []

    def _event(self, raw: dict[str, Any]) -> Event:
        app = self._app
        v, payload = raw["v"], raw["payload"]
        short = raw["kind"].removeprefix(app.prefix) if raw["kind"].startswith(
            app.prefix) else None
        if short is not None and raw.get("readable", True):
            while (short, v) in app._upcasters:  # noqa: SLF001
                payload = app._upcasters[(short, v)](dict(payload))  # noqa: SLF001
                v += 1
        return Event(seq=raw["seq"], id=raw["id"], ts=raw["ts"], kind=raw["kind"], v=v,
                     actor=raw["actor"], payload=payload, cause=raw.get("cause"),
                     root=raw.get("root"), subject=app.own_subject(raw.get("subject")),
                     readable=raw.get("readable", True))


class _Bench(_Part):
    def file(self, kind: str, title: str, *, body: str = "",
             actions: list[dict[str, Any]] | None = None, subject: str | None = None,
             **more: Any) -> str:
        """Put a card on the owner's Bench. Returns its id. JimmyHQ files it under
        ``app.<id>.<kind>``, as this app. Only what the app gave is sent: an empty ``body``,
        ``actions`` or ``subject`` is left out, so a private app, whose card may carry a
        title and none of its own text, files with ``bench.file(kind, title)``."""
        args: dict[str, Any] = {"kind": kind, "title": title}
        if body:
            args["body"] = body
        if actions:
            args["actions"] = actions
        if subject:
            args["subject"] = subject
        args.update({k: v for k, v in more.items() if v is not None and v != ""})
        found = self._app._ask("bench.file", args)  # noqa: SLF001
        return found["card_id"]

    def update(self, card_id: str, **changes: Any) -> Any:
        return self._app._ask("bench.update", {"card_id": card_id, **changes})  # noqa: SLF001

    def withdraw(self, card_id: str, *, note: str | None = None) -> Any:
        return self._app._ask("bench.withdraw", {"card_id": card_id, "note": note})  # noqa: SLF001


class _Ladder(_Part):
    def check(self, tool: str, action: str, category: str) -> str:
        """"read", "approve" or "alone": whether the owner lets this app do it alone."""
        return self._app._ask("ladder.check", {  # noqa: SLF001
            "tool": tool, "action": action, "category": category})


class _Model(_Part):
    def ask(self, prompt: str, *, role: str | None = None, system: str | None = None,
            json_schema: dict[str, Any] | None = None, images: list[str] | None = None,
            untrusted: str | list[str] | None = None, stay_local: bool = False,
            off_record: bool = False) -> Reply:
        """Ask the owner's models. ``role`` is left to JimmyHQ: 'default', or 'vision'
        when ``images`` (paths inside the app's data folder) are given. ``untrusted`` is
        outside text, such as a web page: JimmyHQ puts it after the prompt between marked
        lines that tell the model it is data, not instructions. ``stay_local`` lets only a
        model on the owner's own machines answer this call (refused with a sentence when
        none is set up); ``off_record`` keeps token counts only, never the text. Both make
        one call stricter than the owner's settings, never looser."""
        found = self._app._ask("model.ask", {  # noqa: SLF001
            "prompt": prompt, "role": role, "system": system, "json_schema": json_schema,
            "images": [str(i).replace("\\", "/") for i in images] if images else None,
            "untrusted": untrusted, "stay_local": True if stay_local else None,
            "off_record": True if off_record else None})
        return Reply(text=found["text"], data=found.get("data"),
                     local=bool(found.get("local")), model=found.get("model"))

    def embed(self, texts: list[str]) -> Embedding:
        """One vector for each text, from the owner's 'embeddings' role. Compare them
        with ``jimmyhq_app.similar``."""
        found = self._app._ask("model.embed", {"texts": list(texts)})  # noqa: SLF001
        return Embedding(vectors=found["vectors"], model=found.get("model"),
                         local=bool(found.get("local")))


class _Memory(_Part):
    def recall(self, query: str, *, k: int | None = None) -> Recall:
        found = self._app._ask("memory.recall", {"query": query, "k": k})  # noqa: SLF001
        return Recall(text=found["text"], passages=found.get("passages", []))


class _Vault(_Part):
    def put(self, data: bytes) -> str:
        import base64  # noqa: PLC0415

        found = self._app._ask("vault.put", {  # noqa: SLF001
            "base64": base64.b64encode(data).decode("ascii")})
        return found["ref"]


class _Tasks(_Part):
    def create(self, title: str, **fields: Any) -> dict[str, Any]:
        """Add a task to the owner's list. Only the fields the app gave are sent (an empty
        ``notes`` is left out), so a private app adds a task with ``tasks.create(title)``."""
        given = {k: v for k, v in fields.items() if v is not None and v != ""}
        return self._app._ask("tasks.create", {"title": title, **given})  # noqa: SLF001

    def list(self, *, when: str = "open", area: str | None = None) -> list[dict]:  # noqa: A003
        return self._app._ask("tasks.list", {"when": when, "area": area})["tasks"]  # noqa: SLF001

    def complete(self, task_id: str) -> dict[str, Any]:
        return self._app._ask("tasks.complete", {"id": task_id})  # noqa: SLF001


class _Roles(_Part):
    def get(self) -> dict[str, RoleState]:
        """Which of your roles are filled, by the agent's label only."""
        found = self._app._ask("roles.get")  # noqa: SLF001
        return {role: RoleState(filled=bool(v.get("filled")), agent_label=v.get("agent_label"))
                for role, v in found.items()}


class _Work(_Part):
    def give(self, role: str, title: str, *, details: str | None = None,
             subject: str | None = None, due_ms: int | None = None) -> str:
        """Give a piece of work to whoever holds one of your roles. Returns its id.
        ``CallError`` with code ``not_found`` when nobody holds the role: do the work
        your plain way instead."""
        return self._app._ask("work.give", {  # noqa: SLF001
            "role": role, "title": title, "details": details, "subject": subject,
            "due_ms": due_ms})["id"]

    def withdraw(self, work_id: str) -> Any:
        return self._app._ask("work.withdraw", {"id": work_id})  # noqa: SLF001


class _Ui(_Part):
    """The app's own line in the sidebar."""

    MOST = 999  # what JimmyHQ accepts; ``refresh`` keeps a counter's answer within it

    def __init__(self, app: App):
        super().__init__(app)
        self._counter: Callable[[], int] | None = None
        self._said: int | None = None

    def count(self, n: int) -> None:
        """Say how many things wait on the owner, so the number beside the app in the
        sidebar is right without the app being open. A whole number from 0 to 999: JimmyHQ
        refuses anything else with a sentence (``CallError``). It needs no permission. While
        the app's page is open and has said a number of its own, the page's number shows."""
        self._app._ask("ui.count", {"n": n})  # noqa: SLF001
        self._said = n

    def counter(self, fn: Callable[[], int]) -> Callable[[], int]:
        """Register the one function that counts what waits on the owner. The SDK asks it
        after every delivery the program handles (a job, a decision, a call from the page,
        a tool, work coming back, a setting) and when the program starts, and says the
        number only when it changed. A number above 999 is said as 999; a counter that
        raises is logged and the last number stands. The page should count the same way."""
        self._counter = fn
        return fn

    def refresh(self) -> int | None:
        """Ask the counter now and say its number if it changed. Never raises: a count
        that could not be said must not fail the work that caused it."""
        if self._counter is None:
            return None
        try:
            n = max(0, min(self.MOST, int(self._counter())))
            if n != self._said:
                self.count(n)
            return n
        except Exception as exc:  # noqa: BLE001 - the sidebar's number is never worth a failure
            log.warning("could not say how many things wait on the owner: %s", exc)
            return None


class _Net(_Part):
    def fetch(self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
              body: str | None = None, json: Any = None, file: str | None = None,
              multipart: list[dict[str, Any]] | None = None,
              fresh_connection: bool = False, as_: str = "text",
              timeout_s: int | None = None) -> dict[str, Any]:
        """An https request to a host the owner granted, made by JimmyHQ. ``file`` (a path
        or upload ref in the app's data folder) is sent as the raw body; ``multipart`` is
        a list of ``{name, value}`` and ``{name, file, type?, filename?}``; inside ``json``
        a value ``{"$file": ref, "as": "data_url" | "base64"}`` is filled in with the
        file. JimmyHQ reads the files itself, so they never pass through this call.
        ``timeout_s`` asks for a shorter wait than the owner's limit, never a longer one;
        what the manifest's ``net_expects`` says of the host, and the owner's settings,
        decide the longest. This call waits for JimmyHQ at least that long."""
        wait = (timeout_s if isinstance(timeout_s, int) and timeout_s > 0
                else FETCH_WAIT_MOST_S) + FETCH_WAIT_MARGIN_S
        return self._app._ask("net.fetch", {  # noqa: SLF001
            "url": url, "method": method, "headers": headers, "body": body, "json": json,
            "file": file, "multipart": multipart,
            "fresh_connection": True if fresh_connection else None,
            "as": None if as_ == "text" else as_, "timeout_s": timeout_s}, timeout=wait)


class _Files(_Part):
    def path(self, ref: str) -> Path:
        """Where a picture the owner gave the app is kept, from the ``ref`` its page was
        given. Refuses anything that is not such a ref, or that goes through a link."""
        if not isinstance(ref, str) or not _UPLOAD_REF.match(ref):
            raise ValueError("That is not the ref of a picture the owner gave this app. "
                             "Use the ref hq.files.upload returned, such as "
                             "\"uploads/<32 letters and digits>.jpg\".")
        data = self._app.data_dir
        if data is None:
            raise RuntimeError("This app has no data folder: start it from JimmyHQ, or set "
                               "JIMMYHQ_APP_DATA while developing.")
        here = data / ref
        for step in (data, data / "uploads", here):
            if step.is_symlink():
                raise ValueError("That picture's place goes through a link, which is not "
                                 "followed.")
        if not here.is_file():
            raise FileNotFoundError("There is no such picture. It may have been deleted.")
        return here

    def open(self, ref: str) -> BinaryIO:  # noqa: A003
        """The picture, open for reading as bytes. Close it when done (``with``)."""
        return self.path(ref).open("rb")


class _Web(_Part):
    def search(self, query: str, *, limit: int | None = None) -> list[WebResult]:
        """Search the web with the owner's search provider. Needs ``web``."""
        found = self._app._ask("web.search", {"query": query, "limit": limit})  # noqa: SLF001
        return [WebResult(title=r.get("title", ""), url=r.get("url", ""),
                          snippet=r.get("snippet", ""), text=r.get("text"))
                for r in found["results"]]

    def read(self, url: str, *, start: int = 0, chars: int | None = None) -> WebPage:
        """One piece of a public page, as cleaned text, from ``start``. Needs ``web``."""
        found = self._app._ask("web.read", {"url": url, "start": start,  # noqa: SLF001
                                            "chars": chars})
        return WebPage(url=found["url"], final_url=found["final_url"], title=found["title"],
                       text=found["text"], start=found["start"], end=found["end"],
                       total=found["total"], next=found.get("next"),
                       truncated=bool(found.get("truncated")),
                       links=list(found.get("links") or []))

    def read_all(self, url: str, *, most: int = 400_000) -> str:
        """The whole page, piece by piece, up to ``most`` characters."""
        pieces: list[str] = []
        start: int | None = 0
        size = 0
        while start is not None and size < most:
            page = self.read(url, start=start)
            pieces.append(page.text)
            size += len(page.text)
            start = page.next
        return "".join(pieces)[:most]


class _Feeds(_Part):
    def read(self, app_id: str, feed: str, *, after: int = 0,
             limit: int | None = None) -> FeedPage:
        """Items of another app's feed printed after ``after``, oldest first. Needs
        ``feed:<app_id>.<feed>``. Raises ``CallError`` with a sentence when the feed
        cannot be read (the app is not installed, is switched off, or no longer offers
        it), so an empty page always means nothing new."""
        found = self._app._ask("feed.read", {"app": app_id, "feed": feed,  # noqa: SLF001
                                             "after": after, "limit": limit})
        items = [FeedItem(seq=int(i["seq"]), id=str(i["id"]), ts=int(i["ts"]), v=int(i["v"]),
                          erased=bool(i.get("erased")), item=i.get("item"))
                 for i in found.get("items", [])]
        return FeedPage(app=app_id, feed=feed, items=items, next=int(found["next"]))
