"""How the SDK reaches JimmyHQ: local HTTP to ``/api/app/v1/`` with the app's token.

The token comes, in this order, from ``JIMMYHQ_APP_TOKEN`` (for development by hand) or,
when JimmyHQ's supervisor started the program (``JIMMYHQ_SUPERVISED=1``), from the first
line of standard input, ``{"token": "..."}``. It is never written anywhere.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, Protocol

import httpx

from jimmyhq_app._types import CallError, TokenRevoked

PREFIX = "/api/app/v1"


class Transport(Protocol):
    def request(self, method: str, path: str, *, body: Any = None,
                params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        """The ``result`` of JimmyHQ's answer, or raise ``CallError``."""


def read_token(stdin: Any = None, environ: Any = None) -> str:
    environ = os.environ if environ is None else environ
    given = environ.pop("JIMMYHQ_APP_TOKEN", None)  # not passed on to anything it starts
    if given:
        return given
    if environ.get("JIMMYHQ_SUPERVISED") == "1":
        line = (stdin or sys.stdin).readline()
        try:
            token = json.loads(line)["token"]
        except (ValueError, KeyError, TypeError):
            token = None
        if isinstance(token, str) and token:
            return token
    raise RuntimeError("This app has no token for JimmyHQ. Start it from JimmyHQ (it is "
                       "started for you once installed and switched on), or set "
                       "JIMMYHQ_APP_TOKEN while developing.")


def unwrap(answer: Any) -> Any:
    """The result inside JimmyHQ's envelope, or the refusal as a ``CallError``."""
    if not isinstance(answer, dict) or "ok" not in answer:
        raise CallError("internal", "JimmyHQ's answer could not be read.")
    if answer["ok"]:
        return answer.get("result")
    error = answer.get("error") or {}
    for_owner = error.get("owner_message")
    raise CallError(str(error.get("code", "internal")), str(error.get("message", "")),
                    error.get("retry_after_s"),
                    for_owner if isinstance(for_owner, str) else None)


class HttpTransport:
    """The real thing. ``client`` may be any ``httpx.Client`` pointed at the core's root
    (tests pass FastAPI's test client); otherwise one is made from ``JIMMYHQ_APP_API``."""

    def __init__(self, *, api: str | None = None, token: str | None = None,
                 client: httpx.Client | None = None):
        self._api = api or os.environ.get("JIMMYHQ_APP_API") or ""
        self._token = token
        self._client = client
        self._lock = threading.Lock()

    def _ready(self) -> tuple[httpx.Client, str]:
        with self._lock:
            if self._token is None:
                self._token = read_token()
            if self._client is None:
                if not self._api.startswith(("http://127.0.0.1:", "http://[::1]:",
                                             "http://localhost:")):
                    # The token only ever goes to this machine.
                    raise RuntimeError("JIMMYHQ_APP_API must be this computer's own "
                                       "address, such as http://127.0.0.1:8440.")
                self._client = httpx.Client(base_url=self._api, trust_env=False,
                                            timeout=httpx.Timeout(60.0, connect=5.0))
            return self._client, self._token

    def prepare(self) -> None:
        """Read the token and check the address now, before anything is asked."""
        self._ready()

    def request(self, method: str, path: str, *, body: Any = None,
                params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        client, token = self._ready()
        options: dict[str, Any] = {"headers": {"authorization": f"Bearer {token}"}}
        if body is not None:
            options["json"] = body
        if params:
            options["params"] = params
        if timeout is not None:
            options["timeout"] = timeout
        response = client.request(method, PREFIX + path, **options)
        if response.status_code in (401, 403):
            raise TokenRevoked()
        if response.status_code != 200:
            raise CallError("internal", f"JimmyHQ answered {response.status_code}.")
        return unwrap(response.json())

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
