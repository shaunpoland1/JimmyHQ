"""A fake Tailscale for the remote-access tests. No real ``tailscale`` is ever run."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

from jimmyhq import remote  # noqa: F401 - registers the module
from jimmyhq.remote import tailscale as ts

NAME = "example-machine.example-tailnet.ts.net"
PORT = 8440
CLI = "/opt/example/bin/tailscale"


def ready_status(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"BackendState": "Running", "Self": {"DNSName": NAME + "."},
                           "CertDomains": [NAME], "MagicDNSSuffix": "example-tailnet.ts.net"}
    doc.update(changes)
    return doc


def served_doc(*entries: tuple[int, int | None], public: Sequence[int] = ()) -> dict[str, Any]:
    """What `serve status --json` says for (https port, local port) pairs."""
    doc: dict[str, Any] = {"TCP": {}, "Web": {}}
    for https, local in entries:
        doc["TCP"][str(https)] = {"HTTPS": True}
        handler = {"Proxy": f"http://127.0.0.1:{local}"} if local else {"Text": "hello"}
        doc["Web"][f"{NAME}:{https}"] = {"Handlers": {"/": handler}}
    if public:
        doc["AllowFunnel"] = {f"{NAME}:{p}": True for p in public}
    return doc


class FakeTailscale:
    """Answers like the CLI would, and applies the one serve command it is given."""

    def __init__(self, status: dict[str, Any] | Ran | None = None,
                 served: dict[str, Any] | None = None) -> None:
        self.status = ready_status() if status is None else status
        self.served = served if served is not None else {}
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.fail_change: ts.Ran | None = None

    @property
    def changes(self) -> list[list[str]]:
        return [c for c in self.calls if c[1:2] == ["serve"] and "status" not in c]

    def __call__(self, argv: Sequence[str], timeout: float) -> ts.Ran:
        argv = list(argv)
        self.calls.append(argv)
        self.timeouts.append(timeout)
        tail = argv[1:]
        if tail == ["status", "--json"]:
            if isinstance(self.status, ts.Ran):
                return self.status
            return ts.Ran(0, json.dumps(self.status))
        if tail == ["serve", "status", "--json"]:
            return ts.Ran(0, json.dumps(self.served) if self.served else "{}")
        if self.fail_change is not None:
            return self.fail_change
        if tail[:2] == ["serve", "--bg"] and len(tail) == 4 and tail[2].startswith("--https="):
            https = int(tail[2].split("=")[1])
            local = int(tail[3].rsplit(":", 1)[1])
            merged = served_doc(*self._pairs(), (https, local))
            self.served = merged
            return ts.Ran(0, "Available within your tailnet.")
        if len(tail) == 3 and tail[0] == "serve" and tail[2] == "off":
            https = int(tail[1].split("=")[1])
            self.served = served_doc(*(p for p in self._pairs() if p[0] != https))
            return ts.Ran(0, "")
        return ts.Ran(2, "", "unknown command")

    def _pairs(self) -> list[tuple[int, int | None]]:
        out = []
        for key, config in (self.served.get("Web") or {}).items():
            proxy = next(iter(config["Handlers"].values())).get("Proxy")
            local = int(proxy.rsplit(":", 1)[1]) if proxy else None
            out.append((int(key.rsplit(":", 1)[1]), local))
        return out

    def copy(self) -> dict[str, Any]:
        return copy.deepcopy(self.served)


Ran = ts.Ran


def install(core, fake: FakeTailscale | None, found: bool = True) -> FakeTailscale:
    """Make this core talk to the fake. ``found=False``: Tailscale is nowhere to be found."""
    svc = ts.service(core)
    svc.runner = fake or FakeTailscale()
    svc.which = (lambda _name: CLI) if found else (lambda _name: None)
    svc.standard = lambda: []
    return svc.runner
