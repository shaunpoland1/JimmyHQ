"""Finding out what Tailscale can do for this computer, and the one command that uses it.

Detection only reads: it runs ``tailscale status --json`` and ``tailscale serve
status --json``. The CLI is always run as an argument list, never through a
shell, with a short timeout and no input. What it says is parsed as JSON and
trusted for nothing else: a name is accepted only if it looks like a host name.
Nothing here logs or stores a machine or network name.

Tailscale's own documentation for the commands used:
https://tailscale.com/docs/reference/tailscale-cli (status, version)
https://tailscale.com/docs/reference/tailscale-cli/serve (serve, serve status, off)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jimmyhq.core import Core

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
READ_TIMEOUT_S = 8.0
CHANGE_TIMEOUT_S = 25.0
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")

# What to tell the owner for each state in which turning anything on cannot work.
PROBLEMS = {
    "not_installed": (
        "Tailscale is not installed on this computer, or JimmyHQ cannot find it. Install it "
        "from https://tailscale.com/download. If it is installed somewhere unusual, put the "
        "full path of the tailscale program in \"Path to the Tailscale command\" below."),
    "not_running": (
        "Tailscale is installed but not running. Open the Tailscale app (or start its "
        "service), then check again."),
    "needs_login": (
        "Tailscale is not signed in. Open the Tailscale app, or run \"tailscale up\" in a "
        "terminal, sign in, then check again."),
    "stopped": (
        "Tailscale is turned off. Turn it on in the Tailscale app, or run \"tailscale up\", "
        "then check again."),
    "unknown": (
        "JimmyHQ could not understand what Tailscale answered. It may be an older version. "
        "Use the steps under \"Do it yourself\"."),
}
NO_NAME = (
    "Tailscale is running but has no name for this computer. Turn on MagicDNS and HTTPS "
    "certificates for your tailnet in the Tailscale admin console (the DNS page), then "
    "check again.")


@dataclass(frozen=True)
class Ran:
    """What a command did. ``code`` is None when it never finished or never started."""

    code: int | None
    out: str = ""
    err: str = ""
    timed_out: bool = False


Runner = Callable[[Sequence[str], float], Ran]


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_argv(argv: Sequence[str], timeout: float) -> Ran:
    """Run a program with these arguments, with no shell, no input and a time limit."""
    extra: dict[str, Any] = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW  # no console window flashes up
    try:
        done = subprocess.run(  # noqa: S603 - an argument list, shell off, owner-chosen program
            list(argv), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
            shell=False, check=False, **extra)
    except subprocess.TimeoutExpired as exc:
        return Ran(None, _text(exc.stdout), _text(exc.stderr), timed_out=True)
    except (OSError, ValueError) as exc:
        return Ran(None, "", str(exc))
    return Ran(done.returncode, _text(done.stdout), _text(done.stderr))


def standard_paths() -> list[str]:
    """Where the Tailscale command is usually installed on this kind of computer."""
    if sys.platform == "darwin":
        return ["/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
                "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]
    if sys.platform == "win32":
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
        found = [str(Path(root) / "Tailscale" / "tailscale.exe") for root in roots if root]
        # Older installers used this folder name.
        old = os.environ.get("ProgramFiles(x86)")
        return found + ([str(Path(old) / "Tailscale IPN" / "tailscale.exe")] if old else [])
    return ["/usr/bin/tailscale", "/usr/sbin/tailscale", "/usr/local/bin/tailscale",
            "/snap/bin/tailscale"]


class Remote:
    """The pieces detection depends on, kept as attributes so a test can replace them."""

    def __init__(self, core: Core) -> None:
        self.core = core
        self.runner: Runner = run_argv
        self.which: Callable[[str], str | None] = shutil.which
        self.standard: Callable[[], list[str]] = standard_paths


def service(core: Core) -> Remote:
    return core.service("remote", Remote)


@dataclass(frozen=True)
class ServeEntry:
    """One HTTPS address Tailscale serves on this computer, without the machine's name."""

    https_port: int
    target_port: int | None  # the local port it forwards to, None if it is not a forward
    public: bool  # open to the whole internet (Tailscale Funnel)


@dataclass
class Detection:
    state: str  # not_installed, not_running, needs_login, stopped, ready or unknown
    path: str | None = None  # the command found
    source: str | None = None  # "setting", "path" or "standard"
    backend_state: str | None = None  # Tailscale's own word, such as Running
    name: str | None = None  # this computer's name on the tailnet; only when ready
    https_enabled: bool | None = None  # None: this Tailscale did not say
    serve_known: bool = False  # whether the list of served addresses could be read
    entries: list[ServeEntry] = field(default_factory=list)
    setting_missing: bool = False  # a path is set but nothing is there

    def entry(self, port: int) -> ServeEntry | None:
        """The address that forwards to JimmyHQ's port, if there is one."""
        return next((e for e in self.entries if e.target_port == port), None)

    def public_entry(self, port: int) -> ServeEntry | None:
        """An address forwarding to JimmyHQ's port that Funnel opens to the internet. Any
        one is enough: there may be several addresses for the one port, and the first
        being private says nothing about the others."""
        return next((e for e in self.entries if e.target_port == port and e.public), None)

    def clash(self, port: int) -> ServeEntry | None:
        """An address on this HTTPS port that forwards somewhere else."""
        return next((e for e in self.entries
                     if e.https_port == port and e.target_port != port), None)


def host_name(value: Any) -> str | None:
    """A machine name from Tailscale, in lower case without the final dot, or None."""
    if not isinstance(value, str):
        return None
    name = value.strip().lower().rstrip(".")
    return name if _NAME.fullmatch(name) else None


def find(svc: Remote) -> tuple[str | None, str | None, bool]:
    """The Tailscale command to use: (path, where it came from, a set path is missing)."""
    chosen = svc.core.settings.get("remote.tailscale_path").strip()
    missing = False
    if chosen:
        candidate = Path(chosen).expanduser()
        if candidate.is_file():
            return str(candidate), "setting", False
        missing = True
    found = svc.which("tailscale")
    if found:
        return found, "path", missing
    for candidate_path in svc.standard():
        if Path(candidate_path).is_file():
            return candidate_path, "standard", missing
    return None, None, missing


def _json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


_STATES = {"Running": "ready", "NeedsLogin": "needs_login", "NoState": "needs_login",
           "NeedsMachineAuth": "needs_login", "Stopped": "stopped"}


def _classify(ran: Ran) -> tuple[str, str | None, dict | None]:
    """(state, Tailscale's own word, the parsed answer) from `tailscale status --json`."""
    parsed = _json(ran.out)
    if parsed is None:
        said = f"{ran.out} {ran.err}".lower()
        if ran.timed_out:
            return "unknown", None, None
        if "is stopped" in said:
            return "stopped", None, None
        if any(w in said for w in ("failed to connect", "not running", "tailscaled",
                                   "connection refused", "is tailscale running")):
            return "not_running", None, None
        return "unknown", None, None
    word = parsed.get("BackendState")
    me = parsed.get("Self")
    if isinstance(word, str) and word:
        return _STATES.get(word, "unknown"), word, parsed
    # An older CLI may leave BackendState out. A name for this computer means it is up.
    if isinstance(me, dict) and host_name(me.get("DNSName")):
        return "ready", None, parsed
    return "unknown", None, parsed


def _entries(served: dict, name: str | None) -> list[ServeEntry]:
    web = served.get("Web")
    funnel = served.get("AllowFunnel")
    if not isinstance(web, dict):
        return []
    out = []
    for key, config in web.items():
        if not isinstance(key, str) or ":" not in key:
            continue
        host, _, port_text = key.rpartition(":")
        if name is not None and host_name(host) != name:
            continue
        if not port_text.isdigit():
            continue
        target = None
        handlers = config.get("Handlers") if isinstance(config, dict) else None
        for handler in (handlers.values() if isinstance(handlers, dict) else ()):
            proxy = handler.get("Proxy") if isinstance(handler, dict) else None
            if not isinstance(proxy, str):
                continue
            try:
                parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
                if (parts.hostname or "").lower() in LOOPBACK and parts.port:
                    target = parts.port
                    break
            except ValueError:
                continue
        public = bool(isinstance(funnel, dict) and funnel.get(key) is True)
        out.append(ServeEntry(int(port_text), target, public))
    return out


def detect(core: Core) -> Detection:
    """Read what Tailscale says. Changes nothing, and tolerates a missing or old CLI."""
    svc = service(core)
    path, source, missing = find(svc)
    if path is None:
        return Detection("not_installed", setting_missing=missing)
    ran = svc.runner([path, "status", "--json"], READ_TIMEOUT_S)
    state, word, status = _classify(ran)
    found = Detection(state, path=path, source=source, backend_state=word,
                      setting_missing=missing)
    if state != "ready" or status is None:
        return found
    me = status.get("Self")
    found.name = host_name(me.get("DNSName")) if isinstance(me, dict) else None
    domains = status.get("CertDomains")
    if isinstance(domains, list):
        found.https_enabled = any(host_name(d) for d in domains)
    if found.name is None:
        return found
    served = svc.runner([path, "serve", "status", "--json"], READ_TIMEOUT_S)
    parsed = _json(served.out) if served.code == 0 else None
    if parsed is None and served.code == 0 and not served.out.strip():
        parsed = {}  # nothing is served
    if parsed is not None:
        found.serve_known = True
        found.entries = _entries(parsed, found.name)
    return found


# --- the commands --------------------------------------------------------------------

def target(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def serve_args(port: int) -> list[str]:
    """Map HTTPS on the tailnet, on the same port number, to JimmyHQ. Private to the tailnet."""
    return ["serve", "--bg", f"--https={port}", target(port)]


def off_args(https_port: int) -> list[str]:
    return ["serve", f"--https={https_port}", "off"]


def shown(args: Sequence[str]) -> str:
    """A command as the owner would type it."""
    return " ".join(["tailscale", *args])


def address(name: str, https_port: int) -> str:
    return f"https://{name}" + ("" if https_port == 443 else f":{https_port}")


def failure(ran: Ran, what: str) -> str:
    """A sentence for a change that did not work, with what Tailscale said (short)."""
    said = " ".join(f"{ran.err} {ran.out}".split())[:400]
    if ran.timed_out:
        hint = ("Tailscale did not finish in time. If it needs you to switch on HTTPS or Serve "
                "for your tailnet, run the command yourself in a terminal: it prints a link "
                "to follow.")
    elif re.search(r"access denied|operator|permission|administrator|not allowed", said, re.I):
        hint = ("Tailscale refused because this user may not change its settings. On Linux, run "
                "\"sudo tailscale set --operator=$USER\" once, or run the command yourself "
                "with sudo. On Windows, use a terminal opened as administrator.")
    else:
        hint = "Run the command yourself in a terminal to see the full message."
    return f"Tailscale did not {what}. {hint}" + (f" It said: {said}" if said else "")
