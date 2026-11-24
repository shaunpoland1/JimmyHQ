"""Shared set-up for the MCP adapter tests: a pretend server over standard streams (a
short script run with this interpreter) and a pretend one over HTTP (a transport that
answers from memory). Nothing outside this machine is contacted."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from jimmyhq.hands import mcp, mcp_stdio
from jimmyhq.hands.provider import ProviderRecord, Wiring

SCRIPT = str(Path(__file__).with_name("hands_mcp_fake_server.py"))
PROVIDER = "tools"
TOKEN = "example-token-7f3a9c"
META = "io.modelcontextprotocol/"
ADDRESS = "https://mcp.example.com/mcp"


def run(coro):
    return asyncio.run(coro)


def tool(name: str, description: str = "", hints: dict | None = None, **props: dict) -> dict:
    out: dict[str, Any] = {
        "name": name, "description": description or f"Does {name}.",
        "inputSchema": {"type": "object", "properties": props, "required": sorted(props)}}
    if hints is not None:
        out["annotations"] = hints
    return out


TOOLS = [
    tool("echo", "Says back what it was given.", {"readOnlyHint": True},
         words={"type": "string", "description": "What to say back."}),
    tool("save", "Saves a record.", record={"type": "string"}),
    tool("wipe", "Removes every record.", {"destructiveHint": True}),
]
OTHERS = ["env", "pid", "fail", "leak", "leak_ok", "sleep", "exit", "big", "ask", "input",
          "mixed", "argv"]


def fast(monkeypatch):
    """No waiting between restarts, and a short wait for a silent server. A test file
    wraps this in a fixture; every server started is stopped afterwards."""
    monkeypatch.setattr(mcp_stdio, "BACKOFF_FIRST_S", 0.0)
    monkeypatch.setattr(mcp, "PROBE_WAIT_S", 0.3)
    yield
    mcp.close_all()


class Stdio:
    """A pretend server's files, and adapters wired to run it."""

    def __init__(self, folder: Path, **config: Any):
        self.folder = folder
        self.config_path = folder / "config.json"
        self.log_path = folder / "log.jsonl"
        self.starts_path = folder / "starts.jsonl"
        self.options: dict[str, Any] = {
            "command": sys.executable, "args": [SCRIPT], "env": ["FAKE_CONFIG", "EXAMPLE_TOKEN"]}
        self.saved = {"env_fake_config": str(self.config_path), "env_example_token": TOKEN}
        self.set(tools=[*TOOLS, *[tool(n) for n in OTHERS]], **config)

    def set(self, **config: Any) -> None:
        held = json.loads(self.config_path.read_text("utf-8")) if self.config_path.exists() \
            else {"log": str(self.log_path), "starts": str(self.starts_path)}
        self.config_path.write_text(json.dumps(held | config), "utf-8")

    def record(self, **options: Any) -> ProviderRecord:
        return ProviderRecord(id=PROVIDER, type="mcp", label="Tools",
                              options=self.options | options)

    def adapter(self, **options: Any) -> mcp.McpProvider:
        return mcp.make(self.record(**options), Wiring(key=None, secret=self.saved.get))

    def received(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text("utf-8").splitlines()]

    def methods(self) -> list[str]:
        return [m["method"] for m in self.received() if "method" in m]

    def starts(self) -> int:
        if not self.starts_path.exists():
            return 0
        return len(self.starts_path.read_text("utf-8").splitlines())


def call(adapter: mcp.McpProvider, name: str, arguments: dict | None = None):
    account = run(adapter.accounts())[0]
    return run(adapter.run(account, name, arguments or {}, key="k"))


def names(adapter: mcp.McpProvider) -> list[str]:
    account = run(adapter.accounts())[0]
    return [a.name for a in run(adapter.actions(account))]


class FakeHttp:
    """A pretend MCP server behind an address. ``era`` is "modern" or "legacy"."""

    def __init__(self, era: str = "modern", tools: list[dict] | None = None):
        self.era = era
        self.tools = tools if tools is not None else list(TOOLS)
        self.requests: list[tuple[dict, dict]] = []  # (headers, body) of every POST
        self.stream = False  # answer a call with an event stream
        self.status: int | None = None  # answer everything with this status
        self.forget_once = False  # forget the session: 404 once
        self.sessions = 0
        self.reply: Any = None  # what a tools/call returns; None for an echo
        self.padding = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def bodies(self, method: str) -> list[tuple[dict, dict]]:
        return [(h, b) for h, b in self.requests if b.get("method") == method]

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911, PLR0912
        body = json.loads(request.content)
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.requests.append((headers, body))
        if self.status is not None:
            return httpx.Response(self.status, text="no")
        method, rid = body.get("method"), body.get("id")
        params = body.get("params") or {}

        def answer(result: Any = None, error: dict | None = None, status: int = 200,
                   extra: dict | None = None) -> httpx.Response:
            message = {"jsonrpc": "2.0", "id": rid}
            message |= {"error": error} if error else {"result": result}
            if self.stream and method == "tools/call" and not error:
                progress = {"jsonrpc": "2.0", "method": "notifications/progress",
                            "params": {"progress": 1}}
                ask = {"jsonrpc": "2.0", "id": "ask-1", "method": "sampling/createMessage",
                       "params": {}}
                text = (": keep-alive\r\n\r\n"
                        f"event: message\r\ndata: {json.dumps(progress)}\r\n\r\n"
                        f"data: {json.dumps(ask)}\r\n\r\n"
                        f"id: 7\r\ndata: {json.dumps(message)}\r\n\r\n")
                return httpx.Response(200, content=text.encode("utf-8"), headers={
                    "content-type": "text/event-stream", **(extra or {})})
            return httpx.Response(status, json=message, headers=extra or {})

        if rid is None:
            return httpx.Response(202)
        if self.era == "legacy":
            if method == "initialize":
                self.sessions += 1
                return answer({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                               "serverInfo": {"name": "fake", "version": "1"}},
                              extra={"mcp-session-id": f"session-{self.sessions}"})
            if headers.get("mcp-session-id") != f"session-{self.sessions}" or not self.sessions:
                return answer(error={"code": -32000, "message": "Bad Request: no session"},
                              status=400)
            if self.forget_once:
                self.forget_once = False
                return httpx.Response(404, text="no such session")
        else:
            if method == "initialize":
                return answer(error={"code": -32601, "message": "Method not found"}, status=404)
            meta = params.get("_meta") or {}
            if (headers.get("mcp-protocol-version") != meta.get(META + "protocolVersion")
                    or headers.get("mcp-method") != method):
                return answer(error={"code": -32020, "message": "Header mismatch"}, status=400)
            if method == "server/discover":
                return answer({"resultType": "complete", "supportedVersions": [mcp.MODERN],
                               "capabilities": {"tools": {}}})
        if method == "tools/list":
            return answer({"tools": self.tools})
        if method == "tools/call":
            if self.reply is not None:
                return answer(self.reply)
            said = json.dumps({"arguments": params.get("arguments")}) + "x" * self.padding
            return answer({"content": [{"type": "text", "text": said}]})
        return answer(error={"code": -32601, "message": "Method not found"}, status=404)


def http_adapter(fake: FakeHttp, *, url: str = ADDRESS, key: str | None = None,
                 saved: dict[str, str] | None = None, **options: Any) -> mcp.McpProvider:
    record = ProviderRecord(id=PROVIDER, type="mcp", label="Remote", base_url=url,
                            options=options)
    return mcp.make(record, Wiring(key=key, secret=(saved or {}).get, http=fake.transport))
