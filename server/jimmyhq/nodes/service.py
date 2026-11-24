"""The hub's side of nodes: the roster, the jobs handed to nodes, and the model
providers that stand for a node's model servers.

**How a model call reaches a node.** A model server on a node shows up as an
ordinary OpenAI-compatible provider whose address is this machine's own relay,
``/api/nodes/relay/<node id>/<offer>``, with a key only this install knows. Every
process's Brain (web, worker, village) calls it like any other provider. The web
process, which holds the nodes' connections, turns each call into a job; the node
picks the job up over the connection it opened, asks its own model server on its
own loopback address, and sends the answer back as it is made. Nothing on the node
listens to the network, and the hub never connects to a node.

**Back-pressure.** A reply waits in a short queue per job. When the reader is slow
the queue fills, the hub stops reading the node's upload, the node stops reading
its model server, and the model server waits. Nothing is buffered without bound.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from starlette.requests import ClientDisconnect

from jimmyhq import __version__
from jimmyhq.core import Core
from jimmyhq.ids import now_ms, ulid
from jimmyhq.models import brain
from jimmyhq.models.brain import secret_name
from jimmyhq.models.shapes import Provider, Target
from jimmyhq.models.validate import Invalid
from jimmyhq.nodes import protocol
from jimmyhq.nodes.lens import NodesLens

log = logging.getLogger("jimmyhq.nodes")

RELAY_SECRET = "nodes.relay_key"  # noqa: S105 - the name it is kept under, not the key
RELAY_KEY_PREFIX = "jhqr_"
MAX_BODY = 64 * 1024 * 1024  # the largest request passed to a node, or plain answer back
POLL_S = 20  # how long a node's request for work is held open
_QUEUE = 32  # pieces of one reply waiting for their reader
_SLICE_S = 0.25
_LOOK_S = 2.0  # how often a waiting job checks that its node is still there

_SENTENCES = {
    "unknown": (503, "That node is no longer joined to this machine."),
    "not_connected": (503, "The node {name} is not connected. Check that the machine is on "
                           "and that `jimmyhq node run` is running on it."),
    "not_offered": (503, "The node {name} no longer offers {offer}."),
    "pickup": (504, "The node {name} did not take the request in time. It may be busy, or "
                    "its link to this machine may be slow."),
    "went_away": (503, "The node {name} went away before it finished answering. Check that "
                       "the machine is on and that `jimmyhq node run` is running on it."),
    "removed": (503, "The node {name} was removed from this machine while it was answering."),
    "quiet": (504, "The node {name} went quiet for too long while answering."),
    "unreachable": (502, "The node {name} could not reach its model server for {offer}. "
                         "Check that the model server is running on that machine."),
    "timeout": (504, "The model server for {offer} on the node {name} took too long to answer."),
    "broken": (502, "The model server for {offer} on the node {name} stopped part-way "
                    "through its answer."),
    "no_offer": (503, "The node {name} no longer offers {offer}."),
    "no_handler": (503, "The node {name} cannot do that kind of work yet. Update JimmyHQ on "
                        "that machine."),
    "bad_job": (502, "The node {name} would not take the request as it was sent."),
    "bad_reply": (502, "The node {name} sent back something that could not be read."),
    "too_big": (502, "The answer from the node {name} was too large to pass on."),
}


class NodeError(Exception):
    """A job for a node did not work. ``detail`` is a sentence for the owner."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def provider_id(node_name: str, offer: str) -> str:
    """The id of the provider that stands for one model server on one node. A
    node's name has no underscore, so the first one always divides the two."""
    return f"n-{node_name}_{offer}"


def roles_without(roles: dict[str, list[Target]], gone: set[str]) -> dict[str, list[Target]]:
    """For each role that uses a provider in ``gone``: the choices it keeps, in
    order. An empty list means the role loses its model altogether."""
    return {role: [t for t in targets if t.provider_id not in gone]
            for role, targets in roles.items()
            if any(t.provider_id in gone for t in targets)}


class Job:
    """One piece of work for a node, and the reply as it comes back."""

    def __init__(self, node_id: str, node_name: str, kind: str, offer: str,
                 payload: dict[str, Any]):
        self.id = ulid()
        self.node_id = node_id
        self.node_name = node_name
        self.kind = kind
        self.offer = offer
        self.payload = payload
        self.acked = asyncio.Event()  # the node opened its reply
        self.head: tuple[int, str] | None = None
        self.head_ready = asyncio.Event()
        self.chunks: asyncio.Queue[bytes | None] = asyncio.Queue(_QUEUE)
        self.failure: NodeError | None = None
        self.closed = asyncio.Event()  # the reader is gone, or the job failed
        self.finished = False
        self.taken = False  # a node was handed it

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "offer": self.offer, **self.payload}

    def fail(self, why: str) -> None:
        if self.failure is None and not self.finished:
            status, sentence = _SENTENCES[why]
            self.failure = NodeError(status, sentence.format(name=self.node_name,
                                                             offer=self.offer))
        self.closed.set()
        self.head_ready.set()
        self.acked.set()


class Hub:
    def __init__(self, core: Core):
        self.core = core
        self._lens = NodesLens()
        self._sync_lock = threading.Lock()
        # Touched only from the web process's event loop.
        self._jobs: dict[str, Job] = {}
        self._queues: dict[str, asyncio.Queue[Job]] = {}
        self._cut: set[str] = set()  # nodes removed while this process was running
        # Jobs a node was handed whose reader has since gone, by node. The node is
        # told at its next heartbeat, so a model does not go on writing to nobody.
        self._dropped: dict[str, set[str]] = {}

    # --- the roster -----------------------------------------------------------

    def roster(self) -> dict[str, dict[str, Any]]:
        return self.core.lenses.read(self._lens)["nodes"]

    def connected(self, name: str) -> tuple[bool, int | None]:
        seen = self.core.metrics.last_seen(f"node:{name}")
        within = self.core.settings.get("nodes.gone_after_s") * 1000
        return seen is not None and now_ms() - seen <= within, seen

    def describe(self) -> dict[str, Any]:
        """Every joined node as the owner's screen shows it."""
        b = brain(self.core)
        config = b.config()
        out = []
        for node in sorted(self.roster().values(), key=lambda n: n["joined_ms"]):
            ok, seen = self.connected(node["name"])
            sentence, blocked = protocol.skew(__version__, protocol.PROTOCOL, node["version"],
                                              node["protocol"], node["name"])
            models = []
            for model in node["models"]:
                pid = provider_id(node["name"], model)
                models.append({
                    "name": model, "provider_id": pid,
                    "roles": sorted(role for role, targets in config.roles.items()
                                    if any(t.provider_id == pid for t in targets)),
                })
            out.append({
                "id": node["id"], "name": node["name"], "joined_ms": node["joined_ms"],
                "version": node["version"], "offers": node["offers"], "models": models,
                "browsers": node["browsers"], "connected": ok, "last_seen_ms": seen,
                "skew": sentence, "blocked": blocked,
            })
        return {"nodes": out, "hub_version": __version__, "protocol": protocol.PROTOCOL}

    def joined(self, node_id: str, name: str, version: str | None) -> None:
        self.core.tape.print("node.joined", {"node_id": node_id, "name": name,
                                             "version": version},
                             actor=f"node:{name}", subject=f"node:{node_id}")

    def heartbeat(self, node_id: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
        """A node says it is alive, which version it runs and what it offers.
        Raises ValueError with a sentence for a report that cannot be used."""
        version = body.get("version")
        number = body.get("protocol")
        if (not isinstance(version, str) or not protocol.VERSION.fullmatch(version)
                or isinstance(number, bool) or not isinstance(number, int)):
            raise ValueError("A node must say which version and protocol it runs.")
        models = protocol.offer_names(body.get("models", []), "models")
        browsers = protocol.offer_names(body.get("browsers", []), "browsers")
        offers = [kind for kind, names in (("models", models), ("browser", browsers)) if names]
        self.core.metrics.beat(f"node:{name}")
        now = self.roster().get(node_id)
        said = {"version": version, "protocol": number, "offers": offers, "models": models,
                "browsers": browsers}
        if now is not None and any(now[field] != value for field, value in said.items()):
            self.core.tape.print("node.offered", {"node_id": node_id, "name": name, **said},
                                 actor=f"node:{name}", subject=f"node:{node_id}")
            self.sync_providers(actor=f"node:{name}")
        _, blocked = protocol.skew(__version__, protocol.PROTOCOL, version, number, name)
        return {"ok": True, "name": name, "hub_version": __version__,
                "protocol": protocol.PROTOCOL, "blocked": blocked,
                "cancelled": sorted(self._dropped.pop(node_id, ())),
                "heartbeat_s": self.core.settings.get("nodes.heartbeat_s"), "poll_s": POLL_S}

    def remove(self, node_id: str, *, actor: str, reason: str) -> dict[str, Any] | None:
        """Revoke a node's token at once, then record that it left and take its
        models out of the roles that used them. None if there is no such node."""
        node = self.roster().get(node_id)
        if not self.core.auth.revoke_node(node_id):
            return None
        name = node["name"] if node else next(
            (n["name"] for n in self.core.auth.list_nodes() if n["id"] == node_id), "node")
        self.core.tape.print("node.left", {"node_id": node_id, "name": name, "reason": reason},
                             actor=actor, subject=f"node:{node_id}")
        changed = self.sync_providers(actor=actor)
        self.core.metrics.forget(f"node:{name}")
        return {"name": name, "roles_changed": changed}

    # --- providers ------------------------------------------------------------

    def relay_key(self) -> str:
        """The key this install's own processes use to call the relay. It opens
        the relay and nothing else, and never leaves this machine."""
        key = self.core.secrets.get(RELAY_SECRET)
        if not key:
            key = RELAY_KEY_PREFIX + secrets.token_urlsafe(32)
            self.core.secrets.set(RELAY_SECRET, key)
        return key

    def relay_base(self) -> str:
        host = self.core.config.host
        if host in ("", "0.0.0.0", "::"):  # noqa: S104 - reading the setting, not binding
            host = "127.0.0.1"
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.core.config.port}{protocol.RELAY}"

    @staticmethod
    def _ours(provider: Provider) -> bool:
        return (provider.type == "openai_compatible"
                and f"{protocol.RELAY}/" in (provider.base_url or ""))

    def sync_providers(self, *, actor: str = "system") -> list[str]:
        """Make the model providers match what the joined nodes offer: add one
        for each offered model server, correct one whose address is stale (the
        port changed), and remove those whose node or offer is gone, taking them
        out of any role first. Returns the roles that were changed."""
        with self._sync_lock:
            b = brain(self.core)
            config = b.config()
            base = self.relay_base()
            want: dict[str, tuple[str, str]] = {}
            for node in self.roster().values():
                for model in node["models"]:
                    want[provider_id(node["name"], model)] = (
                        f"{model} on {node['name']}", f"{base}/{node['id']}/{model}")
            key = self.relay_key() if want else None
            for pid, (label, url) in want.items():
                now = config.providers.get(pid)
                if now is not None and not self._ours(now):
                    log.warning("the provider id %s is taken by a provider that is not a "
                                "node's; rename it to see this node's model", pid)
                    continue
                if now is None or now.base_url != url or not now.is_local:
                    b.save_provider({
                        "id": pid, "type": "openai_compatible", "base_url": url, "local": True,
                        "label": now.label if now else label,
                        "enabled": now.enabled if now else True,
                        "options": dict(now.options) if now else {},
                    }, actor=actor)
                if self.core.secrets.get(secret_name(pid)) != key:
                    b.set_key(pid, key)
            gone = {p.id for p in config.providers.values()
                    if self._ours(p) and p.id not in want}
            changed = roles_without(config.roles, gone)
            for role, rest in changed.items():
                try:
                    b.set_role(role, rest[0] if rest else None, rest[1:], actor=actor)
                except Invalid:  # what is left cannot be used either
                    b.set_role(role, None, (), actor=actor)
            for pid in gone:
                b.remove_provider(pid, actor=actor)
            return sorted(changed)

    # --- jobs: the reader's side ------------------------------------------------

    def _check(self, node_id: str, kind: str, offer: str) -> str:
        """The node's name, if it can be given this job now. Otherwise NodeError."""
        node = self.roster().get(node_id)
        if node is None or not self.core.auth.node_live(node_id):
            raise NodeError(*_SENTENCES["unknown"])
        name = node["name"]
        sentence, blocked = protocol.skew(__version__, protocol.PROTOCOL, node["version"],
                                          node["protocol"], name)
        if blocked and sentence:
            raise NodeError(503, sentence)
        if not self.connected(name)[0]:
            status, text = _SENTENCES["not_connected"]
            raise NodeError(status, text.format(name=name))
        offered = node["models"] if kind == "model" else node["browsers"]
        if offer not in offered:
            status, text = _SENTENCES["not_offered"]
            raise NodeError(status, text.format(name=name, offer=offer))
        return name

    async def submit(self, node_id: str, kind: str, offer: str,
                     payload: dict[str, Any]) -> Job:
        """Queue a job for a node. Refused at once, with a sentence, if the node
        is gone, out of reach, on a version that cannot be worked with, or no
        longer offers this."""
        name = await asyncio.to_thread(self._check, node_id, kind, offer)
        job = Job(node_id, name, kind, offer, payload)
        self._jobs[job.id] = job
        self._queues.setdefault(node_id, asyncio.Queue()).put_nowait(job)
        return job

    async def _look(self, job: Job, stopped: Callable[[], Awaitable[bool]] | None) -> None:
        """Fail a waiting job whose node stopped reporting in, and close one
        whose reader has gone."""
        if stopped is not None and await stopped():
            self.release(job)
            raise NodeError(499, "The request was dropped.")
        ok, _ = await asyncio.to_thread(self.connected, job.node_name)
        if not ok:
            job.fail("went_away")

    async def head(self, job: Job, stopped: Callable[[], Awaitable[bool]] | None = None
                   ) -> tuple[int, str]:
        """Wait for the node to take the job and start answering. Returns the
        status and content type its answer starts with."""
        get = self.core.settings.get
        pickup, quiet = await asyncio.to_thread(
            lambda: (get("nodes.pickup_timeout_s"), get("models.read_timeout_s")))
        started = looked = time.monotonic()
        while not job.head_ready.is_set():
            now = time.monotonic()
            if not job.acked.is_set() and now - started > pickup:
                job.fail("pickup")
            elif now - started > pickup + quiet:
                job.fail("quiet")
            elif now - looked > _LOOK_S:
                looked = now
                await self._look(job, stopped)
            else:
                try:
                    await asyncio.wait_for(job.head_ready.wait(), _SLICE_S)
                except TimeoutError:
                    pass
        if job.failure is not None or job.head is None:
            self.release(job)
            raise job.failure or NodeError(*_SENTENCES["unknown"])
        return job.head

    async def chunks(self, job: Job, stopped: Callable[[], Awaitable[bool]] | None = None
                     ) -> AsyncIterator[bytes]:
        """The body of the answer as it arrives. Raises NodeError if the node
        goes away, is removed or goes quiet before the end."""
        quiet = await asyncio.to_thread(self.core.settings.get, "models.read_timeout_s")
        last = looked = time.monotonic()
        try:
            while True:
                try:
                    item = job.chunks.get_nowait()
                except asyncio.QueueEmpty:
                    if job.failure is not None:
                        raise job.failure from None
                    now = time.monotonic()
                    if now - last > quiet:
                        job.fail("quiet")
                    elif now - looked > _LOOK_S:
                        looked = now
                        await self._look(job, stopped)
                    try:
                        item = await asyncio.wait_for(job.chunks.get(), _SLICE_S)
                    except TimeoutError:
                        continue
                last = time.monotonic()
                if item is None:
                    return
                yield item
        finally:
            self.release(job)

    async def body(self, job: Job, stopped: Callable[[], Awaitable[bool]] | None = None
                   ) -> bytes:
        """An answer that is not a stream, whole."""
        parts, size = [], 0
        async for chunk in self.chunks(job, stopped):
            size += len(chunk)
            if size > MAX_BODY:
                job.fail("too_big")
                raise job.failure or NodeError(*_SENTENCES["too_big"])
            parts.append(chunk)
        return b"".join(parts)

    async def call(self, node_id: str, kind: str, offer: str,
                   payload: dict[str, Any]) -> tuple[int, str, bytes]:
        """Ask a node to do one job and wait for the whole answer: (status,
        content type, body). The seam a later package uses for a kind of its own."""
        job = await self.submit(node_id, kind, offer, payload)
        status, content_type = await self.head(job)
        return status, content_type, await self.body(job)

    def release(self, job: Job) -> None:
        """The reader is done with this job, or gave up on it."""
        job.closed.set()
        if self._jobs.pop(job.id, None) is not None and job.taken and not job.finished:
            self._dropped.setdefault(job.node_id, set()).add(job.id)

    def cut_off(self, node_id: str) -> None:
        """A node was removed: end its request for work and fail what it was doing."""
        self._cut.add(node_id)
        for job in [j for j in self._jobs.values() if j.node_id == node_id]:
            job.fail("removed")

    # --- jobs: the node's side --------------------------------------------------

    async def next_job(self, node_id: str, wait_s: float) -> Job | None:
        """The next job for this node, waiting up to ``wait_s`` for one."""
        queue = self._queues.setdefault(node_id, asyncio.Queue())
        deadline = time.monotonic() + wait_s
        while node_id not in self._cut:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                job = await asyncio.wait_for(queue.get(), min(left, 0.5))
            except TimeoutError:
                continue
            if not job.closed.is_set():  # its reader may have given up meanwhile
                job.taken = True
                return job
        raise NodeError(401, "This machine was removed from the hub.")

    def job_for(self, node_id: str, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        return job if job is not None and job.node_id == node_id else None

    async def _put(self, job: Job, item: bytes | None) -> bool:
        while not job.closed.is_set():
            try:
                await asyncio.wait_for(job.chunks.put(item), _SLICE_S)
                return True
            except TimeoutError:
                continue
        return False

    async def receive(self, job: Job, stream: AsyncIterator[bytes]) -> bool:
        """Take a node's reply to a job, frame by frame, as fast as its reader
        takes it and no faster. False if the reader had already gone, which
        tells the node to stop."""
        reader = protocol.FrameReader()
        job.acked.set()
        pieces = stream.__aiter__()
        gone = asyncio.ensure_future(job.closed.wait())
        try:
            while True:
                # Wait for the next piece, or for the reader to give up: a node
                # whose model is still thinking sends nothing for a long time.
                coming = asyncio.ensure_future(pieces.__anext__())
                await asyncio.wait({coming, gone}, return_when=asyncio.FIRST_COMPLETED)
                if not coming.done():
                    coming.cancel()
                    return False
                try:
                    data = coming.result()
                except StopAsyncIteration:
                    break
                for kind, payload in reader.feed(data):
                    if job.closed.is_set():
                        return False
                    if kind == protocol.HEAD and job.head is None:
                        said = json.loads(payload)
                        status, content_type = said["status"], said.get("content_type")
                        if (isinstance(status, bool) or not isinstance(status, int)
                                or not 100 <= status <= 599):
                            raise ValueError("status")
                        if content_type not in ("application/json", "text/event-stream"):
                            content_type = "application/json"
                        job.head = (status, content_type)
                        job.head_ready.set()
                    elif kind == protocol.DATA and job.head is not None:
                        if not await self._put(job, payload):
                            return False
                    elif kind == protocol.ERROR:
                        why = json.loads(payload).get("error")
                        job.fail(why if why in protocol.ERRORS else "bad_reply")
                        return True
                    elif kind == protocol.END and job.head is not None:
                        if not await self._put(job, None):
                            return False
                        job.finished = True
                        return True
                    else:
                        raise ValueError("order")
        except ClientDisconnect:
            pass
        except (ValueError, KeyError, TypeError, AttributeError):
            job.fail("bad_reply")
            return True
        finally:
            gone.cancel()
        if job.closed.is_set():
            return False
        # The upload ended without saying it was done: the node went away.
        job.fail("went_away")
        return True

    def close(self) -> None:
        for job in list(self._jobs.values()):
            job.closed.set()
        self._jobs.clear()
