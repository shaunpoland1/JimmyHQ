"""Sending a message and writing the reply.

A reply is written by a task of its own, not by the request that asked for
it. The request (or a Python caller) only listens. That is what lets a reply
be stopped from another tab, lets a dropped connection stop the model call,
and guarantees the reply is stored exactly once however it ends:

- **complete**: the model finished;
- **stopped**: the owner stopped it or the listener went away; what had
  arrived is kept and marked;
- **failed**: the model call did not work; the reason is kept, and whatever
  had arrived is never sent back to a model as if it were an answer.

Nothing is stored per piece of text. The person's message is stored when the
send is accepted and the reply once, when it ends. If the process dies in
between, what is stored is a message with no answer and the conversation is
simply ready for another send or a regenerate.

Where the message and the reply are stored follows the conversation's switch
at the moment the send (or the regenerate) begins: on the Tape, or in the
off-the-record store. This module does not ask which; ``conversations`` does
the keeping. The one thing decided here is what the model call's receipt may
be tied to: an exchange kept off the record is tied to nothing on the Tape.

Before the model is asked, memory is searched when the conversation uses it
(see ``recall``). What comes back goes on the system side of the prompt, and
what the reply was given is stored with the reply: ids and counts, not words.

When the message asks for the web and web lookup is on, the model is offered
two tools and may go back to the web a bounded number of times before it
answers (see ``web``). Each search and each page read is told to the listener
as a "step" event while it happens, and the sources are stored with the reply.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jimmyhq.chat import conversations, recall, store, web
from jimmyhq.chat.store import KEEP, ChatError
from jimmyhq.core import Core
from jimmyhq.ids import ulid
from jimmyhq.models import ModelError, Reply, Target, brain

log = logging.getLogger("jimmyhq.chat")

BUSY = ("A reply is already being written in this conversation. Wait for it to finish, or "
        "stop it first.")
NO_MODEL = ("No model is set up to answer here yet. Add a model under Settings, Models and "
            "roles, then send again.")
_TITLE_SYSTEM = ("Write a short title, six words at most, for the conversation below. Answer "
                 "with the title only: no quotes and no full stop.")
_TITLE_EXCERPT = 1500
# A reply that wrote nothing before its model hit the answer limit: the model spent it all
# on reasoning, which the stock sentence ("ask for a longer answer") does not say.
ALL_REASONING = ("The model used its whole answer allowance on reasoning and wrote no text. "
                 "Lower the effort for this conversation, or raise \"Longest answer "
                 "(tokens)\" (models.default_max_tokens) under Settings, Models, then "
                 "regenerate the reply.")
# What the silent-reply watch reads the time from, and how often it looks. Tests replace them.
clock = time.monotonic
WATCH_POLL_S = 1.0


def silent_too_long(seconds: int) -> ModelError:
    """The reply was stopped for being quiet. Says what happened and what to do."""
    return ModelError(
        "timeout",
        f"The model worked for {seconds} seconds without writing any text or looking "
        "anything up, so the reply was stopped. Lower the effort for this conversation, or "
        "raise \"Longest silent wait for a reply (seconds)\" under Settings, Chat, then "
        "regenerate the reply.")


@dataclass
class Plan:
    """One reply, checked and ready, before any model is asked."""

    conv: dict[str, Any]
    user: dict[str, Any]  # the message being answered, as the API shows it
    on_record: bool  # where the reply will be kept: the switch when it began
    cause: str | None  # the Tape event the receipts descend from, if they may
    replaces: str | None
    messages: list[dict[str, str]]
    omitted: int
    options: dict[str, Any]
    since: int  # Tape position when the reply began, to find its own receipts
    actor: str
    wants_title: bool
    title_on_record: bool  # a model-written title may go to the Tape
    replaces_on_record: bool = True  # the reply this one takes the place of is on the Tape
    routes: tuple[tuple[str, str], ...] = ()  # the (provider, model) pairs that may be tried
    query: str | None = None  # what to ask memory; None when it is not to be asked
    private: bool = False  # the question was asked off the record: its receipt says nothing
    hosted_ok: bool = False  # the owner lets hosted models see memory
    hosted_fallback: bool = False  # a hosted provider is among those that may be tried
    web: bool = False  # the reply is offered the web tools
    web_private: bool = False  # its lookups' receipts may not say what was asked
    typed: list[str] = field(default_factory=list)  # the person's messages, for web's rule


@dataclass
class Run:
    """A reply being written in this process."""

    conversation_id: str
    message_id: str = field(default_factory=ulid)
    start: dict[str, Any] = field(default_factory=dict)
    queue: asyncio.Queue | None = None
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task | None = None
    settled: asyncio.Event | None = None
    finished: bool = False
    stop_requested: bool = False
    message: dict[str, Any] | None = None  # the reply as stored, once it has ended
    memory: dict[str, Any] | None = None  # what memory did for this reply; None when off
    kept_local: bool = False  # the reply carried memory, so no hosted fallback was allowed
    web: dict[str, Any] | None = None  # what web lookup did for this reply; None when off
    lively: bool = False  # it has written text or looked something up: it is not silent

    def emit(self, name: str, data: dict[str, Any]) -> None:
        if name in ("delta", "step"):
            self.lively = True
        if self.queue is not None:
            self.queue.put_nowait((name, data))

    def stop(self) -> None:
        """Ask the reply to stop. Safe from any thread, and after it has ended."""
        def cancel() -> None:
            if not self.finished and self.task is not None:
                self.stop_requested = True
                self.task.cancel()

        if self.loop is None:
            return
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if here is self.loop:
            cancel()
        else:
            with contextlib.suppress(RuntimeError):  # that loop has already closed
                self.loop.call_soon_threadsafe(cancel)


# --- deciding what will answer, and what it is sent ------------------------------


def _first_usable(core: Core, target: dict[str, Any], local_only: bool
                  ) -> tuple[Any, str, tuple[tuple[str, str], ...], list[Any]]:
    """The provider and model that will be tried first, every (provider,
    model) pair that may be, and those providers in the order they would be
    tried; or a refusal that says what to set up. Mirrors the Brain's own
    routing without contacting anyone."""
    config = brain(core).config()
    if target["kind"] == "model":
        targets = ([Target(target["provider_id"], target["model"])]
                   if target["provider_id"] in config.providers else [])
    elif target["kind"] == "role":
        targets = config.roles.get(target["role"]) or config.roles.get("default") or []
    else:
        raise ChatError(422, "Conversations with your agents arrive in a later release. "
                             "Choose a role or a model for this conversation.")
    present = [(config.providers[t.provider_id], t) for t in targets
               if t.provider_id in config.providers and config.providers[t.provider_id].enabled]
    if not present:
        raise ChatError(409, NO_MODEL)
    routes = tuple((p.id, t.model) for p, t in present)
    for provider, chosen in present:
        if not local_only or provider.is_local:
            return provider, chosen.model, routes, [p for p, _ in present]
    raise ChatError(409, "This conversation is set to stay on your own machines, and no local "
                         "model is set up to answer here. Add a local model under Settings, "
                         "Models and roles, or switch \"local only\" off for this conversation.")


def build_context(core: Core, messages: list[dict[str, Any]], max_messages: int,
                  max_chars: int) -> tuple[list[dict[str, str]], int]:
    """What the model is sent: the newest messages that fit the budget, oldest
    first, and how many earlier ones were left out. ``messages`` ends with the
    message being answered, which is always sent whole."""
    usable = [m for m in messages if m["role"] == "user"
              or (m["state"] in ("complete", "stopped") and m["chars"] > 0)]
    chosen = [usable[-1]]
    total = usable[-1]["chars"]
    for message in reversed(usable[:-1]):
        if len(chosen) >= max_messages or total + message["chars"] > max_chars:
            break
        chosen.append(message)
        total += message["chars"]
    chosen.reverse()
    while chosen[0]["role"] != "user":  # a model call starts with the person
        chosen.pop(0)
    out: list[dict[str, str]] = []
    for message in chosen:
        text = store.text_of(core, message)
        if out and out[-1]["role"] == message["role"]:
            # Two in a row from one side (an earlier reply failed): send them as one turn.
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": message["role"], "content": text})
    return out, len(usable) - len(chosen)


def _system(core: Core) -> str | None:
    prompt = core.settings.get("chat.system_prompt").strip()
    name = " ".join(core.settings.get("owner.name").split())
    if name:
        prompt = f"{prompt}\n\nYou are talking with {name}.".strip()
    return prompt or None


def _prepare(core: Core, run: Run, conv_id: str, text: Any, regenerate: bool, target: Any,
             effort: Any, actor: str, use_web: Any = KEEP) -> Plan:
    """Everything that can refuse a send, then the person's message is stored."""
    conv, messages, full = conversations.story(core, conv_id)
    if conv["archived"]:
        raise ChatError(409, "This conversation is archived. Restore it before sending.")
    if full:
        raise ChatError(409, "This conversation has grown too long to add to. Start a new "
                             "one to carry on.")
    changes: dict[str, Any] = {}
    if target is not KEEP and target is not None:
        changes["target"] = store.clean_target(target)
    if effort is not KEEP:
        changes["effort"] = store.clean_effort(effort)
    if use_web is not KEEP and not isinstance(use_web, bool):
        raise ChatError(422, "web must be true or false.")
    replaces, replaces_on_record = None, True
    if regenerate:
        asked = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if asked is None:
            raise ChatError(409, "There is nothing to regenerate yet. Send a message first.")
        # The reply on screen to that message, if it has one. (The newest
        # message can be a recorded reply whose question was since deleted
        # off the record: that one stays where it is.)
        old = next((m for m in reversed(messages)
                    if m["role"] == "assistant" and m["reply_to"] == asked["id"]), None)
        if old is not None:
            replaces, replaces_on_record = old["id"], old["on_record"]
        messages = messages[:messages.index(asked) + 1]
    else:
        text = store.clean_text(core, text)
    now = conv | changes
    provider, model, routes, providers = _first_usable(core, now["target"],
                                                       now["local_only"])

    # Nothing below refuses: from here on the send is stored.
    on_record = conv["on_record"]
    if not on_record:
        run.message_id = store.quiet_id()  # says nothing of when, wherever it ends up
    conversations.apply(core, conv, changes, actor=actor)
    since = core.tape.last_seq()
    if regenerate:
        user = store.public_message(core, asked)
    else:
        asked, named = conversations.say(core, conv, text, actor=actor)
        now = now | {"title": named["title"], "title_source": named["title_source"]}
        messages = [*messages, asked]
        user = store.public_message(core, asked, text)
    # The receipt of a reply kept off the record is tied to nothing on the
    # Tape: not to the conversation, and not to the message it answers.
    cause = asked["event"] if on_record else None
    get = core.settings.get
    context, omitted = build_context(core, messages, get("chat.context_messages"),
                                     get("chat.context_chars"))
    options: dict[str, Any] = {
        "system": _system(core), "effort": now["effort"], "actor": actor,
        "caller": "chat.reply", "local_only": now["local_only"],
        # The conversation is already stored here. The receipt must not hold a
        # second copy of the words.
        "keep_text": False, "cause": cause, "root": conv["root"] if on_record else None,
    }
    if now["target"]["kind"] == "model":
        options |= {"provider_id": now["target"]["provider_id"],
                    "model": now["target"]["model"]}
    else:
        options["role"] = now["target"]["role"]
    run.start = {
        "conversation_id": conv["id"], "user_message": user,
        "assistant_message_id": run.message_id, "regenerate": regenerate, "replaces": replaces,
        "provider_id": provider.id, "provider_label": provider.label, "model": model,
        "local": provider.is_local, "omitted": omitted, "web": False,
    }
    wants_title = (get("chat.model_titles") and now["title_source"] == "cut"
                   and sum(1 for m in messages if m["role"] == "user") == 1)
    # A title a model writes is made from the message and its reply, so it
    # may go to the Tape only when both of those are there.
    plan = Plan(now, user, on_record, cause, replaces, context, omitted, options, since, actor,
                wants_title, on_record and asked["on_record"], replaces_on_record, routes)
    _plan_memory(core, run, plan, messages, user["text"], providers)
    _plan_web(core, run, plan, messages, use_web)
    return plan


def _plan_web(core: Core, run: Run, plan: Plan, messages: list[dict[str, Any]],
              asked: Any) -> None:
    """Decide whether this reply is offered the web. ``asked`` is what the
    send said (True, False, or KEEP for "whatever new conversations start
    with"). It only has an effect while web lookup itself is on; a send that
    asked for it by name while it is off is told so."""
    wanted = core.settings.get("lookup.chat_default") if asked is KEEP else asked
    if not wanted:
        return
    if not core.settings.get("lookup.enabled"):
        if asked is True:
            run.web = web.skipped(web.DISABLED)
        return
    plan.web = run.start["web"] = True
    # A receipt names the query or the address. It may only do that when
    # every word the model was shown is itself on the record.
    plan.web_private = not (plan.on_record and all(m["on_record"] for m in messages))
    plan.typed = [m["content"] for m in plan.messages if m["role"] == "user"]
    run.web = web.skipped(web.STOPPED)


def _plan_memory(core: Core, run: Run, plan: Plan, messages: list[dict[str, Any]],
                 asked_text: str, providers: list[Any]) -> None:
    """Decide, before anyone is asked, whether this reply may be given memory
    and what memory will be asked. ``messages`` ends with the message being
    answered. Leaves ``run.memory`` as the reply should record it if nothing
    more happens: None when memory is off, or the reason it was not used."""
    conv = plan.conv
    if not conv["use_memory"]:
        return
    if not recall.may_receive(core, providers, conv["local_only"]):
        # The model that will answer is hosted and the owner has not allowed
        # that: memory is not even searched.
        run.memory = recall.skipped(recall.HOSTED)
        return
    asked = messages[-1]
    # The receipt of a recall carries a keyed hash and the length of the
    # question. For a question asked off the record it must carry neither.
    plan.private = not (plan.on_record and asked["on_record"])
    # What "that" and "it" refer to: the two messages before this one. An
    # exchange on the record is never asked about with words kept off it.
    earlier = [m for m in reversed(messages[:-1])
               if (m["on_record"] or plan.private)
               and (m["role"] == "user" or m["state"] in ("complete", "stopped"))][:2]
    plan.query = recall.build_query(asked_text, [store.text_of(core, m) for m in earlier])
    plan.hosted_ok = bool(core.settings.get("chat.memory_to_hosted"))
    plan.hosted_fallback = any(not p.is_local for p in providers)
    run.memory = recall.skipped(recall.STOPPED)


# --- writing the reply ------------------------------------------------------------


def _whole(text: str) -> str:
    """Text that can always be saved and sent on. A provider may split one
    character (an emoji, say) into two halves across pieces, or send half of
    one alone: halves that belong together are joined, and a stray half becomes
    the replacement character."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return text


def _legs(core: Core, plan: Plan) -> list[dict[str, Any]]:
    """This reply's own failed attempts, from the receipts the Brain printed."""
    if plan.cause is not None:
        events = core.tape.caused_by(plan.cause, limit=200)
    else:
        # Tied to nothing, so they are found by when they were printed.
        events = core.tape.after(plan.since, kinds=("model.failed",), limit=200)
    legs = [e.payload for e in events
            if e.readable and e.kind == "model.failed" and (e.seq or 0) > plan.since
            and e.cause == plan.cause and e.payload.get("caller") == "chat.reply"
            and (plan.cause is not None or e.root == plan.options["root"])]
    if plan.cause is not None:
        return legs
    # Another conversation's reply, also tied to nothing, may have failed in
    # the same moment. Keep only the models this reply could have asked, each
    # once: what is left is the same whichever of the two it came from.
    own: dict[tuple[str, str], dict[str, Any]] = {}
    for leg in legs:
        route = (leg["provider_id"], leg["model"])
        if route in plan.routes:
            own.setdefault(route, leg)
    return list(own.values())


def _finish(core: Core, run: Run, plan: Plan, state: str, text: str, reply: Reply | None,
            error: ModelError | None, started: float) -> None:
    """Store the reply, once, and tell whoever is listening. Never awaits, so a
    stop cannot land half-way through it."""
    if run.finished:
        return
    run.finished = True
    detail = None
    try:
        facts: dict[str, Any] = {
            "provider_id": None, "provider_type": None, "model": None, "local": None,
            "fallback_from": None, "fallback_reason": None, "input_tokens": None,
            "output_tokens": None,
        }
        if reply is not None:
            facts = {
                "provider_id": reply.provider_id, "provider_type": reply.provider_type,
                "model": reply.model, "local": reply.is_local,
                "fallback_from": reply.fallback_from, "fallback_reason": reply.fallback_reason,
                "input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens,
            }
        else:
            # No finished answer, so the Brain handed nothing back. Its receipts
            # still say which provider was writing, and whether another had
            # been passed over first.
            legs = _legs(core, plan)
            if legs:
                last = legs[-1]
                provider = brain(core).provider(last["provider_id"])
                facts |= {"provider_id": last["provider_id"],
                          "provider_type": last["provider_type"], "model": last["model"],
                          "local": provider.is_local if provider else None}
                if len(legs) > 1:
                    facts |= {"fallback_from": f"{legs[0]['provider_id']}/{legs[0]['model']}",
                              "fallback_reason": legs[0]["message"]}
        payload = {
            "message_id": run.message_id,
            "reply_to": plan.user["id"], "replaces": plan.replaces, "state": state,
            # Only our own sentence is kept; what a provider said stays out.
            "error": error.public if error else None,
            "error_kind": error.kind if error else None,
            **facts,
            "duration_ms": int((time.monotonic() - started) * 1000), "omitted": plan.omitted,
            "memory": run.memory, "web": run.web,
        }
        if error is not None and run.kept_local and error.fallback_ok:
            # Say why the hosted model the owner set up as a fallback was not asked.
            payload["error"] += recall.KEPT_LOCAL
        created = conversations.answer(core, plan.conv, payload, text,
                                       on_record=plan.on_record, cause=plan.cause,
                                       replaces_on_record=plan.replaces_on_record)
        run.message = {
            "id": run.message_id, "role": "assistant", "created": created, "text": text,
            "on_record": plan.on_record,
            **{name: payload[name] for name in store.REPLY_FIELDS},
        }
    except ChatError as exc:
        detail = exc.detail
    except Exception as exc:  # noqa: BLE001 - whoever is listening must still be told
        _log_failure("a chat reply could not be stored", exc, plan)
        detail = "The reply could not be saved. Check the disk JimmyHQ's data is on, then " \
                 "regenerate it."
    finally:
        _release(core, run)
    if run.message is None:
        run.emit("error", {"detail": detail, "message": None})
    elif state == "failed":
        detail = error.message if error else run.message["error"]
        if error is not None and run.message["error"].endswith(recall.KEPT_LOCAL):
            detail += recall.KEPT_LOCAL
        run.emit("error", {"detail": detail, "message": run.message})
    else:
        run.emit("done", {"message": run.message})
    if run.settled is not None:
        run.settled.set()


def _log_failure(what: str, exc: Exception, plan: Plan) -> None:
    """Write an unexpected failure to the log. An error can quote what it was
    handling, so for a reply kept off the record only its kind is written."""
    if plan.on_record:
        log.error(what, exc_info=exc)
    else:
        log.error("%s: %s", what, type(exc).__name__)


def _release(core: Core, run: Run) -> None:
    service = store.svc(core)
    with service.lock:
        if service.runs.get(run.conversation_id) is run:
            del service.runs[run.conversation_id]


async def _answer(core: Core, run: Run, plan: Plan) -> None:
    parts: list[str] = []
    held = ""  # the first half of a split character, until the next piece brings the rest
    reply: Reply | None = None
    error: ModelError | None = None
    state = "complete"
    started = time.monotonic()

    def take(item: str) -> None:
        nonlocal held
        piece, held = held + item, ""
        if piece and "\ud800" <= piece[-1] <= "\udbff":
            piece, held = piece[:-1], piece[-1]  # wait for its other half
        piece = _whole(piece)
        if piece:
            parts.append(piece)
            run.emit("delta", {"text": piece})

    async def ask(messages: list[dict[str, Any]], options: dict[str, Any]) -> Reply:
        """Ask the model once, passing its text on as it arrives."""
        answered: Reply | None = None
        stream = brain(core).stream(messages, **options)
        async with contextlib.aclosing(stream):
            async for item in stream:
                if isinstance(item, str):
                    take(item)
                else:
                    answered = item
        if answered is None:
            raise ModelError("bad_reply", "The model sent back nothing. Try again.")
        return answered

    async def guarded(work: Awaitable[Reply]) -> Reply:
        """Run the model's work, and stop it if it stays silent for longer than
        ``chat.silent_reply_limit_s``. Once it has written text or looked
        something up the watch is over: that reply is never cut by it."""
        task = asyncio.ensure_future(work)
        limit = core.settings.get("chat.silent_reply_limit_s")
        since = clock()
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=WATCH_POLL_S)
                if done:
                    return task.result()
                if run.lively or not limit:
                    limit = 0  # a reply that has spoken is never cut
                    continue
                if clock() - since >= limit:
                    task.cancel()
                    await asyncio.wait({task})
                    raise silent_too_long(limit)
        except asyncio.CancelledError:
            # The model call must have wound up, and printed its receipt, before the
            # reply is stored: what that receipt says is part of what is stored.
            task.cancel()
            await asyncio.wait({task})
            raise

    async def write() -> Reply:
        options = plan.options
        if plan.query is not None:
            # Inside the guard: a stop while memory is being searched ends the
            # reply as stopped, like a stop at any other moment.
            run.memory, block = await recall.gather(core, plan.query, actor=plan.actor,
                                                    private=plan.private)
            if block:
                options = options | {"system": recall.with_memory(options["system"], block)}
                if not plan.hosted_ok:
                    # Memory may not reach a hosted model, and a fallback would
                    # carry this same prompt: only local providers may answer.
                    run.kept_local = plan.hosted_fallback and not options["local_only"]
                    options = options | {"local_only": True}
        if run.web is not None and not plan.web:
            # Asked for by name while web lookup is off: say so as it starts.
            run.emit("step", {"type": "failed", "id": None, "detail": run.web["reason"]})
        if plan.web:
            session = web.Session(
                core, actor=plan.actor, private=plan.web_private,
                cause=None if plan.web_private else plan.cause,
                root=None if plan.web_private else options["root"], typed=plan.typed,
                emit=lambda data: run.emit("step", data),
                keep=lambda record: setattr(run, "web", record))
            reply = await web.answer(session, plan.messages, options, ask,
                                     written=lambda: len(parts), gap=lambda: take("\n\n"))
            if not "".join(parts).strip():
                raise ModelError("bad_reply", "The model looked things up but wrote no "
                                              "answer. Regenerate the reply, or raise \"Most "
                                              "lookup rounds in one reply\" under Settings, "
                                              "Web lookup.")
            return reply
        return await ask(plan.messages, options)

    try:
        reply = await guarded(write())
    except ModelError as exc:
        state, error = "failed", exc
        if exc.kind == "truncated" and not "".join(parts).strip():
            error = ModelError("truncated", ALL_REASONING)
    except asyncio.CancelledError:
        # Stopped by the owner, by a listener that went away, or by the server
        # shutting down (see ``shutdown``). Keep what arrived.
        _finish(core, run, plan, "stopped", "".join(parts), None, None, started)
        if run.stop_requested:
            return
        raise
    except Exception as exc:  # noqa: BLE001 - an odd failure must still end the reply properly
        _log_failure("a chat reply failed unexpectedly", exc, plan)
        state = "failed"
        error = ModelError("bad_reply", "Something went wrong while writing the reply. "
                                        "Try again.")
    # A first half still held when the reply ended early never got its other half.
    # A reply written over several rounds is everything that was passed on.
    whole = reply is not None and state == "complete" and not plan.web
    text = _whole(reply.text) if whole else "".join(parts)
    _finish(core, run, plan, state, text, reply, error, started)
    if state == "complete" and plan.wants_title and run.message is not None:
        await _write_title(core, plan, text, reply, run.message_id)


async def _write_title(core: Core, plan: Plan, answer: str, reply: Reply | None,
                       reply_id: str) -> None:
    """Ask the cheap model for a title. Any failure leaves the cut title, silently.

    The title call carries the message and the reply, so it may go no further
    than the reply itself did: a hosted model is asked only when a hosted model
    wrote the reply. Otherwise the Brain is told to stay local, and with no
    local model in the "cheap" role the cut title simply stays."""
    stay_local = plan.conv["local_only"] or reply is None or reply.is_local is not False
    try:
        asked = plan.messages[-1]["content"]
        titled = await brain(core).chat(
            [{"role": "user", "content": f"Message:\n{asked[:_TITLE_EXCERPT]}\n\n"
                                         f"Reply:\n{answer[:_TITLE_EXCERPT]}"}],
            role="cheap", system=_TITLE_SYSTEM, max_tokens=200, actor=plan.actor,
            caller="chat.title", local_only=stay_local, keep_text=False,
            cause=plan.options["cause"], root=plan.options["root"])
        line = next((ln for ln in titled.text.splitlines() if ln.strip()), "")
        title = store.cut_title(line.strip().strip("\"'`*#").rstrip("."), store.MAX_TITLE - 1)
        if title:
            await asyncio.to_thread(_save_title, core, plan, title, reply_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a title is never worth an error
        log.info("no model-written title: %s", type(exc).__name__)


def _save_title(core: Core, plan: Plan, title: str, reply_id: str) -> None:
    with store.svc(core).lock:
        if not plan.title_on_record:
            # Made from words kept off the record. If they were deleted while
            # the title was being written, it goes with them.
            kept = {m["id"] for m in conversations.story(core, plan.conv["id"])[1]}
            if not {plan.user["id"], reply_id} <= kept:
                return
        conv = conversations.find(core, plan.conv["id"])
        if conv["title_source"] == "cut":  # the owner has not renamed it meanwhile
            # On the Tape only if the exchange it was made from is there, the
            # conversation is still on the record and the title shown is the Tape's.
            on_record = (plan.title_on_record and conv["on_record"]
                         and not conv["off_record_title"])
            conversations.apply(core, conv, {"title": title, "title_source": "model"},
                                actor="system", title_on_record=on_record)


def _ended(core: Core, run: Run, plan: Plan, task: asyncio.Task) -> None:
    """The task is over, however that happened. If it never got to store its
    reply (stopped before it took a step), store it as stopped now."""
    if not task.cancelled():
        task.exception()  # looked at, so asyncio does not warn about it
    if not run.finished:
        _finish(core, run, plan, "stopped", "", None, None, time.monotonic())
    _release(core, run)


# --- the public face --------------------------------------------------------------


async def begin(core: Core, conversation_id: Any, *, text: Any = None,
                regenerate: bool = False, target: Any = KEEP, effort: Any = KEEP,
                web: Any = KEEP, actor: str = "owner") -> Run:
    """Check a send, record the person's message and start the reply.

    ``web`` is whether this reply may look things up on the web: True, False,
    or left out for the ``lookup.chat_default`` setting.

    Raises ``ChatError`` for anything that should be an ordinary refusal.
    After it returns, listen with ``listen(run)``.
    """
    if not isinstance(conversation_id, str) or not store.CONV_ID.fullmatch(conversation_id):
        raise ChatError(404, store.NOT_FOUND)
    service = store.svc(core)
    run = Run(conversation_id)
    with service.lock:
        if conversation_id in service.runs:
            raise ChatError(409, BUSY)
        service.runs[conversation_id] = run
    try:
        plan = await asyncio.to_thread(_prepare, core, run, conversation_id, text, regenerate,
                                       target, effort, actor, web)
    except BaseException:
        _release(core, run)
        raise
    run.loop = asyncio.get_running_loop()
    run.queue = asyncio.Queue()
    run.settled = asyncio.Event()
    run.task = run.loop.create_task(_answer(core, run, plan))
    run.task.add_done_callback(lambda task: _ended(core, run, plan, task))
    return run


async def listen(run: Run, *, disconnected: Callable[[], Awaitable[bool]] | None = None,
                 poll_s: float = 1.0, keepalive_s: float = 15.0
                 ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """A reply's events as ``(name, data)``: "start", any number of "delta"
    (and, with web lookup, "step"), then exactly one of "done" or "error".
    "ping" may appear while the model is quiet. If the listener goes away
    first, the reply is stopped."""
    assert run.queue is not None  # noqa: S101 - begin() always sets it
    try:
        yield "start", run.start
        quiet = 0.0
        while True:
            try:
                name, data = await asyncio.wait_for(run.queue.get(), poll_s)
            except TimeoutError:
                if disconnected is not None and await disconnected():
                    return
                quiet += poll_s
                if quiet >= keepalive_s:
                    quiet = 0.0
                    yield "ping", {}
                continue
            quiet = 0.0
            yield name, data
            if name in ("done", "error"):
                return
    finally:
        run.stop()  # does nothing once the reply has ended


async def send(core: Core, conversation_id: str, text: str, *, target: Any = KEEP,
               effort: Any = KEEP, web: Any = KEEP, actor: str = "owner"
               ) -> AsyncIterator[str | dict[str, Any]]:
    """Send a message and yield the reply: pieces of text as they arrive, then
    the stored reply as a dict (its ``state`` is "complete", "stopped" or
    "failed"; a failed one carries ``error``).

    ``target`` and ``effort``, when given, also become the conversation's new
    defaults. ``web`` lets this one reply look things up on the web (see
    ``begin``); the stored reply's ``web`` says what it used. Raises
    ``ChatError`` before anything is yielded if the send is refused. Closing
    the iterator early stops the reply and keeps what arrived.
    """
    run = await begin(core, conversation_id, text=text, target=target, effort=effort,
                      web=web, actor=actor)
    async with contextlib.aclosing(_pieces(run)) as pieces:
        async for item in pieces:
            yield item


async def regenerate(core: Core, conversation_id: str, *, target: Any = KEEP,
                     effort: Any = KEEP, web: Any = KEEP, actor: str = "owner"
                     ) -> AsyncIterator[str | dict[str, Any]]:
    """Write a new reply to the conversation's last message from the person.
    Yields what ``send`` yields. The new reply is kept where the switch says
    now, whatever it said when the message was sent. The earlier reply, if
    there was one, leaves the screen: one on the Tape stays there, one off
    the record is removed."""
    run = await begin(core, conversation_id, regenerate=True, target=target, effort=effort,
                      web=web, actor=actor)
    async with contextlib.aclosing(_pieces(run)) as pieces:
        async for item in pieces:
            yield item


async def _pieces(run: Run) -> AsyncIterator[str | dict[str, Any]]:
    async with contextlib.aclosing(listen(run)) as events:
        async for name, data in events:
            if name == "delta":
                yield data["text"]
            elif name in ("done", "error"):
                if data["message"] is None:
                    raise ChatError(500, data["detail"])
                yield data["message"]
    # The reply is stored; a model-written title may still be on its way. A
    # Python caller's loop often ends with the iterator, so let that finish.
    if run.task is not None:
        await asyncio.wait({run.task})


async def stop(core: Core, conversation_id: Any, *, wait_s: float = 5.0) -> dict[str, Any]:
    """Stop the reply being written in a conversation, if there is one, and
    wait briefly for what had arrived to be stored."""
    run = store.svc(core).runs.get(conversation_id) if isinstance(conversation_id, str) else None
    if run is None or run.task is None:
        conversations.find(core, conversation_id)  # a 404 if it does not exist
        return {"stopped": False}
    run.stop()
    if run.loop is asyncio.get_running_loop() and run.settled is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run.settled.wait(), wait_s)
    return {"stopped": True}


async def shutdown(core: Core, *, wait_s: float = 3.0) -> None:
    """The server is stopping: stop every reply being written and wait briefly
    for each to be stored as stopped, with the text that had arrived and the
    model's receipt. Without this the process would end before either is
    printed, and the Tape would hold a message with no answer."""
    service = store.svc(core)
    with service.lock:
        runs = list(service.runs.values())
    here = asyncio.get_running_loop()
    waiting = []
    for run in runs:
        run.stop()
        if run.loop is here and run.settled is not None and not run.finished:
            waiting.append(run.settled.wait())
    if waiting:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(*waiting), wait_s)
