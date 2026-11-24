"""A small AgentMail that speaks its REST shapes through ``httpx.MockTransport``, for the
AgentMail tests. Nothing here reaches the network or uses a real key. Addresses are
built at run time on example domains."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx

KEY = "sk-test-SECRET-0123456789"
BASE = "https://agentmail.example.com/v0"


def at(user: str, domain: str = "example.com") -> str:
    return "@".join((user, domain))


OWNER = at("owner")
OWNER_DOMAIN = "example.com"
FRIEND = at("friend", "example.org")
STRANGER = at("stranger", "example.net")
INBOX = at("ada", "example.net")

PASS = {"spf": "pass", "dkim": "pass", "dmarc": "pass", "dkim_domain": OWNER_DOMAIN,
        "dmarc_domain": OWNER_DOMAIN}
FAIL = {"spf": "fail", "dkim": "fail", "dmarc": "fail", "dkim_domain": "example.org",
        "dmarc_domain": OWNER_DOMAIN}


def message(message_id: str, sender: str, *, verdict: dict[str, Any] | None = None,
            subject: str = "Hello", text: str = "Hi there.", ts: str = "2026-11-20T10:00:00Z",
            labels: tuple[str, ...] = ("received", "unread"), **more: Any) -> dict[str, Any]:
    out = {"inbox_id": INBOX, "thread_id": f"t-{message_id}", "message_id": message_id,
           "labels": list(labels), "timestamp": ts, "from": sender, "to": [INBOX],
           "size": 100, "subject": subject, "preview": text[:40], "text": text,
           "created_at": ts, "updated_at": ts}
    if verdict is not None:
        out["authentication_results"] = verdict
    return out | more


def reply(status: int = 200, body: Any = None) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {})


class World(httpx.MockTransport):
    """One AgentMail organisation: inboxes, messages, drafts, and what each send did."""

    def __init__(self) -> None:
        super().__init__(self.handle)
        self.requests: list[httpx.Request] = []
        self.inboxes: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []  # newest last
        self.drafts: dict[str, dict[str, Any]] = {}
        self.sent: list[dict[str, Any]] = []
        self.answers: dict[str, Any] = {}  # "METHOD /path" -> response or function

    def calls(self, method: str | None = None, ending: str | None = None) -> list[httpx.Request]:
        return [r for r in self.requests if (method is None or r.method == method)
                and (ending is None or unquote(r.url.path).endswith(ending))]

    def bodies(self, method: str, ending: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.calls(method, ending)]

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911
        self.requests.append(request)
        path = unquote(request.url.path).removeprefix("/v0")
        forced = self.answers.get(f"{request.method} {path}")
        if forced is not None:
            return forced(request) if callable(forced) else forced
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        body = json.loads(request.content) if request.content else {}
        parts = path.strip("/").split("/")
        if parts == ["inboxes"] and request.method == "GET":
            return reply(200, {"count": len(self.inboxes), "inboxes": self.inboxes})
        if parts == ["inboxes"] and request.method == "POST":
            made = {"inbox_id": INBOX, "email": INBOX, "pod_id": "p1",
                    "display_name": "Ada", "created_at": "", "updated_at": ""}
            self.inboxes.append(made)
            return reply(200, made)
        if len(parts) >= 3 and parts[0] == "inboxes" and parts[2] == "messages":
            if len(parts) == 3 and request.method == "GET":
                found = list(self.messages)
                if "after" in query:
                    found = [m for m in found if m["timestamp"] >= query["after"]]
                if query.get("ascending") != "true":
                    found = list(reversed(found))
                found = found[:int(query.get("limit", 100))]
                return reply(200, {"count": len(found), "messages": found})
            if len(parts) == 4 and parts[3] == "send":
                self.sent.append(body)
                return reply(200, {"message_id": f"sent-{len(self.sent)}", "thread_id": "t"})
            if len(parts) == 4 and request.method == "GET":
                found = next((m for m in self.messages if m["message_id"] == parts[3]), None)
                return reply(200, found) if found else reply(404, {"name": "NotFound",
                                                                  "message": "no message"})
            if len(parts) == 5 and parts[4] == "reply":
                self.sent.append(body | {"reply_to_message": parts[3]})
                return reply(200, {"message_id": f"sent-{len(self.sent)}", "thread_id": "t"})
        if len(parts) >= 3 and parts[2] == "drafts":
            if len(parts) == 3 and request.method == "POST":
                draft_id = f"d{len(self.drafts) + 1}"
                self.drafts[draft_id] = body
                return reply(200, {"draft_id": draft_id, "inbox_id": INBOX, "labels": []})
            if len(parts) == 4 and request.method == "DELETE":
                return (reply(200, {}) if self.drafts.pop(parts[3], None) is not None
                        else reply(404, {"name": "NotFound", "message": "no draft"}))
        return reply(404, {"name": "NotFound", "message": "no route"})
