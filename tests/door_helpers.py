"""Shared pieces for the door's tests: a door switched on, a named client, and ways to
speak the protocol to it without opening a port."""

from __future__ import annotations

import json
from typing import Any

from starlette.testclient import TestClient

from jimmyhq import door as door_module
from jimmyhq.door.listener import DoorApp
from jimmyhq.village import record

HOST = "http://127.0.0.1:8441"
META = "io.modelcontextprotocol/"
MODERN = "2026-07-28"
ALL_TOOLS = ("village_agents", "village_priorities", "village_messages",
             "village_send_message", "bench_cards", "bench_file_card", "tasks_list",
             "tasks_add", "tasks_complete", "memory_recall", "memory_propose")


def open_door(core):
    core.settings.set("door.enabled", True, actor="owner")
    return door_module.door(core)


def add_client(core, name="helper", tools=ALL_TOOLS, **options):
    """A live client with these tools. Returns (its record, its token)."""
    d = door_module.door(core)
    client, token = d.add_client(name, "A test client.", by="owner")
    client = d.change_client(client["id"], by="owner", tools_={t: None for t in tools},
                             options=options or None)
    return client, token


def refreshed(core, client):
    return door_module.door(core).client(client["id"])


def http(core) -> TestClient:
    return TestClient(DoorApp(door_module.door(core)), base_url=HOST)


def modern(method: str, params: dict | None = None, rid: Any = 1) -> tuple[dict, dict]:
    """A request in the newest revision: (headers, body)."""
    params = dict(params or {})
    params["_meta"] = {META + "protocolVersion": MODERN,
                       META + "clientInfo": {"name": "test", "version": "0"},
                       META + "clientCapabilities": {}}
    headers = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method,
               "Accept": "application/json, text/event-stream"}
    if method == "tools/call":
        headers["Mcp-Name"] = params.get("name", "")
    return headers, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def ask(client: TestClient, token: str | None, method: str, params: dict | None = None,
        **extra_headers: str):
    headers, body = modern(method, params)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", json=body, headers=headers | extra_headers)


def call(core, client, tool: str, **args):
    """One tool call straight into the door, as the client now stands."""
    return door_module.door(core).call(refreshed(core, client), tool, args)


def receipts(core) -> list[dict]:
    return [e.payload for e in core.tape.after(0, kind="door.called", limit=500)]


def tape_text(core) -> str:
    return json.dumps([e.model_dump() for e in core.tape.after(0, limit=2000)])


def born(core, agent_id="ada", role="researcher", outside=True):
    """A living agent. ``outside`` gives it the capability to take outside messages."""
    record.born(core, agent_id, 7, role)
    if outside:
        record.configured(core, agent_id, {"role": role, "capabilities": ["outside"]})
    return agent_id
