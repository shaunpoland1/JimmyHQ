"""A pretend MCP server over standard streams. FOR TESTS ONLY.

It is run by the tests with the same interpreter, reads one JSON-RPC message per line and
answers on standard output. How it behaves is read from the JSON file named by the
environment variable FAKE_CONFIG before every request, so a test can change the server
while it runs. Every message it receives is appended to the file named by "log".
"""

from __future__ import annotations

import json
import os
import sys
import time

MODERN = "2026-07-28"
META = "io.modelcontextprotocol/"


def config() -> dict:
    with open(os.environ["FAKE_CONFIG"], encoding="utf-8") as handle:
        return json.load(handle)


def note(cfg: dict, key: str, value: object) -> None:
    if cfg.get(key):
        with open(cfg[key], "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value) + "\n")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def text(words: str, **more: object) -> dict:
    return {"content": [{"type": "text", "text": words}], **more}


def call(cfg: dict, name: str, args: dict) -> dict | None:  # noqa: C901, PLR0911, PLR0912
    if name == "echo":
        return text(json.dumps({"arguments": args}), structuredContent={"arguments": args})
    if name == "env":
        if args.get("name"):
            return text(os.environ.get(args["name"], ""))
        return text("", structuredContent={"names": sorted(os.environ)})
    if name == "save":
        return text("Saved.")
    if name == "argv":
        return text("", structuredContent={"argv": sys.argv[1:]})
    if name == "pid":
        return text(str(os.getpid()))
    if name == "fail":
        return text("The record could not be saved: it is locked.", isError=True)
    if name == "leak":
        return text("could not sign in with " + os.environ.get("EXAMPLE_TOKEN", ""),
                    isError=True)
    if name == "leak_ok":
        return text("signed in with " + os.environ.get("EXAMPLE_TOKEN", ""),
                    structuredContent={"used": os.environ.get("EXAMPLE_TOKEN", "")})
    if name == "sleep":
        time.sleep(float(args.get("seconds", 5)))
        return text("slept")
    if name == "exit":
        sys.stdout.flush()
        os._exit(3)
    if name == "big":
        sys.stdout.write("{\"padding\": \"" + "x" * int(args.get("bytes", 100000)) + "\"}\n")
        sys.stdout.flush()
        return text("after the big one")
    if name == "ask":
        send({"jsonrpc": "2.0", "id": "ask-1", "method": "sampling/createMessage",
              "params": {"messages": [], "maxTokens": 10}})
        send({"jsonrpc": "2.0", "id": "ask-2", "method": "ping"})
        got: dict = {}
        if not cfg.get("ask_and_go_on"):
            while len(got) < 2:
                line = sys.stdin.readline()
                if not line:
                    os._exit(0)
                answer = json.loads(line)
                note(cfg, "log", answer)
                if answer.get("id") in ("ask-1", "ask-2"):
                    got[answer["id"]] = answer
        refused = got.get("ask-1", {}).get("error", {}).get("code")
        return text(f"refused:{refused} ping:{'result' in got.get('ask-2', {})}")
    if name == "input":
        return {"resultType": "input_required", "inputRequests": {"who": {
            "method": "elicitation/create",
            "params": {"mode": "form", "message": "Who are you?", "requestedSchema": {}}}}}
    if name == "mixed":
        return {"content": [
            {"type": "text", "text": "A picture and a file."},
            {"type": "image", "data": "A" * 400, "mimeType": "image/png"},
            {"type": "resource", "resource": {"uri": "file:///notes.txt",
                                              "mimeType": "text/plain", "text": "x" * 50}},
            {"type": "resource_link", "uri": "file:///more.txt", "name": "more.txt"},
        ]}
    return None


def handle(cfg: dict, state: dict, message: dict) -> dict | None:  # noqa: C901, PLR0911, PLR0912
    """The result, or an error, for one request. None answers nothing."""
    method, params = message.get("method"), message.get("params") or {}
    era = cfg.get("era", "modern")
    meta = params.get("_meta") or {}
    modern_request = META + "protocolVersion" in meta
    if method == "initialize":
        if era == "modern":
            return {"error": {"code": -32601, "message": "Method not found"}}
        state["initialized"] = True
        return {"result": {"protocolVersion": cfg.get("legacy_version", "2025-11-25"),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fake", "version": "1"},
                           "instructions": cfg.get("instructions", "")}}
    if era == "legacy" and not state.get("initialized"):
        if cfg.get("refuses_version"):
            return {"error": {"code": -32022, "message": "Unsupported protocol version",
                              "data": {"supported": [cfg.get("legacy_version", "2025-11-25")],
                                       "requested": meta.get(META + "protocolVersion")}}}
        if cfg.get("silent_before_init"):
            return None
        return {"error": {"code": -32601, "message": "Method not found"}}
    if era == "modern":
        versions = cfg.get("versions", [MODERN])
        if not modern_request or META + "clientCapabilities" not in meta:
            return {"error": {"code": -32602, "message": "Missing request metadata"}}
        if meta[META + "protocolVersion"] not in versions:
            return {"error": {"code": -32022, "message": "Unsupported protocol version",
                              "data": {"supported": versions,
                                       "requested": meta[META + "protocolVersion"]}}}
    if method == "server/discover":
        return {"result": {"resultType": "complete",
                           "supportedVersions": cfg.get("versions", [MODERN]),
                           "capabilities": {"tools": {}},
                           "instructions": cfg.get("instructions", "")}}
    if method == "tools/list":
        tools, size = cfg.get("tools", []), int(cfg.get("page_size", 0))
        if cfg.get("loop_cursor"):
            return {"result": {"tools": tools, "nextCursor": "again"}}
        if not size:
            return {"result": {"tools": tools}}
        start = int(params.get("cursor") or 0)
        result: dict = {"tools": tools[start:start + size]}
        if start + size < len(tools):
            result["nextCursor"] = str(start + size)
        return {"result": result}
    if method == "tools/call":
        result = call(cfg, params.get("name"), params.get("arguments") or {})
        if result is None:
            return {"error": {"code": -32602, "message": f"Unknown tool: {params.get('name')}"}}
        if era == "modern":
            result.setdefault("resultType", "complete")
        return {"result": result}
    return {"error": {"code": -32601, "message": "Method not found"}}


def main() -> None:
    cfg = config()
    note(cfg, "starts", {"pid": os.getpid()})
    if cfg.get("exit_at_start") is not None:
        sys.exit(int(cfg["exit_at_start"]))
    if cfg.get("chatter"):
        print("starting up, please wait")  # a careless server writing to standard output
    state: dict = {}
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        cfg = config()
        note(cfg, "log", message)
        if "id" not in message:
            continue
        answer = handle(cfg, state, message)
        if answer is not None:
            send({"jsonrpc": "2.0", "id": message["id"], **answer})


if __name__ == "__main__":
    main()
