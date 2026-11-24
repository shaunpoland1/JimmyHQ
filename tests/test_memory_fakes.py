"""A fake embeddings server for the memory tests. Nothing here touches a network.

It speaks the OpenAI embeddings wire format through ``httpx.MockTransport`` and
makes vectors by hand: one axis per topic, so two texts about the same topic
are close even when they share no words.
"""

from __future__ import annotations

import json
import re

import httpx

from jimmyhq.models import Target, brain
from test_models_fakes import Network, add

TOPICS = {
    "drink": ("tea", "coffee", "beverage", "espresso", "thirsty"),
    "pet": ("dog", "dogs", "cat", "puppy", "animal", "animals"),
    "travel": ("train", "flight", "airport", "journey", "trip"),
    "money": ("invoice", "budget", "payment", "cost", "salary"),
}
HOME, CLOUD = "127.0.0.1", "embed.example.com"


def vector(text: str) -> list[float]:
    words = "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
    axes = [float(sum(w in topic for w in words)) for topic in TOPICS.values()]
    return [*axes, 0.01]  # never all zeros


class Embedder:
    """``mode``: ok, down, garbage or short (one vector too few)."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.texts: list[str] = []
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "down":
            raise httpx.ConnectError("refused", request=request)
        if self.mode == "garbage":
            return httpx.Response(200, text="<html>not json</html>")
        assert request.url.path.endswith("/embeddings")
        body = json.loads(request.content)
        texts = body["input"]
        self.texts += texts
        data = [{"object": "embedding", "index": i, "embedding": vector(t)}
                for i, t in enumerate(texts)]
        if self.mode == "short":
            data = data[:-1]
        tokens = sum(len(t.split()) for t in texts)
        return httpx.Response(200, json={
            "object": "list", "data": list(reversed(data)), "model": body["model"],
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens}})


def with_embedder(core, *, mode: str = "ok", hosted: bool = False, model: str = "embed-a"):
    """Give the embeddings role a fake model, on this machine or on a hosted address."""
    fake = Embedder(mode)
    b = brain(core)
    b.transport = Network({HOME: fake, CLOUD: fake})
    if hosted:
        add(b, "cloud", host=CLOUD, key="sk-test-embed-key-000000")
        b.set_role("embeddings", Target("cloud", model), actor="owner")
    else:
        add(b, "home", host=f"{HOME}:11434")
        b.set_role("embeddings", Target("home", model), actor="owner")
    return fake


def tape_text(core) -> str:
    """Every event on the Tape, as one string, to search for things that must not be there."""
    return "\n".join(json.dumps(e.model_dump(), ensure_ascii=False) for e in core.tape.after(
        0, limit=100_000))


# --- a model that proposes facts ---------------------------------------------------

ITEM = re.compile(r'^\[(\d+)\] \((.*?); (.*?); (\d{4}-\d\d-\d\d)\) (".*")$', re.M)


class Miner:
    """A fake chat model on this machine (or a hosted address) that answers a
    mining prompt with whatever ``answer(items)`` returns, in the shape the
    schema asks for. ``items`` are the numbered items it was shown, parsed
    back out of the prompt: ``{"n", "who", "where", "text"}``. Anything else
    it is asked (a chat message) gets ``chat_reply``.

    ``mode``: ok, down, or garbage (an answer that is not JSON)."""

    def __init__(self, answer=None, mode: str = "ok", chat_reply: str = "Noted."):
        self.answer = answer or (lambda items: [])
        self.mode = mode
        self.chat_reply = chat_reply
        self.prompts: list[str] = []  # every mining prompt, system and user text together
        self.seen: list[list[dict]] = []  # the items of each mining prompt
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "small"}]})
        if self.mode == "down":
            raise httpx.ConnectError("refused", request=request)
        body = json.loads(request.content)
        text = "\n".join(m["content"] for m in body["messages"] if isinstance(m["content"], str))
        usage = {"prompt_tokens": 50, "completion_tokens": 20}
        if "ITEMS BEGIN>>>" not in text:
            if body.get("stream"):
                chunks = [{"choices": [{"delta": {"role": "assistant", "content": ""}}]},
                          {"choices": [{"delta": {"content": self.chat_reply}}]},
                          {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                          {"choices": [], "usage": usage}]
                lines = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                return httpx.Response(200, text=lines,
                                      headers={"content-type": "text/event-stream"})
            content = self.chat_reply
        else:
            self.prompts.append(text)
            items = [{"n": int(n), "who": who, "where": where, "text": json.loads(quoted)}
                     for n, who, where, _, quoted in ITEM.findall(text)]
            self.seen.append(items)
            content = ("this is not json" if self.mode == "garbage"
                       else json.dumps({"facts": self.answer(items)}))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}], "usage": usage})


def with_miner(core, answer=None, *, hosted: bool = False, role: str = "default",
               mode: str = "ok", chat_reply: str = "Noted.") -> Miner:
    """Give a role a fake model that proposes facts. ``default`` also answers
    for the cheap role, which borrows it."""
    fake = Miner(answer, mode, chat_reply)
    b = brain(core)
    b.transport = Network({HOME: fake, CLOUD: fake})
    if hosted:
        add(b, "cloud", host=CLOUD, key="sk-test-miner-key-0000000")
        b.set_role(role, Target("cloud", "small"), actor="owner")
    else:
        add(b, "home", host=f"{HOME}:11434")
        b.set_role(role, Target("home", "small"), actor="owner")
    return fake


def fact(statement: str, *evidence: int, confidence: float = 0.6, kind: str = "preference"):
    return {"statement": statement, "kind": kind, "confidence": confidence,
            "evidence": list(evidence)}


def found_in(data, needle: str) -> list[str]:
    """Every file under the data folder whose raw bytes contain ``needle``.

    Paths come back with forward slashes on every system."""
    wanted = needle.encode("utf-8")
    return sorted(p.relative_to(data).as_posix() for p in data.rglob("*")
                  if p.is_file() and wanted in p.read_bytes())
