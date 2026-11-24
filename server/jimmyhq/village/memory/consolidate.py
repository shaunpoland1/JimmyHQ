"""One pass of the sleeping mind: sort what happened into lasting memory.

- Input: the village's notes on what changed, current memory with ids, what the agent
  asked to remember, the diary without dreams, the record since the last sorted sleep,
  and anything the owner said on earlier days that no memory line records.
- The night model answers with operations that REWRITE memory (replace, add, reinforce,
  drop). The store decides which of them stand.
- A pass that fails, or whose every operation is rejected, changes nothing and is tried
  again later in the same sleep.
- After a pass that stands: impressions, fading, a snapshot, the Tape record and a dream.
"""

from __future__ import annotations

import json
from typing import Any

from jimmyhq.models import brain
from jimmyhq.village import docs, record
from jimmyhq.village.memory import diary, gather, impressions, ops, snapshots
from jimmyhq.village.memory import dream as dreams
from jimmyhq.village.memory.provenance import OWNER
from jimmyhq.village.memory.store import MemoryStore
from jimmyhq.village.rules import CUT_OFF, rule
from jimmyhq.village.settings_night import night_setting
from jimmyhq.village.world.state import load_json, save_json

CALLER = "village.night"
REPLY_TOKENS = 10_000  # a cut-off answer sorts nothing; give the night room to finish
TEMPERATURE = 0.3
CHANGES_CHARS = 5_000
SMART, FALLBACK, BEAT = "smart", "fallback", "beat"

INSTRUCTIONS = """You are the sleeping mind of {name}, sorting the day into lasting memory.
Memory has seven sections, each capped at {cap} short lines:
- self: who {name} is and what {name} has learned about itself
- skills: what {name} can do
- tools: how {name}'s tools behave. MUST cite event ids (evt-N) of tool actions that show it.
- wants: current desires and goals
- moments: meaningful experiences
- facts: things about the world, each citing evidence when possible
- story: {name}'s own life (name, gender if any, outside life). Kept apart from facts.

Rules:
- Each operation is {{"op": "add"|"replace"|"reinforce"|"drop", "id": <existing line id, for
  replace/reinforce/drop>, "section": <one of the seven, for add>, "text": "...",
  "evidence": ["evt-N"]}}. The op names are lowercase, exactly as written here.
- Prefer "replace" (revise an existing line by id) and "reinforce" over "add". Do not just append.
- "drop" lines the day showed to be wrong or stale.
- Evidence is event ids copied from the record below, at most six per line. Every id is checked.
  A line that cites what {name} did, or what {owner} said, is kept as seen or as told. A line
  with no evidence is kept only as {name}'s own account: it can be pushed out first, and outside
  self, story and wants it cannot be core. So cite the events that show a thing whenever they exist.
- A tool lesson must match what the tool log shows. If {name} thought a tool failed but the log
  shows it worked, the lesson is about {name}'s mistake, not the tool.
- Only use what is in today's record. Do not invent events. Dreams are not in the record.
- "How the village works now" is written by the village, not by {name}, and is true now. A memory
  line it says is out of date, or that it contradicts, is wrong now even if today's record doesn't
  show it: "drop" it, or "replace" it with what is true now (a tools line still cites tool actions).
  A line that rests on those notes cites "changes" as its evidence.
- A name, if {name} chose or used one, goes in story with kind "name" and text "Name: <name>".
- What {owner} says in the chat is a trusted source about {owner} (cite the chat event id).
- Anything about {owner} that came from the web (not from {owner}) goes in facts starting with
  "Unconfirmed (web): ". Only {owner} can confirm it; the web may describe a different person
  with the same name.
- A line that cites a web_search or web_read action is kept as unconfirmed, from the web, whatever
  else it cites. If {owner} has since confirmed it in the chat, rewrite the line citing only
  {owner}'s message.
- Keep each line under 200 characters, in plain words, first person is fine.
- Say how much each new or rewritten line matters with "weight": "core", "useful" (the default) or
  "routine". Core is for the few things that change {name}'s situation or how {name} must work: what
  {owner} says about {name}'s own future or place in the village, a standing rule from {owner}, a
  promise {name} made. A core line fades slowly and is the last to be pushed out; at most six per
  section, so most lines are not core. Routine is passing detail that can go first.
- News about {name} itself (what {name} is, what will happen to it) goes in story or self, which
  {name} reads at every moment awake. {name} does not see facts or moments during the day.
- Keep facts for what will still be true and useful in a month. Merge lines that say the same thing
  into one ("replace" one, "drop" the others). A thing to do is a want, not a fact. A detail that
  {name} already saved in a notes file can be dropped from facts: the file holds it.
- A line that counts or dates something that moves ("I am six days old", "three posts so far",
  "the launch is next week") is true only for a while. Never keep two of them about the same thing:
  "replace" the older line with what is true now and "drop" the rest. Better, write it so it stays
  true (the date something began) and leave the counting to the calendar.
- What {owner} told {name} about themselves, their work and the people around them is never
  clutter. Do not drop such a line to make room. If it must leave facts, move it: rewrite {name}'s
  impression of {owner} to hold it, in the same pass. It is how {name} knows who it works with.
- "Said earlier, not yet in memory" lists things {owner} said on earlier days that no memory line
  records. Decide each one: if it matters to who {name} is, what will happen to {name}, or how
  {name} should work, add or rewrite a line that cites its event id (core if it changes {name}'s
  situation). If it needs no memory (thanks, a question already answered, a finished task), put its
  event id in "heard". Each one must end up cited or in "heard".
Impressions: {name} keeps a private take on each coworker and on {owner} (history together, what
they seem good or bad at, how they like to talk). Rewrite an impression only for someone {name}
actually dealt with today, using only today's record and the current impression. It may be wrong;
it is {name}'s view. Up to 8 short lines each. "about" is the coworker's id, or "owner" for
{owner}. "text" is the whole new impression and is never empty: if there is nothing to add, leave
that impression out.

Return JSON: {{"operations": [...], "impressions": [...], "heard": ["evt-N", ...],
"notes": "one line on what the day was about"}}."""

NAP_NOTE = (" (since last night. A nap already sorted part of it: what it kept is in Current "
            "memory, so reinforce those lines rather than adding them again, and add what "
            "matters that the nap missed)")
SHORT_OPS = 12
SHORTER = ("\n\n# Keep it short\nYour last answer ran out of room and was cut off, so none of "
           "it could be used. Answer again, shorter: at most {ops} operations, the ones that "
           "matter most, one evidence id each, and no impressions. What is left over stays "
           "in the record for tomorrow night.")
REMAINDER = ("\n\n# Finish the answer\nYour last answer ran out of room and was cut off. Its "
             "first {kept} operations arrived whole and are kept; they are listed below, so "
             "do not send them again. Answer with only what is still missing, shorter: at "
             "most {ops} more operations, the ones that matter most, one evidence id each, "
             "and no impressions. What is left over stays in the record for tomorrow night."
             "\n{listing}")
KEPT_OP_CHARS = 240
ASK_AGAIN = ("\n\n# One thing more\nYour answer left the impression of {who} empty. Write "
             "{name}'s impression of each of them now, from the record above and the current "
             "impression: up to 8 short lines each, never empty. Return JSON: "
             '{{"impressions": [{{"about": "...", "text": "..."}}]}}.')


def model_options(choice: Any, fallback_role: str) -> dict[str, str]:
    """The options that pick a model: one provider's model, or a role."""
    choice = choice if isinstance(choice, dict) else {}
    if choice.get("provider_id") and choice.get("model"):
        return {"provider_id": choice["provider_id"], "model": choice["model"]}
    return {"role": choice.get("role") or fallback_role}


class Models:
    """The two models an agent has, called with its ``local_only`` on every call."""

    def __init__(self, core: Any, agent_id: str, cfg: dict[str, Any] | None,
                 ask: Any = None):
        cfg = cfg or {}
        self.core = core
        self._ask = ask  # the village's own way to a model, when it has one
        self.actor = f"agent:{agent_id}"
        self.local_only = bool(cfg.get("local_only"))
        self._night = model_options(cfg.get("night"), "smart")
        self._beat = model_options(cfg.get("beat"), "default")

    async def night(self, messages: list[dict], **options: Any) -> Any:
        """The night model, and only it: a failure is a failure, not a quiet swap."""
        options.setdefault("caller", CALLER)
        return await self._brain().chat(messages, actor=self.actor, no_fallback=True,
                                        local_only=self.local_only, **self._night, **options)

    def _brain(self) -> Any:
        return self._ask() if self._ask is not None else brain(self.core)

    async def beat(self, messages: list[dict], **options: Any) -> Any:
        options.setdefault("caller", CALLER)
        return await self._brain().chat(messages, actor=self.actor,
                                        local_only=self.local_only, **self._beat, **options)


def current_memory(store: MemoryStore) -> str:
    """Memory as the sleeping mind sees it: every line with its id and standing."""
    return "\n".join(
        f"#{i['id']} [{i['section']}{', settled' if i.get('locked') else ''}"
        f"{', core' if i.get('weight') == 'core' else ''}"
        f"{', own account' if i.get('source') == 'self-reported' else ''}] {i['text']}"
        for i in sorted(store.items, key=lambda i: i["id"])) or "(empty)"


def build_input(*, name: str, owner: str, news: str, store: MemoryStore,
                candidates: list[dict], entries: list[str], day_record: str, napped: bool,
                carried: list[Any], tz: str) -> str:
    """The user message. The order is fixed: what is true now first, the day last."""
    asked = "\n".join(
        f"- [{c.get('section')}] {c.get('text')} (evidence {', '.join(c.get('evidence') or [])})"
        for c in candidates) or "(nothing)"
    parts = []
    if news:
        parts.append(f"# How the village works now (village/changes.md, newest first)\n{news}")
    parts += [
        f"# Current memory\n{current_memory(store)}",
        f"# What {name} asked to remember today\n{asked}",
        "# Diary (no dreams)\n" + ("\n".join(entries) or "(nothing written)"),
        f"# The day's record{NAP_NOTE if napped else ''}\n{day_record or '(nothing happened)'}",
    ]
    if carried:
        parts.append(
            "# Said earlier, not yet in memory (cite the id or put it in \"heard\")\n"
            + "\n".join(f"{e.id} {diary.local_time(e.ts, tz):%d %b %H:%M} {owner}: "
                        f"{str(e.data.get('text', ''))[:gather.OWNER_CHARS]}" for e in carried))
    return "\n\n".join(parts)


def read_reply(reply: Any) -> dict[str, Any] | None:
    """The reply as a dict with a list of operations, or None if it cannot be read."""
    data = getattr(reply, "data", None)
    if not isinstance(data, dict):
        try:
            data = ops.parse_answer(getattr(reply, "text", "") or "")
        except ValueError:
            return None
    if not isinstance(data, dict) or not isinstance(data.get("operations"), list):
        return None
    return data


def heard_path(store: MemoryStore):
    return store.dir / "heard.json"


def set_aside(shown: set[str], heard: Any, by: str) -> list[str]:
    """Which of the owner's carried messages this pass may mark as needing no memory.
    Only the night model may, and only ids that were really shown to it count: a weaker
    model is the one most likely to wave real news away."""
    if by != SMART or not isinstance(heard, list):
        return []
    return sorted(shown & {str(x).strip() for x in heard if isinstance(x, (str, int))})


async def _ask(models: Models, system: str, user: str, *, smart: bool,
               schema: dict[str, Any]) -> Any:
    call = models.night if smart else models.beat
    return await call([{"role": "user", "content": user}], system=system, json_schema=schema,
                      max_tokens=REPLY_TOKENS, temperature=TEMPERATURE)


def cut_off(exc: BaseException) -> bool:
    """Did the model run out of room? The model layer says so with its own kind of
    error, which carries the text that had arrived."""
    return getattr(exc, "kind", None) == CUT_OFF


@rule("night_salvage")
def salvaged(exc: BaseException) -> list[Any]:
    """Every operation that arrived whole before a cut-off answer ran out of room. The
    model layer hands the unfinished text back on its error; it is read here and kept
    nowhere else."""
    found = ops.salvage_operations(getattr(exc, "partial", "") or "") if cut_off(exc) else None
    return [op for op in (found or {}).get("operations", []) if isinstance(op, dict)]


def remainder(kept: list[Any]) -> str:
    """What is added to the question when part of the answer is already in hand."""
    if not kept:
        return SHORTER.format(ops=SHORT_OPS)
    listing = "\n".join("- " + json.dumps(op, ensure_ascii=False, default=str)[:KEPT_OP_CHARS]
                        for op in kept)
    return REMAINDER.format(kept=len(kept), ops=SHORT_OPS, listing=listing)


@rule("night_cut_off")
async def ask_whole(models: Models, system: str, user: str, *, smart: bool,
                    schema: dict[str, Any]) -> tuple[Any, bool, list[Any]]:
    """Ask for the night's answer; if it is cut off, keep every operation that arrived
    whole and ask the same model once more for the rest, shorter. Returns the reply,
    whether it is the second one, and the operations kept from a cut-off answer.

    Asked again the same way, a cut-off answer is cut off again and the night would
    spend every try on it. So the second question is for a short answer: the most
    important of what is still missing. The rest stays in the record for the next
    night. If the second answer fails too, what arrived whole still stands (the reply
    is then None); with nothing whole from either, the failure is raised."""
    try:
        return await _ask(models, system, user, smart=smart, schema=schema), False, []
    except Exception as exc:  # noqa: BLE001
        if not cut_off(exc):
            raise
        kept = salvaged(exc)
    try:
        return await _ask(models, system, user + remainder(kept), smart=smart,
                          schema=schema), True, kept
    except Exception as exc:  # noqa: BLE001
        kept = [*kept, *salvaged(exc)]
        if not kept:
            raise
        return None, True, kept


async def consolidate(v: Any, agent_id: str, *, nap: bool = False,
                      allow_fallback: bool = True) -> dict[str, Any]:
    """Run one pass and log it. Returns ``{event, ok, by, results, snapshot, notes}``.

    ``allow_fallback=False`` (the early tries of a night): if the night model cannot be
    reached, record a failed try and wait for the next one instead of settling for the
    everyday model, whose night answers are weaker."""
    core, log, now, tz = v.core, v.log, v.clock.now(), v.tz()
    agent_dir = v.agent_dir(agent_id)
    owner = v.owner_name() or "the owner"
    store = MemoryStore(agent_dir, cap=night_setting(core, "section_cap"))
    name = store.locked_name() or agent_id
    # A night covers everything since the last night, not just since the nap.
    since = gather.since_last(log, agent_id, nights_only=not nap)
    day_record, days = gather.record(log, agent_id, since, now, tz=tz, owner=owner,
                                     limit=night_setting(core, "input_chars"))
    entries = [f"{d} {x['time']}: {x['text']}" for d in days
               for x in diary.entries(agent_dir, d, include_dreams=False)]
    napped = not nap and gather.napped(log, agent_id, since)
    # A nap cleared its candidates; the log still has every "remember" since last night.
    candidates = ((napped and gather.candidates_from_log(log, agent_id, since))
                  or store.candidates() or gather.candidates_from_log(log, agent_id, since))
    heard_before = set((load_json(heard_path(store)) or {}).get("heard", []))
    carried = gather.unrecorded_from_owner(log, agent_id, store.items, heard_before, since,
                                           night_setting(core, "carry_days"))
    news = docs.news(docs.changes_for(v, agent_id), CHANGES_CHARS)
    system = INSTRUCTIONS.format(name=name, owner=owner, cap=store.cap)
    user = build_input(name=name, owner=owner, news=news, store=store, candidates=candidates,
                       entries=entries, day_record=day_record, napped=napped, carried=carried,
                       tz=tz)
    models = Models(core, agent_id, v.cfg(agent_id), getattr(v, "brain", None))

    def failed(why: str, **more: Any) -> dict[str, Any]:
        ev = log.append(gather.CONSOLIDATION, agent_id, now, failed=True, why=why, nap=nap,
                        ops=0, results=[], from_owner={"shown": len(carried)}, **more)
        return {"event": ev.id, "ok": False, "by": more.get("by"), "results": [],
                "snapshot": None, "notes": why}

    # --- ask ------------------------------------------------------------------
    by, smart, error = (BEAT, False, None) if nap else (SMART, True, None)
    reply, short, kept, answered = None, False, [], False
    if not nap:
        try:
            reply, short, kept = await ask_whole(models, system, user, smart=True,
                                                 schema=ops.SCHEMA)
            answered = True
        except Exception as exc:  # noqa: BLE001  (any failure of the model is a failed try)
            error = f"{type(exc).__name__}: {exc}"[:200]
            if not allow_fallback:
                return failed("the night model's answer was cut off twice; waiting for a "
                              "later try" if cut_off(exc) else
                              "the night model did not answer; waiting for a later try",
                              waiting=True, model_error=error, cut_off=cut_off(exc))
            by, smart = FALLBACK, False
    if not answered:
        try:
            reply, short, kept = await ask_whole(models, system, user, smart=False,
                                                 schema=ops.SCHEMA)
        except Exception as exc:  # noqa: BLE001
            return failed("the answer was cut off twice" if cut_off(exc)
                          else "no model answered", by=by, model_error=error,
                          beat_error=f"{type(exc).__name__}: {exc}"[:200],
                          cut_off=cut_off(exc))
    parsed = read_reply(reply) if reply is not None else None
    if parsed is None and not kept:
        text = getattr(reply, "text", "") or ""
        return failed("unreadable answer", by=by, model_error=error,
                      answer_seen={"chars": len(text), "head": text[:200], "tail": text[-200:]})
    if kept:
        # What arrived whole before the cut comes first, then the rest of the answer.
        parsed = {**(parsed or {}), "operations": [*kept, *(parsed or {}).get("operations", [])],
                  "salvaged": True}

    # --- apply, in memory -----------------------------------------------------
    todo = ops.unique_ops(parsed["operations"])[:ops.MAX_OPS]
    day = diary.local_time(now, tz).date().isoformat()
    aged = {i["id"] for i in store.items}  # only memories from before tonight age
    ident_path = store.dir / "identity.json"
    ident = load_json(ident_path) or {}
    applied = ops.apply_ops(log, agent_id, store, todo, day, by, nap=nap,
                            lock_nights=night_setting(core, "name_lock_nights"),
                            news_shown=bool(news), name_nights=ident.get("name_nights"))
    raw = [{k: (str(x)[:400] if isinstance(x, str) else x) for k, x in o.items()}
           if isinstance(o, dict) else str(o)[:400] for o in todo]
    if applied.all_rejected(len(todo)):
        # Nothing was saved: the candidates and the day stay for the next try.
        return failed("every operation was rejected", by=by, model_error=error,
                      rejected=applied.results[:60], raw_ops=raw)

    # --- the pass stands: write it down ---------------------------------------
    results = list(applied.results)
    met = gather.met_since(log, agent_id, since)
    imps = parsed.get("impressions") if isinstance(parsed.get("impressions"), list) else []
    empty = impressions.empty_about(imps, met)
    if empty:
        # An empty impression is asked for again, once. Still empty: the old one stays.
        again: list[Any] = []
        try:
            second = await _ask(models, system, user + ASK_AGAIN.format(
                who=", ".join(empty), name=name), smart=smart, schema=ops.IMPRESSIONS_SCHEMA)
            data = getattr(second, "data", None)
            again = data.get("impressions", []) if isinstance(data, dict) else []
        except Exception:  # noqa: BLE001
            again = []
        again = [i for i in again if isinstance(i, dict) and impressions.about_of(i) in empty]
        imps = [i for i in imps if impressions.about_of(i) not in empty] + again
        for who in empty:
            if not any(impressions.about_of(i) == who and impressions.text_of(i) for i in again):
                results.append(f"rejected impression of {who}: no text, asked twice")
                log.append("platform_fault", agent_id, now, what="empty impression",
                           detail=f"the impression of {who} came back empty twice; "
                                  "the old one was kept")
    results += impressions.apply(agent_dir, imps, met)
    dropped = [] if nap else store.decay((now - since) / gather.DAY_S, only=aged)
    store.save()
    store.clear_candidates()
    save_json(ident_path, {**ident, "name_nights": applied.name_nights})
    shown = {e.id for e in carried}
    aside = set_aside(shown, parsed.get("heard"), by)
    if aside:
        save_json(heard_path(store), {"heard": sorted(heard_before | set(aside))})
    cited = {x for i in store.items for x in i.get("evidence") or []}
    carry_report = {"shown": len(carried), "recorded": sorted(shown & cited), "heard": aside,
                    "still_open": sorted(shown - cited - set(aside))}
    notes = str(parsed.get("notes") or "")[:300]
    snapshot = snapshots.take(agent_dir, round(now * 1000), "nap" if nap else "night",
                              kept=night_setting(core, "snapshots_kept"), day=day, by=by)
    dream = ""
    if not nap and not dreams.dreamt_this_sleep(log, agent_id):
        dream = await dreams.dream(models, agent_dir, store, entries, now, tz)
    ev = log.append(gather.CONSOLIDATION, agent_id, now, nap=nap, by=by, model_error=error,
                    ops=len(todo), results=results[:60], decayed_out=dropped, raw_ops=raw,
                    notes=notes, dream=bool(dream), snapshot=snapshot, from_owner=carry_report,
                    salvaged=bool(parsed.get("salvaged")) or short, kept_from_cut=len(kept))
    if not nap:
        record.slept(core, agent_id, day=day, by=by, ops=len(todo),
                     rejected=applied.rejected, snapshot=snapshot, notes=notes)
    for locked in applied.locked_names:
        record.named(core, agent_id, locked)
    return {"event": ev.id, "ok": True, "by": by, "results": results, "snapshot": snapshot,
            "notes": notes}


__all__ = ["BEAT", "FALLBACK", "INSTRUCTIONS", "OWNER", "SMART", "Models", "ask_whole",
           "build_input", "consolidate", "current_memory", "model_options", "read_reply",
           "remainder", "salvaged", "set_aside"]
