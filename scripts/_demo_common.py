"""What `demo_data.py` and `take_screenshots.py` share: the safety checks, starting and
stopping a JimmyHQ by its process id, a small pretend model on this computer, and a client
for the web API.

Everything here refuses to touch a data folder it was not told about. Both scripts work only
on the folder named by `JIMMYHQ_DATA`, on the port named by `JIMMYHQ_PORT`.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8440
# The demo owner's password. It is the same for everyone and protects nothing: the demo
# folder is a throwaway, and JimmyHQ listens on this computer only.
DEMO_PASSWORD = "demo-household-pass"  # noqa: S105 - public on purpose; see above
DEMO_OWNER = "Alex"
MARKER = "demo-data.txt"
MARKER_TEXT = (
    "This folder was filled by scripts/demo_data.py with invented sample data.\n"
    "scripts/take_screenshots.py works only on a folder that has this file.\n")


class Refused(SystemExit):
    """The script will not run. The message says what to change."""

    def __init__(self, message: str):
        super().__init__(f"{message}\nNothing was changed.")


def environment(*, fresh: bool) -> tuple[Path, int]:
    """The data folder and the port, from the environment only.

    `fresh`: the folder must not exist or must be empty (filling it). Otherwise it must hold
    the marker `demo_data.py` leaves (taking screenshots of it)."""
    raw = os.environ.get("JIMMYHQ_DATA", "").strip()
    if not raw:
        raise Refused("Set JIMMYHQ_DATA to the folder to use, on the same command line, for "
                      "example JIMMYHQ_DATA=/tmp/jimmyhq-demo. This script never picks a "
                      "folder for you, so it can never touch your own data.")
    data = Path(raw).expanduser().resolve()
    from platformdirs import user_data_dir

    if data == Path(user_data_dir("JimmyHQ", False)).resolve():
        raise Refused("JIMMYHQ_DATA is the folder JimmyHQ uses when you set nothing: your own "
                      "data. Name a different, empty folder.")
    raw_port = os.environ.get("JIMMYHQ_PORT", "").strip()
    if not raw_port.isdigit() or not 1024 <= int(raw_port) < 65535:
        raise Refused("Set JIMMYHQ_PORT to a free port between 1024 and 65534, on the same "
                      "command line, for example JIMMYHQ_PORT=8478.")
    port = int(raw_port)
    if port == DEFAULT_PORT:
        raise Refused(f"Port {DEFAULT_PORT} is the one JimmyHQ uses by default, so a running "
                      "JimmyHQ of yours may be there. Choose another port.")
    if fresh:
        if data.exists() and (not data.is_dir() or any(data.iterdir())):
            raise Refused(f"{data} is not empty. The demo data goes into a new, empty folder. "
                          "Name one that does not exist yet, or empty it yourself.")
    elif not (data / MARKER).is_file():
        raise Refused(f"{data} was not filled by scripts/demo_data.py (it has no {MARKER}), so "
                      "no screenshots are taken of it. Run demo_data.py on an empty folder "
                      "first.")
    return data, port


# --- the server ----------------------------------------------------------------------------


def start_server(data: Path, port: int, log: Path) -> subprocess.Popen:
    """Start `jimmyhq run` on the folder and port, wait until it answers, and print its pid."""
    env = dict(os.environ, JIMMYHQ_DATA=str(data), JIMMYHQ_PORT=str(port))
    logfile = log.open("ab")
    proc = subprocess.Popen(  # noqa: S603 - this Python, running this repo's own package
        [sys.executable, "-m", "jimmyhq", "run"], env=env, stdin=subprocess.DEVNULL,
        stdout=logfile, stderr=subprocess.STDOUT, cwd=ROOT)
    print(f"JimmyHQ started, pid {proc.pid}, port {port}", flush=True)
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            stop_server(proc)
            raise SystemExit(f"JimmyHQ stopped while starting. Its log is {log}.")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    stop_server(proc)
    raise SystemExit(f"JimmyHQ did not answer within 90 seconds. Its log is {log}.")


def stop_server(proc: subprocess.Popen) -> None:
    """Stop the process this script started, by its pid, and wait until it has gone."""
    if proc.poll() is None:
        proc.terminate()  # the supervisor stops its web, worker and village processes
        try:
            proc.wait(60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(30)
    print(f"JimmyHQ stopped, pid {proc.pid}, exit {proc.returncode}", flush=True)


# --- the pretend model ----------------------------------------------------------------------

MODEL_NAME = "demo-local"

REPLIES: list[tuple[str, str]] = [
    ("boiler", "The boiler service is due this month. I can add it to your tasks for Thursday, "
               "and put the engineer's number from your Home note on the card. Want me to?"),
    ("week", "Here is the week as it stands: the bins on Thursday, car insurance by Friday, "
             "and the garden bed still unplanted. Nothing else is overdue."),
    ("meal", "Three dinners that use what is already in the cupboard: lentil soup, a baked "
             "pasta with the last of the tomatoes, and an omelette with greens."),
]
FALLBACK = "Noted. I have kept that on this computer."
DIARY = [
    "Read the shared notes again. The boiler service is the thing most likely to be forgotten.",
    "Quiet hour. Went back over the garden plan and tidied what I had written.",
    "Nothing new from outside. I kept to my own notes and checked them for gaps.",
    "Wrote down which questions I could answer from the notes and which need Alex.",
]


def empty_for(schema: object) -> object:
    """The smallest value that fits a JSON Schema: what a model with nothing to say sends."""
    if not isinstance(schema, dict):
        return None
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    if kind == "object":
        props = schema.get("properties", {})
        return {k: empty_for(props.get(k)) for k in schema.get("required", list(props))}
    return {"array": [], "string": "", "integer": 0, "number": 0, "boolean": False}.get(kind)


class _ModelHandler(BaseHTTPRequestHandler):
    """Enough of an OpenAI-style server for JimmyHQ's chat, its agents and its health check.
    A turn that must call a tool writes a diary line; a reply that must be JSON is empty."""

    calls = 0

    def log_message(self, *_: object) -> None:
        pass

    def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, json.dumps({"data": [{"id": MODEL_NAME}]}).encode())
        else:
            self._send(404, b"{}")

    def _tool_call(self, body: dict) -> dict | None:
        names = [t["function"]["name"] for t in body.get("tools") or []]
        if not names:
            return None
        type(self).calls += 1
        name = "write_diary" if "write_diary" in names else names[0]
        text = f"{DIARY[self.calls % len(DIARY)]} (Note {self.calls}.)"
        args = {"thought": "A quiet moment.", "text": text} if name == "write_diary" \
            else {"thought": "A quiet moment."}
        return {"id": f"call-{self.calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(size) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, b"{}")
            return
        last = ""
        for message in body.get("messages", []):
            if message.get("role") == "user":
                content = message.get("content")
                last = content if isinstance(content, str) else json.dumps(content)
        wanted = (body.get("response_format") or {}).get("json_schema", {}).get("schema")
        call = self._tool_call(body)
        if wanted is not None:
            text = json.dumps(empty_for(wanted))
        elif call is not None:
            text = ""
        else:
            text = next((reply for word, reply in REPLIES if word in last.lower()), FALLBACK)
        usage = {"prompt_tokens": 40 + len(last) // 4, "completion_tokens": len(text) // 4 + 8}
        if not body.get("stream"):
            message = {"role": "assistant", "content": text or None}
            if call:
                message["tool_calls"] = [call]
            self._send(200, json.dumps({
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if call else "stop"}],
                "usage": usage}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = [{"choices": [{"index": 0, "delta": {"content": w + " "}}]}
                  for w in text.split(" ") if w]
        if call:
            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, **call}]}}]})
        chunks.append({"choices": [{"index": 0, "delta": {},
                                    "finish_reason": "tool_calls" if call else "stop"}],
                       "usage": usage})
        for chunk in chunks:
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


@contextlib.contextmanager
def pretend_model(port: int):
    """A model server on this computer, at `port`, for as long as the block runs. It answers
    from a short fixed list, so chat in the demo is the same every time."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()


# --- the web API ----------------------------------------------------------------------------


class Api:
    """The owner, signed in over HTTP on this computer."""

    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.http = httpx.Client(base_url=self.base, timeout=120)

    def sign_in(self) -> None:
        self.post("/api/auth/login", {"password": DEMO_PASSWORD})

    def request(self, method: str, path: str, body: object = None) -> object:
        answer = self.http.request(method, path, json=body)
        if answer.status_code >= 400:
            raise RuntimeError(f"{method} {path} answered {answer.status_code}: "
                               f"{answer.text[:300]}")
        return answer.json() if answer.content else None

    def get(self, path: str) -> object:
        return self.request("GET", path)

    def post(self, path: str, body: object = None) -> object:
        return self.request("POST", path, body if body is not None else {})

    def put(self, path: str, body: object) -> object:
        return self.request("PUT", path, body)

    def app_call(self, app_id: str, name: str, /, **args: object) -> object:
        """One call of an app's program, as the app's own page makes it (`hq.app.call`)."""
        answer = self.post(f"/api/apps/{app_id}/bridge/app.call",
                           {"jimmyhq": 1, "id": 1, "args": {"name": name, "args": args}})
        if not answer.get("ok"):
            error = answer.get("error") or {}
            raise RuntimeError(f"{app_id} {name}: {error.get('message', answer)}")
        return answer.get("result")

    def close(self) -> None:
        self.http.close()
