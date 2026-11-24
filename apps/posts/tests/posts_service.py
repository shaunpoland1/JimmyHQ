"""A stand-in for SocialBu inside the fake core: canned answers by method and address,
and a record of every request. Nothing reaches the network.

Every answer is set by the test with ``service.on(method, path, *answers)``; the last
answer for an address repeats. An address with no answer gets no answer (as a timeout),
and is listed in ``service.unscripted``. Ids and names are invented; the key is the
made-up one every test uses.
"""

from __future__ import annotations

import json
from typing import Any

import buffer_schema
from jimmyhq_app import CallError
from posts.schedulers.socialbu import BASE
from posts_rig import START

KEY = "sk-test-SECRET-0123456789"
HOUR = 3_600_000
DAY = 24 * HOUR
SLOT = START + DAY  # the page's next 08:00, as SocialBu writes it: 2027-01-16 08:00:00
BODY = "Some 1,240 homes in the valley added solar panels last year."
MAIN = {"scheduler": "socialbu", "account_id": "1001", "label": "Main"}
SECOND = {"scheduler": "socialbu", "account_id": "1002", "label": "Second"}
ACCOUNTS = [MAIN, SECOND]
COLLISION = ("A post is already scheduled at this time for your Facebook page (Example "
             "Garden). Please choose a different time.")


def reply(status: int = 200, data: Any = None, text: str | None = None) -> dict[str, Any]:
    body = text if text is not None else ("" if data is None else json.dumps(data))
    return {"status": status, "headers": {"content-type": "application/json"}, "text": body}


def no_answer() -> CallError:
    return CallError("unavailable", "socialbu.com took longer than 30 seconds to answer. "
                                    "Try again later.")


def created(*pairs: tuple[int, int]) -> dict[str, Any]:
    """A create answer naming ``(post id, account id)`` pairs, in the order given."""
    return reply(200, {"success": True, "posts": [{"id": pid, "account_id": aid,
                                                   "publish_at": "2027-01-16 08:00:00"}
                                                  for pid, aid in pairs]})


def upstream_post(pid: int, aid: int, *, publish_at: str = "2027-01-16 08:00:00",
                  published: bool = False, error: str = "", can_edit: bool = True,
                  attachments: list | None = None, insights: list | None = None) -> dict:
    post: dict[str, Any] = {"id": pid, "account_id": aid, "publish_at": publish_at,
                            "published": published, "error": error or None,
                            "can_edit": can_edit, "content": "Some text."}
    if attachments is not None:
        post["attachments"] = attachments
    if insights is not None:
        post["insights"] = insights
    return reply(200, {"post": post})


class Service:
    def __init__(self, core: Any):
        self.core = core
        self.script: dict[tuple[str, str], list[Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.unscripted: list[tuple[str, str]] = []
        self._fetch_as_core = core._net_fetch  # noqa: SLF001 - keeps the host and file rules
        core._net_fetch = self._fetch  # noqa: SLF001

    def on(self, method: str, path: str, *answers: Any) -> None:
        self.script.setdefault((method.upper(), BASE + path), []).extend(answers)

    def _fetch(self, args: dict[str, Any]) -> dict[str, Any]:
        method = str(args.get("method") or "GET").upper()
        url = str(args["url"])
        self.calls.append((method, url.removeprefix(BASE), args))
        queue = self.script.get((method, url))
        if not queue:
            self.unscripted.append((method, url.removeprefix(BASE)))
            raise no_answer()
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        self.core.responses[url] = answer
        return self._fetch_as_core(args)

    def sent(self, method: str, path: str) -> list[Any]:
        """The JSON bodies sent to one address."""
        return [args.get("json") for m, p, args in self.calls if m == method and p == path]

    def asked(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _ in self.calls]


# --- a post on its way, through the app as the owner would take it ---------------------------


def queued(rig, *, accounts=ACCOUNTS, page=None, url=None):
    """A post approved and queued, with its proposed slot."""
    page = page or rig.page(accounts=accounts)
    more = {"url": url} if url else {}
    piece = rig.through(page, BODY, **more)
    [card] = [c for c in rig.cards_of("draft") if c["subject"].endswith(
        f"post:{rig.store.post_of(piece['id'])['id']}")]
    rig.core.decide(card["card_id"], "approve")
    return rig.store.post_of(piece["id"])


def booked(rig, **more):
    """A post booked on both accounts as copies 500 and 501, with the service's record
    cleared for what the test does next."""
    post = queued(rig, **more)
    rig.service.on("POST", "/posts", created((500, 1001), (501, 1002)))
    done = rig.core.call("post.book", {"id": post["id"]})
    assert done["state"] == "booked"
    rig.service.script.clear()
    rig.service.calls.clear()
    return done


def copies(rig, post_id):
    return {u["account"]: u["external_id"] for u in rig.store.upstream(post_id)}


def operation(query: str) -> str:
    """Which of Buffer's reads a query is: the channels, the organizations, the posts."""
    if "channels(" in query:
        return "channels"
    if "posts(" in query:
        return "posts"
    return "organizations" if "organizations" in query else "?"


class BufferService:
    """A stand-in for Buffer's reads: canned answers by ``organizations``, ``channels`` or
    ``posts``, in order (the last repeats); an operation with no answer gets no answer."""

    def __init__(self, core: Any):
        self.core = core
        self.script: dict[str, list[Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._as_core = core._net_fetch  # noqa: SLF001
        core._net_fetch = self._fetch  # noqa: SLF001

    def on(self, name: str, *answers: Any) -> None:
        self.script.setdefault(name, []).extend(answers)

    def _fetch(self, args: dict[str, Any]) -> dict[str, Any]:
        buffer_schema.check(str(args["json"]["query"]))  # only names Buffer documents
        name = operation(str(args["json"]["query"]))
        self.calls.append((name, args))
        queue = self.script.get(name)
        if not queue:
            raise CallError("unavailable", "api.buffer.com took longer than 30 seconds to "
                                           "answer. Try again later.")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        self.core.responses[str(args["url"])] = answer
        return self._as_core(args)

    def asked(self) -> list[str]:
        return [n for n, _ in self.calls]

    def queries(self, name: str) -> list[str]:
        return [a["json"]["query"] for n, a in self.calls if n == name]
