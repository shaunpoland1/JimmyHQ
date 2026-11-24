"""Shared pieces for the nodes tests: a hub and a node in one test, each a real
server or client on a loopback port chosen by the system, plus a small fake of
an OpenAI-compatible model server for the node's side. Nothing leaves the machine."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from jimmyhq import config
from jimmyhq.core import open_core
from jimmyhq.nodes import agent
from jimmyhq.web.app import COOKIE, create_app

PIECES = ("Hello", " from", " the", " node")
TEXT = "".join(PIECES)
PASSWORD = "a long enough password"


def until(check, seconds: float = 8.0, what: str = "the condition"):
    """Wait for something another thread is doing. Returns what ``check`` returned."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class Served:
    """An app answering on a loopback port, in a thread, until ``stop``."""

    def __init__(self, app, port: int = 0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(
            app, log_config=None, timeout_graceful_shutdown=1))
        self.thread = threading.Thread(
            target=lambda: self.server.run(sockets=[self.sock]), daemon=True)

    def start(self) -> Served:
        self.thread.start()
        until(lambda: self.server.started, what="the server to start")
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(8)
        self.sock.close()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HubUnderTest:
    """A hub: its Core, its web process on a real port, and the owner signed in."""

    def __init__(self, data, port: int | None = None):
        self.data = data
        data.mkdir(parents=True, exist_ok=True)
        self.port = port or free_port()
        self.cfg = config.Config(data=data, host="127.0.0.1", port=self.port,
                                 allowed_hosts=("testserver",), remote_setup=True)
        self.core = open_core(self.cfg)
        if not self.core.auth.owner_exists():
            self.core.auth.create_owner(PASSWORD)
        self.core.settings.set("nodes.heartbeat_s", 0.5, actor="owner")
        self.served = Served(create_app(self.core), self.port).start()
        self.url = self.served.url
        self.owner = httpx.Client(
            base_url=self.url, cookies={COOKIE: self.core.auth.create_session(1)}, timeout=10)

    def code(self) -> str:
        return self.owner.post("/api/nodes/join-codes").json()["code"]

    def nodes(self) -> list[dict]:
        return self.owner.get("/api/nodes").json()["nodes"]

    def stop(self) -> None:
        self.owner.close()
        self.served.stop()
        self.core.close()


class FakeModels:
    """An OpenAI-compatible model server. ``hold`` makes a streamed answer stop
    after its first piece until ``release`` is set, so a test can act mid-reply."""

    def __init__(self):
        self.asked: list[tuple[str, str, dict | None]] = []
        self.hold = False
        self.release = threading.Event()
        self.first_sent = threading.Event()
        self.app = Starlette(routes=[
            Route("/v1/models", self._models),
            Route("/v1/chat/completions", self._chat, methods=["POST"]),
            Route("/v1/embeddings", self._embed, methods=["POST"]),
        ])

    async def _models(self, request):
        self.asked.append(("GET", "/models", None))
        return JSONResponse({"object": "list", "data": [{"id": "model-a"}, {"id": "model-b"}]})

    async def _embed(self, request):
        body = await request.json()
        self.asked.append(("POST", "/embeddings", body))
        return JSONResponse({
            "data": [{"index": i, "embedding": [0.5, float(i)]}
                     for i, _ in enumerate(body["input"])],
            "usage": {"prompt_tokens": 3}})

    async def _chat(self, request):
        body = await request.json()
        self.asked.append(("POST", "/chat/completions", body))
        usage = {"prompt_tokens": 7, "completion_tokens": 4}
        if not body.get("stream"):
            return JSONResponse({"choices": [{"message": {"role": "assistant", "content": TEXT},
                                              "finish_reason": "stop"}], "usage": usage})

        async def events():
            for index, piece in enumerate(PIECES):
                yield f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n"
                if index == 0:
                    self.first_sent.set()
                    while self.hold and not self.release.is_set():
                        await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")


class NodeUnderTest:
    """A node: its own data folder, and ``jimmyhq node run`` going in a thread."""

    def __init__(self, data, **agent_options):
        data.mkdir(parents=True, exist_ok=True)
        self.store = agent.NodeStore(data)
        self.options = {"delay": lambda failures: 0.05, "poll_s": 1.0, **agent_options}
        self.agent: agent.NodeAgent | None = None
        self.exit: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    def join(self, hub_url: str, code: str, name: str = "example-box", **options) -> str:
        return asyncio.run(agent.join(self.store, hub_url, code, name, **options))

    def start(self) -> NodeUnderTest:
        self.agent = agent.NodeAgent(self.store, **self.options)
        self.exit = None

        async def main():
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            self.exit = await self.agent.run(self._stop)

        self._thread = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
        self._thread.start()
        return self

    def connected(self) -> NodeUnderTest:
        until(lambda: self.agent.state == "connected", what="the node to connect")
        return self

    def stop(self) -> None:
        if self._thread is None:
            return
        if self._loop is not None and self._stop is not None and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(8)
        self._thread = None
