"""The node's side: what runs on the second machine.

A node keeps two small things in its own data folder and nothing of the hub's:
``node.json`` (the hub's address, the name it joined under, the model servers
it offers) and, in the secret store, its node token. ``NodeAgent.run`` is what
``jimmyhq node run`` runs: it reports in, asks the hub for work over a connection
it opens itself, and does each job against a model server on this machine.
Nothing here listens on the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import socket
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from jimmyhq import __version__
from jimmyhq.auth import NODE_NAME
from jimmyhq.models import validate
from jimmyhq.nodes import protocol
from jimmyhq.nodes.protocol import DATA, END, ERROR, HEAD, JOIN, LINK, frame
from jimmyhq.secretstore import SecretStore

log = logging.getLogger("jimmyhq.node")

TOKEN = "node.token"  # noqa: S105 - the name it is kept under, not the token
_POLL_S = 20
_AT_ONCE = 4  # jobs one node does at the same time


class NodeProblem(Exception):
    """Something the person at the node has to put right. The message says what."""


class _Removed(Exception):
    pass


class _LinkLost(Exception):
    pass


def default_name() -> str:
    made = re.sub(r"[^a-z0-9]+", "-", socket.gethostname().split(".")[0].lower()).strip("-")
    return made[:16].rstrip("-") or "node"


class NodeStore:
    """The node's own configuration and token."""

    def __init__(self, data: Path):
        self.path = Path(data) / "node.json"
        self._secrets = SecretStore(Path(data) / "secrets.json")

    def read(self) -> dict[str, Any]:
        conf: dict[str, Any] = {"hub": None, "node_id": None, "name": None,
                                "plain_http_ok": False, "models": {}}
        with contextlib.suppress(OSError, ValueError):
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                conf.update({k: v for k, v in saved.items() if k in conf})
        if not isinstance(conf["models"], dict):
            conf["models"] = {}
        return conf

    def write(self, conf: dict[str, Any]) -> None:
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(conf, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def token(self) -> str | None:
        return self._secrets.get(TOKEN)

    def keep_token(self, token: str) -> None:
        self._secrets.set(TOKEN, token)

    def forget_token(self) -> None:
        self._secrets.delete(TOKEN)


def offer_model(store: NodeStore, name: str, url: str) -> str:
    """Offer a model server on this machine to the hub under a short name."""
    if not protocol.OFFER_NAME.fullmatch(name):
        raise NodeProblem("Nothing was changed: a model server's name is 1 to 16 characters, "
                          "lower-case letters, digits, - and _. Choose a name like that.")
    try:
        url = validate.base_url(url)
    except validate.Invalid as exc:
        raise NodeProblem(f"Nothing was changed. {exc}") from None
    conf = store.read()
    if name not in conf["models"] and len(conf["models"]) >= protocol.MAX_OFFERED:
        raise NodeProblem(f"Nothing was changed: a node offers at most {protocol.MAX_OFFERED} "
                          "model servers. Remove one first.")
    conf["models"][name] = url
    store.write(conf)
    return (f"This machine now offers the model server at {url} as {name}. A running "
            "`jimmyhq node run` tells the hub within a few seconds.")


def remove_model(store: NodeStore, name: str) -> str:
    conf = store.read()
    if conf["models"].pop(name, None) is None:
        raise NodeProblem(f"Nothing was changed: this machine does not offer anything called "
                          f"{name}. `jimmyhq node status` lists what it offers.")
    store.write(conf)
    return (f"This machine no longer offers {name}. On the hub, any role that used it moves "
            "to its next choice.")


def _detail(response: httpx.Response) -> str:
    with contextlib.suppress(ValueError):
        said = response.json().get("detail")
        if isinstance(said, str):
            return said
    return f"The hub answered with status {response.status_code}."


def _client(hub: str, token: str | None, version: str, number: int,
            transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    headers = {protocol.VERSION_HEADER: version, protocol.PROTOCOL_HEADER: str(number)}
    if token:
        headers["authorization"] = f"Bearer {token}"
    # Redirects are never followed: the token must not be carried to another host.
    return httpx.AsyncClient(base_url=hub, headers=headers, transport=transport,
                             follow_redirects=False,
                             timeout=httpx.Timeout(15, connect=10))


async def join(store: NodeStore, hub: str, code: str, name: str | None = None, *,
               accept_plain: bool = False, transport: httpx.AsyncBaseTransport | None = None,
               resolver: Callable[[str], Iterable[str]] = protocol.resolve,
               version: str = __version__) -> str:
    """Exchange a join code for this machine's node token and keep it."""
    try:
        hub = protocol.hub_address(hub)
    except ValueError as exc:
        raise NodeProblem(f"Nothing was joined. {exc}") from None
    name = (name or default_name()).strip().lower()
    if not NODE_NAME.fullmatch(name):
        raise NodeProblem("Nothing was joined: a machine's name is 1 to 16 characters, "
                          "lower-case letters, digits and -. Give one with --name.")
    if store.token:
        raise NodeProblem("Nothing was joined: this machine has already joined a hub. Run "
                          "`jimmyhq node leave` first if you mean to join another.")
    refusal = protocol.plain_http_refusal(hub, accept_plain, resolver)
    if refusal:
        raise NodeProblem(refusal)
    try:
        async with _client(hub, None, version, protocol.PROTOCOL, transport) as client:
            response = await client.post(JOIN, json={
                "code": code, "name": name, "version": version,
                "protocol": protocol.PROTOCOL})
    except httpx.HTTPError:
        raise NodeProblem(
            f"Nothing was joined: the hub at {hub} could not be reached. Check the address, "
            "that JimmyHQ is running there, and that it listens on an address this machine "
            "can reach (docs/nodes.md).") from None
    if response.status_code == 404:
        raise NodeProblem("Nothing was joined: the hub has no join code waiting. Make one "
                          "under Settings, Nodes on the hub, then run this again.")
    if response.status_code != 200:
        raise NodeProblem(_detail(response))
    said = response.json()
    store.keep_token(said["token"])
    conf = store.read()
    conf.update(hub=hub, node_id=said["node_id"], name=said["name"],
                plain_http_ok=bool(accept_plain))
    store.write(conf)
    return (f"This machine has joined the hub at {hub} as {said['name']}. Start it with "
            "`jimmyhq node run`, or `jimmyhq service install --node` to start it at login.")


async def leave(store: NodeStore, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Tell the hub this machine is leaving, and forget the token either way."""
    conf, token = store.read(), store.token
    if not token or not conf["hub"]:
        raise NodeProblem("Nothing was changed: this machine has not joined a hub.")
    told = False
    with contextlib.suppress(httpx.HTTPError):
        async with _client(conf["hub"], token, __version__, protocol.PROTOCOL,
                           transport) as client:
            told = (await client.post(f"{LINK}/leave")).status_code == 200
    store.forget_token()
    conf.update(hub=None, node_id=None, name=None, plain_http_ok=False)
    store.write(conf)
    if told:
        return "This machine has left the hub, and the hub has been told."
    return ("This machine has forgotten the hub, but the hub could not be told. Remove this "
            "machine under Settings, Nodes on the hub so its token stops working there.")


def status(store: NodeStore) -> str:
    conf = store.read()
    if not conf["hub"] or not store.token:
        lines = ["This machine has not joined a hub."]
    else:
        lines = [f"Joined the hub at {conf['hub']} as {conf['name']}."]
    if conf["models"]:
        lines += ["Model servers offered:"] + [
            f"  {name}  {url}" for name, url in sorted(conf["models"].items())]
    else:
        lines.append("No model servers offered. Add one with `jimmyhq node offer model "
                     "--name NAME --url http://127.0.0.1:PORT/v1`.")
    return "\n".join(lines)


class NodeAgent:
    """Keeps this machine connected to its hub and does the jobs it is given."""

    def __init__(self, store: NodeStore, *, version: str = __version__,
                 protocol_number: int = protocol.PROTOCOL,
                 delay: Callable[[int], float] = protocol.reconnect_delay,
                 hub_transport: httpx.AsyncBaseTransport | None = None,
                 model_transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Callable[[str], Iterable[str]] = protocol.resolve,
                 poll_s: float = _POLL_S):
        self.store = store
        self.version = version
        self.protocol_number = protocol_number
        self._delay = delay
        self._hub_transport = hub_transport
        self._model_transport = model_transport
        self._resolver = resolver
        self._poll_s = poll_s
        self.state = "starting"  # then "connected", "waiting" or "stopped"
        self.failures = 0  # failed tries in a row
        self.connects = 0  # times the hub was reached after not being
        self.waits: list[float] = []  # the seconds waited before each retry
        self.said: list[str] = []  # what the person at this machine was told
        self.skew: str | None = None
        self.blocked = False
        self._running: dict[str, asyncio.Task] = {}
        # A job kind and what does it. Each takes the job and yields the frames
        # of the reply. A later package adds its own kind here ("browser").
        self.handlers: dict[str, Callable[[dict[str, Any]], AsyncIterator[bytes]]] = {
            "model": self._model}

    def _say(self, sentence: str, *, problem: bool = False) -> None:
        if not self.said or self.said[-1] != sentence:
            self.said.append(sentence)
            (log.warning if problem else log.info)(sentence)

    async def run(self, stop: asyncio.Event) -> int:
        conf, token = self.store.read(), self.store.token
        if not conf["hub"] or not token:
            self._say("This machine has not joined a hub. Run `jimmyhq node join <hub "
                      "address> <code>` first.", problem=True)
            self.state = "stopped"
            return 2
        hub = conf["hub"]
        try:
            while not stop.is_set():
                refusal = protocol.plain_http_refusal(hub, conf["plain_http_ok"],
                                                      self._resolver)
                try:
                    if refusal:
                        raise _LinkLost(refusal)
                    async with _client(hub, token, self.version, self.protocol_number,
                                       self._hub_transport) as link:
                        await self._session(link, stop)
                except _Removed:
                    self.store.forget_token()
                    self._say("This machine was removed from the hub, so it has stopped and "
                              "forgotten its token. To join again, make a new code under "
                              "Settings, Nodes on the hub.", problem=True)
                    return 3
                except _LinkLost as exc:
                    self._say(str(exc), problem=True)
                except (httpx.HTTPError, OSError):
                    self._say(f"The hub at {hub} cannot be reached. This machine keeps "
                              "trying.", problem=True)
                if stop.is_set():
                    break
                self.failures += 1
                self.state = "waiting"
                wait = self._delay(self.failures)
                self.waits.append(wait)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), wait)
        finally:
            self.state = "stopped"
        return 0

    def _check(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise _Removed
        if response.status_code >= 400:
            raise _LinkLost(_detail(response))

    async def _session(self, link: httpx.AsyncClient, stop: asyncio.Event) -> None:
        beat = await self._beat(link)
        if self.failures or not self.connects:
            self._say(f"Connected to the hub as {beat.get('name')}.")
        self.failures = 0
        self.connects += 1
        self.state = "connected"
        tasks = [asyncio.create_task(self._beats(link, float(beat.get("heartbeat_s", 10)))),
                 asyncio.create_task(self._work(link)),
                 asyncio.create_task(stop.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (*tasks, *self._running.values()):
                task.cancel()
            await asyncio.gather(*tasks, *self._running.values(), return_exceptions=True)
            self._running.clear()
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                raise task.exception()  # type: ignore[misc]

    async def _beat(self, link: httpx.AsyncClient) -> dict[str, Any]:
        conf = await asyncio.to_thread(self.store.read)  # offers may have changed
        response = await link.post(f"{LINK}/heartbeat", json={
            "version": self.version, "protocol": self.protocol_number,
            "models": sorted(conf["models"]), "browsers": []})
        self._check(response)
        said = response.json()
        sentence, self.blocked = protocol.skew_on_node(
            str(said.get("hub_version")), said.get("protocol"), self.version,
            self.protocol_number)
        if sentence and sentence != self.skew:
            self._say(sentence, problem=True)
        self.skew = sentence
        for job_id in said.get("cancelled") or []:
            task = self._running.get(job_id)
            if task is not None:
                task.cancel()  # nobody is waiting for that answer any more
        return said

    async def _beats(self, link: httpx.AsyncClient, every: float) -> None:
        while True:
            await asyncio.sleep(every)
            await self._beat(link)

    async def _work(self, link: httpx.AsyncClient) -> None:
        free = asyncio.Semaphore(_AT_ONCE)
        while True:
            if self.blocked:  # versions that cannot work together: take no work
                await asyncio.sleep(1)
                continue
            await free.acquire()
            try:
                response = await link.get(
                    f"{LINK}/jobs", params={"wait": self._poll_s},
                    timeout=httpx.Timeout(self._poll_s + 15, connect=10))
                self._check(response)
                job = response.json() if response.status_code == 200 else None
            except BaseException:
                free.release()
                raise
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                free.release()
                continue
            task = asyncio.create_task(self._do(link, job))
            self._running[job["id"]] = task

            def finished(_: asyncio.Task, job_id: str = job["id"]) -> None:
                self._running.pop(job_id, None)
                free.release()

            task.add_done_callback(finished)

    async def _do(self, link: httpx.AsyncClient, job: dict[str, Any]) -> None:
        handler = self.handlers.get(str(job.get("kind")))
        frames = handler(job) if handler else _refuse("no_handler")
        try:
            # No overall time limit: a slow reader on the hub holds this upload
            # back on purpose. The heartbeat notices a hub that has gone.
            await link.post(f"{LINK}/jobs/{job['id']}/reply", content=frames,
                            timeout=httpx.Timeout(None, connect=10))
        except httpx.HTTPError:
            pass  # the hub went away or stopped listening; the heartbeat deals with it
        finally:
            with contextlib.suppress(Exception):
                await frames.aclose()  # type: ignore[attr-defined]

    async def _model(self, job: dict[str, Any]) -> AsyncIterator[bytes]:
        """Ask the model server kept under this offer's name, and pass its answer
        on as it comes. Only the configured address and the few known paths are
        ever asked, whatever the job says."""
        conf = await asyncio.to_thread(self.store.read)
        base = conf["models"].get(job.get("offer"))
        method, path = job.get("method"), job.get("path")
        if not isinstance(base, str):
            yield frame(ERROR, {"error": "no_offer"})
            return
        if not isinstance(path, str) or protocol.MODEL_PATHS.get(path) != method:
            yield frame(ERROR, {"error": "bad_job"})
            return
        body = job.get("body") if method == "POST" else None
        started = False
        try:
            async with httpx.AsyncClient(
                    transport=self._model_transport, follow_redirects=False,
                    timeout=httpx.Timeout(None, connect=5)) as client, client.stream(
                    method, base.rstrip("/") + path,
                    content=body.encode() if isinstance(body, str) else None,
                    headers={"content-type": "application/json"}) as answer:
                started = True
                kind = answer.headers.get("content-type", "").split(";")[0].strip().lower()
                yield frame(HEAD, {"status": answer.status_code, "content_type": kind})
                async for piece in answer.aiter_bytes():
                    for at in range(0, len(piece), protocol.CHUNK):
                        yield frame(DATA, piece[at:at + protocol.CHUNK])
            yield frame(END)
        except httpx.TimeoutException:
            yield frame(ERROR, {"error": "timeout"})
        except (httpx.HTTPError, httpx.InvalidURL):
            yield frame(ERROR, {"error": "broken" if started else "unreachable"})


async def _refuse(why: str) -> AsyncIterator[bytes]:
    yield frame(ERROR, {"error": why})
