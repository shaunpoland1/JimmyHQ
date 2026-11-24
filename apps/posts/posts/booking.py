"""The one booking path. Every booking, whoever asks (a click, approval, later an agent's
ladder row), goes through ``book``:

1. the pre-checks, each a named function: the post is queued; no attempt is unsure or
   still waiting; the page has what the scheduler needs; the slot is far enough ahead;
   the page is under its posts a day; no other post of that account is in that minute;
2. the attempt is recorded, and ``booking.attempted`` written, before anything is sent;
3. the scheduler is asked once. A definite no puts the post back in the queue with the
   reason (it clears itself). No answer makes it ``unsure``, which blocks another try
   until reconcile finds it, or the owner says they checked. Nothing ever retries by itself.

A booking that reached some accounts for certain and none of the rest (``partial``) is
booked on those; the post says which it lacks, and "Book the rest" (``book`` on a booked
post) books only those. No path books an account again that holds a copy Posts knows of:
"I checked" puts a post back in the queue only when no copy is known anywhere.

Moving, editing and cancelling a booked post go through the scheduler the same way. An
edit the scheduler cannot take is kept here and the post says plainly that it did not
reach the scheduler. A change that reached only some of a post's copies is recorded for
what it is: the post is out of step and says which accounts have it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from posts import kinds, pictures, states
from posts.checks.text import strip_markdown
from posts.context import Ctx
from posts.schedulers import Booking, Outcome, accounts_for
from posts.store import LIVE_UPSTREAM

MINUTE_MS = 60_000
SLOT_DAYS = 90  # how far ahead a free slot is looked for


class BookingError(ValueError):
    """Posts did not book, move, edit or cancel. The message says why and what to do."""


# --- what is sent ---------------------------------------------------------------------------


def booked_text(post: dict[str, Any], piece: dict[str, Any], sponsor_tag: str) -> str:
    """The text as it is booked: plain, with the sponsor tag at the end when the piece is
    sponsored and the text does not carry it."""
    text, _ = strip_markdown(post["body"])
    text = text.strip()
    tag = (sponsor_tag or "").strip()
    if piece.get("sponsored") and tag and tag.lower() not in text.lower():
        text = f"{text}\n\n{tag}"
    return text


def pictures_of(ctx: Ctx, post: dict[str, Any]) -> tuple[dict[str, str], ...]:
    """The post's pictures in the order they are shown, each ``{ref, name}``, from
    ``pictures.for_post``: a post made from pictures is booked with every one of them. A
    test may hand its own finder as ``ctx.pictures_for``."""
    finder = getattr(ctx, "pictures_for", None) or pictures.for_post
    return tuple({"ref": str(p["ref"]), "name": str(p.get("name") or p["ref"])}
                 for p in finder(ctx, post["id"]) or ())


# --- counting -------------------------------------------------------------------------------


def posts_on_day(ctx: Ctx, page: dict[str, Any], day: str, *, among: frozenset[str],
                 skip_post: int | None = None) -> int:
    marks = ",".join("?" * len(among))
    rows = ctx.store.all(f"SELECT id, slot_ms FROM post WHERE page_id=? AND state IN ({marks}) "  # noqa: S608
                         "AND slot_ms IS NOT NULL", (page["id"], *sorted(among)))
    return sum(1 for r in rows if r["id"] != skip_post and ctx.day(r["slot_ms"], page) == day)


def accounts_in_minute(ctx: Ctx, slot_ms: int, *, skip_post: int | None,
                       among: frozenset[str]) -> set[str]:
    """The accounts already holding a post in the minute of ``slot_ms``: by each post's
    own time, and by the time each copy holds on its scheduler when that differs (a move
    that reached only some copies, or a time changed there)."""
    minute = slot_ms // MINUTE_MS
    start, end = minute * MINUTE_MS, minute * MINUTE_MS + MINUTE_MS - 1
    marks = ",".join("?" * len(among))
    taken: set[str] = set()
    for copy in ctx.store.all(f"SELECT u.account, u.post_id FROM upstream u JOIN post p ON "  # noqa: S608
                              f"p.id=u.post_id WHERE p.state IN ({marks}) AND u.state IN "
                              "(?, ?) AND u.slot_ms BETWEEN ? AND ?",
                              (*sorted(among), *LIVE_UPSTREAM, start, end)):
        if copy["post_id"] != skip_post:
            taken.add(copy["account"])
    for other in ctx.store.all(f"SELECT * FROM post WHERE state IN ({marks}) AND slot_ms "  # noqa: S608
                               "BETWEEN ? AND ?", (*sorted(among), start, end)):
        if other["id"] == skip_post:
            continue
        page = ctx.store.page(other["page_id"])
        if page is not None:
            taken |= {a["account"] for a in accounts_for(scheduler_id_of(ctx, other, page),
                                                         page)}
    return taken


# --- the pre-checks, one function each; None means it passes --------------------------------


def must_be_queued(post: dict[str, Any], lacking: list[str] | None = None) -> str | None:
    """A queued post can be booked; a booked one only for the accounts it lacks."""
    if post["state"] == "queued" or (post["state"] == "booked" and lacking):
        return None
    if post["state"] == "booked":
        return "This post is booked on every one of its accounts already. Nothing was sent."
    return (f"This post is {states.WORDS.get(post['state'], post['state'])}, and only a "
            "queued post can be booked. Nothing was sent.")


def no_open_attempt(ctx: Ctx, post: dict[str, Any]) -> str | None:
    for attempt in ctx.store.attempts(post["id"]):
        if attempt["outcome"] == "unsure":
            return ("An earlier booking of this post got no clear answer, so it may be "
                    "booked already. Nothing was sent. Posts looks for it on the scheduler "
                    "at its next check, and books nothing again until it knows.")
        if attempt["outcome"] == "pending":
            return ("A booking of this post is still waiting for its answer. Nothing was "
                    "sent. Wait for it to finish.")
    return None


def page_is_ready(sched: Any, page: dict[str, Any]) -> str | None:
    lacks = sched.needs(page)
    return f"{lacks} Nothing was sent." if lacks else None


def slot_is_ahead(slot_ms: int | None, now_ms: int, lead_minutes: int) -> str | None:
    if slot_ms is None:
        return "This post has no time to go out. Choose one, then book. Nothing was sent."
    if slot_ms <= now_ms:
        return "That time has passed. Choose a time in the future. Nothing was sent."
    if slot_ms < now_ms + lead_minutes * MINUTE_MS:
        return (f"That time is less than {lead_minutes} minutes away, and Posts books at "
                "least that far ahead. Choose a later time. Nothing was sent.")
    return None


def under_daily_limit(ctx: Ctx, page: dict[str, Any], slot_ms: int, most: int,
                      skip_post: int | None) -> str | None:
    if most <= 0:
        return None
    day = ctx.day(slot_ms, page)
    have = posts_on_day(ctx, page, day, among=states.POST_ON_SCHEDULE, skip_post=skip_post)
    if have < most:
        return None
    return (f"{page['name']} already has {have} post{'s' if have != 1 else ''} on {day}, "
            f"its limit for a day. Choose another day, or raise \"Posts a day for each "
            "brand\". Nothing was sent.")


def minute_is_free(ctx: Ctx, sched_id: str, page: dict[str, Any], slot_ms: int,
                   skip_post: int | None) -> str | None:
    mine = {a["account"] for a in accounts_for(sched_id, page)}
    clash = mine & accounts_in_minute(ctx, slot_ms, skip_post=skip_post,
                                      among=states.POST_ON_SCHEDULE)
    if not clash:
        return None
    return ("Another post of the same account goes out in that minute. Choose another "
            "time. Nothing was sent.")


def prechecks(ctx: Ctx, post: dict[str, Any], sched: Any, slot_ms: int | None,
              lacking: list[str] | None = None) -> list[str]:
    page = ctx.store.page(post["page_id"]) or {}
    cfg = ctx.cfg()
    found = [must_be_queued(post, lacking), no_open_attempt(ctx, post),
             page_is_ready(sched, page),
             slot_is_ahead(slot_ms, ctx.now(), int(cfg.book_lead_minutes))]
    if slot_ms is not None and slot_ms > ctx.now():
        found += [under_daily_limit(ctx, page, slot_ms, int(cfg.posts_per_page_per_day),
                                    post["id"]),
                  minute_is_free(ctx, sched.id, page, slot_ms, post["id"])]
    return [f for f in found if f]


# --- slots ----------------------------------------------------------------------------------


def propose_slot(ctx: Ctx, page: dict[str, Any], *, skip_post: int | None = None) -> int | None:
    """The page's next free posting time in its own time zone: at least the lead time
    ahead, on a day under the page's limit, in a minute none of its accounts holds."""
    cfg = ctx.cfg()
    zone = ctx.zone(page)
    now = ctx.now()
    earliest = now + int(cfg.book_lead_minutes) * MINUTE_MS
    most = int(cfg.posts_per_page_per_day)
    mine = {a["account"] for a in accounts_for(chosen_id(ctx, page), page)}
    today = datetime.fromtimestamp(now / 1000, zone).date()
    for offset in range(SLOT_DAYS):
        day = today + timedelta(days=offset)
        if most > 0 and posts_on_day(ctx, page, day.isoformat(), among=states.POST_OUT,
                                     skip_post=skip_post) >= most:
            continue
        for hour in page.get("hours") or []:
            h, m = (int(x) for x in hour.split(":"))
            local = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
            ms = int(local.timestamp() * 1000)
            if ms < earliest or ctx.day(ms, page) != day.isoformat():
                continue
            if mine & accounts_in_minute(ctx, ms, skip_post=skip_post, among=states.POST_OUT):
                continue
            return ms
    return None


# --- attempts -------------------------------------------------------------------------------


def _attempt(ctx: Ctx, post: dict[str, Any], action: str, sched: Any,
             sent: dict[str, Any]) -> int:
    """Recorded before anything is sent."""
    attempt_id = ctx.store.insert("attempt", {
        "post_id": post["id"], "action": action, "scheduler": sched.id, "started": ctx.now(),
        "outcome": "pending", "sent": sent})
    ctx.tape("booking.attempted", {"post": post["id"], "attempt": attempt_id,
                                   "action": action, "scheduler": sched.id},
             subject=f"post:{post['id']}")
    return attempt_id


def _finish(ctx: Ctx, post_id: int, attempt_id: int, outcome: str, detail: str) -> None:
    ctx.store.update("attempt", attempt_id, {"outcome": outcome, "finished": ctx.now(),
                                             "detail": detail[:500]})
    ctx.tape("booking.result", {"post": post_id, "attempt": attempt_id, "outcome": outcome},
             subject=f"post:{post_id}")


def _ask(fn: Any, *args: Any) -> Outcome:
    """One call to the scheduler. Anything but a definite answer is unsure."""
    try:
        outcome = fn(*args)
    except Exception as error:  # noqa: BLE001 - no answer is an answer: unsure
        return Outcome("unsure", detail=f"The scheduler gave no answer ({error}).")
    if not isinstance(outcome, Outcome) or outcome.kind not in ("sent", "refused", "unsure",
                                                                "partial", "cannot"):
        return Outcome("unsure", detail="The scheduler's answer could not be read.")
    return outcome


def _matched(outcome: Outcome, accounts: list[dict[str, str]], sends_out: bool
             ) -> tuple[list[dict[str, Any]], str | None]:
    """Upstream copies matched to accounts by the account each came back with, never by
    the order sent. A scheduler that sends out must return an id for every account."""
    wanted = {a["account"] for a in accounts}
    got = {u.get("account"): u.get("external_id") for u in outcome.upstream
           if u.get("account") in wanted}
    if sends_out and (set(got) != wanted or not all(got.values())):
        return [], "The scheduler said yes but did not say where each copy went."
    return [{"account": k, "external_id": v} for k, v in got.items()], None


# A post in one of these states is on the scheduler it was booked with, and stays with it.
HELD_BY = frozenset({"booking", "booked", "unsure", "missing", "failed", "went_out"})


def chosen_id(ctx: Ctx, page: dict[str, Any] | None) -> str:
    """Rule ``a_page_books_through_its_own_tool_when_it_has_one``: the tool the page chose
    for itself when Posts still offers it, otherwise the one in Posts' settings. A page
    that names a tool Posts no longer offers is booked through the setting, and its
    screen says so, rather than being left unable to book."""
    own = str((page or {}).get("book_through") or "")
    if own and own in ctx.schedulers:
        return own
    return str(ctx.cfg().scheduler)


def scheduler_id_of(ctx: Ctx, post: dict[str, Any], page: dict[str, Any] | None = None) -> str:
    """The tool for one post: the one it was booked with once it is on a scheduler (a
    change of the page's choice never moves a booked post), otherwise the page's."""
    if post.get("scheduler") and post.get("state") in HELD_BY:
        return str(post["scheduler"])
    return chosen_id(ctx, page if page is not None else ctx.store.page(post["page_id"]))


def scheduler_for(ctx: Ctx, name: str | None = None, *, page: dict[str, Any] | None = None,
                  post: dict[str, Any] | None = None) -> Any:
    """The scheduler to ask: ``name`` when given, else the one for ``post`` (see
    ``scheduler_id_of``), else the one for ``page``, else the one in Posts' settings."""
    if name:
        sched_id = name
    elif post is not None:
        sched_id = scheduler_id_of(ctx, post, page)
    else:
        sched_id = chosen_id(ctx, page)
    sched = ctx.schedulers.get(sched_id)
    if sched is None:
        raise BookingError(f"Posts cannot book through \"{sched_id}\". Choose a tool Posts "
                           "offers under Posts' settings, or on the brand. Nothing was sent.")
    return sched


def wanted_accounts(ctx: Ctx, post: dict[str, Any]) -> list[str]:
    """The accounts a post is for on its scheduler: those of its latest booking of the
    whole post (a "Book the rest" booking names only the rest)."""
    for attempt in reversed(ctx.store.attempts(post["id"])):
        if attempt["action"] == "book" and not attempt["sent"].get("rest"):
            return [str(a) for a in attempt["sent"].get("accounts") or []]
    return []


def lacking_accounts(ctx: Ctx, post: dict[str, Any]) -> list[str]:
    """Rule ``a_post_booked_on_some_accounts_lacks_the_rest``: the accounts of its latest
    whole booking that hold no copy Posts knows of, booked or unconfirmed, and that the
    owner did not take it off. Only a booked post on a scheduler that sends out lacks
    anything."""
    sched = ctx.schedulers.get(post.get("scheduler") or "")
    if post["state"] != "booked" or sched is None or not sched.sends_out:
        return []
    have = {u["account"] for u in ctx.store.upstream(post["id"])
            if u["state"] in (*LIVE_UPSTREAM, "cancelled")}
    return [a for a in wanted_accounts(ctx, post) if a not in have]


def labels_of(ctx: Ctx, post: dict[str, Any], accounts: list[str]) -> str:
    """Accounts as the owner knows them, from the post's page: "A", "A and B"."""
    page = ctx.store.page(post["page_id"]) or {}
    sched_id = str(post.get("scheduler") or chosen_id(ctx, page))
    known = {a["account"]: a["label"] for a in accounts_for(sched_id, page)}
    names = [known.get(a, a.partition(":")[2] or a) for a in accounts]
    return names[0] if len(names) == 1 else (
        f"{', '.join(names[:-1])} and {names[-1]}" if names else "")


def book(ctx: Ctx, post_id: int, *, slot_ms: int | None = None) -> dict[str, Any]:
    """Book one post through its page's tool (else the one in the settings). Returns the
    post. A booked post that lacks some of its accounts is booked for those only, at its
    own time ("Book the rest")."""
    store = ctx.store
    post = store.post(post_id)
    if post is None:
        raise BookingError("There is no such post. Nothing was sent.")
    rest = post["state"] == "booked"
    if rest:
        sched = scheduler_for(ctx, post=post)
        lacking = lacking_accounts(ctx, post)
        if slot_ms is not None and post["slot_ms"] is not None \
                and slot_ms // MINUTE_MS != post["slot_ms"] // MINUTE_MS:
            raise BookingError("The rest is booked at the post's own time, so its copies go "
                               "out together. Nothing was sent. Move the post first to book "
                               "it at another time.")
        slot = post["slot_ms"]
    else:
        sched = scheduler_for(ctx, page=store.page(post["page_id"]))
        lacking = None
        slot = slot_ms if slot_ms is not None else post["slot_ms"]
    refused = prechecks(ctx, post, sched, slot, lacking)
    if refused:
        raise BookingError(refused[0])
    page = store.page(post["page_id"]) or {}
    piece = store.piece(post["piece_id"]) or {}
    accounts = accounts_for(sched.id, page)
    if rest:
        accounts = [a for a in accounts if a["account"] in set(lacking or [])]
        if not accounts:
            raise BookingError("The accounts this post lacks are no longer on its brand. "
                               "Nothing was sent. Add them under Brands, or leave it as it "
                               "is.")
    else:
        held = {u["account"] for u in store.upstream(post_id, live=True)}
        accounts = [a for a in accounts if a["account"] not in held]  # never one that has it
    text = booked_text(post, piece, str(ctx.cfg().sponsor_tag))
    pictures = pictures_of(ctx, post)
    attempt_id = _attempt(ctx, post, "book", sched, {
        "slot_ms": slot, "accounts": [a["account"] for a in accounts], "text": text,
        **({"rest": True} if rest else {})})
    store.move_post(post_id, "booking", scheduler=sched.id, slot_ms=slot)
    outcome = _ask(sched.book, Booking(post_id, text, slot, accounts, pictures))
    upstream, problem = ([], None)
    if outcome.kind == "sent":
        upstream, problem = _matched(outcome, accounts, sched.sends_out)
        if problem:
            outcome = Outcome("unsure", detail=problem)
    if outcome.kind == "sent":
        _keep(ctx, post_id, upstream, "booked", slot)
        _finish(ctx, post_id, attempt_id, "sent", outcome.detail)
        return store.move_post(post_id, "booked", out_of_step=0,
                               note="" if rest else outcome.detail)
    if outcome.kind == "refused":
        _finish(ctx, post_id, attempt_id, "refused", outcome.detail)
        if rest:
            return store.move_post(post_id, "booked", note=(
                f"{sched.label} said no to the rest ({labels_of(ctx, post, lacking or [])}): "
                f"{outcome.detail} It is still booked on the accounts that had it."))
        tail = "" if outcome.detail.rstrip().lower().endswith("nothing was booked.") \
            else " Nothing was booked."
        return store.move_post(post_id, "queued", note=f"{sched.label} said no: "
                               f"{outcome.detail}{tail}")
    if outcome.kind == "partial":
        return _partial(ctx, sched, post, attempt_id, outcome, accounts, slot)
    _keep(ctx, post_id, _named(outcome.upstream, accounts), "unconfirmed", slot)
    _finish(ctx, post_id, attempt_id, "unsure", outcome.detail)
    return store.move_post(post_id, "unsure", note=unsure_note(ctx, post_id, sched.label,
                                                               outcome.detail))


def _named(copies: Any, accounts: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The copies that came back for an account that was asked, with an id."""
    wanted = {a["account"] for a in accounts}
    return [{"account": c["account"], "external_id": str(c["external_id"])}
            for c in copies or [] if c.get("account") in wanted and c.get("external_id")]


def _keep(ctx: Ctx, post_id: int, copies: list[dict[str, Any]], state: str, slot: int) -> None:
    """Copies written down: ``booked`` for a definite yes; ``unconfirmed`` for copies an
    unclear answer named (never called booked, but known, so reconcile can find them and
    no account that holds one is booked again)."""
    for copy in copies:
        ctx.store.insert("upstream", {"post_id": post_id, "account": copy["account"],
                                      "external_id": str(copy["external_id"]),
                                      "state": state, "seen": ctx.now(), "slot_ms": slot})


def _partial(ctx: Ctx, sched: Any, post: dict[str, Any], attempt_id: int, outcome: Outcome,
             accounts: list[dict[str, str]], slot: int) -> dict[str, Any]:
    """A booking that reached some accounts for certain. With every other account a
    definite no, the post is booked on those and lacks the rest; with any answer unclear,
    or a copy named but not as asked, it is unsure."""
    post_id = post["id"]
    confirmed = _named(outcome.upstream, accounts)
    named = _named(outcome.named, accounts)
    _keep(ctx, post_id, confirmed, "booked", slot)
    _keep(ctx, post_id, named, "unconfirmed", slot)
    got = {c["account"] for c in confirmed} | {c["account"] for c in named}
    unclear = [a["account"] for a in accounts if a["account"] not in got] \
        if outcome.unclear is None else list(outcome.unclear)
    if unclear or named:
        _finish(ctx, post_id, attempt_id, "unsure", outcome.detail)
        return ctx.store.move_post(post_id, "unsure", note=unsure_note(
            ctx, post_id, sched.label, outcome.detail))
    _finish(ctx, post_id, attempt_id, "sent", outcome.detail)
    lack = labels_of(ctx, post, lacking_accounts(ctx, {**post, "state": "booked",
                                                       "scheduler": sched.id}))
    return ctx.store.move_post(post_id, "booked", out_of_step=0, note=(
        f"Booked on only some of its accounts. {outcome.detail} Press \"Book the rest\" to "
        f"book it for {lack}; the accounts that have it are left alone."))


def unsure_note(ctx: Ctx, post_id: int, label: str, detail: str = "") -> str:
    """What an unsure post says, by what Posts knows of its copies: none (look, and say
    if it is on none of its accounts); some booked for certain (look on the rest only);
    any named by an unclear answer (Posts looks for it; nothing is put back to book)."""
    post = ctx.store.post(post_id) or {}
    live = ctx.store.upstream(post_id, live=True)
    detail = f"{detail.strip()} " if detail.strip() else ""
    asked = next((a["sent"].get("accounts") or [] for a in reversed(
        ctx.store.attempts(post_id)) if a["action"] == "book"), [])
    held = [u["account"] for u in live if u["state"] == "booked"]
    named = [u["account"] for u in live if u["state"] == "unconfirmed"]
    if named:
        return (f"{label} gave an answer that was not clear, so this post may be booked on "
                f"{labels_of(ctx, post, named)} in a way Posts cannot confirm. {detail}"
                f"Posts looks for it on {label} at its next check, and books nothing again "
                "on an account that may have it.")
    if held:
        rest = [a for a in asked if a not in held]
        return (f"{label} booked it for {labels_of(ctx, post, held)}, but gave no clear "
                f"answer for {labels_of(ctx, post, rest) or 'the rest'}, so it may be there "
                f"too. {detail}Posts looks for it on {label} at its next check. Or look on "
                f"{labels_of(ctx, post, rest) or 'the rest'} yourself: if it is not there, "
                "press \"I checked\" and then \"Book the rest\". The accounts that have it "
                "are never booked again.")
    return (f"{label} gave no clear answer, so this post may or may not be booked. {detail}"
            f"Posts looks for it on {label} at its next check. Or look yourself: if it is on "
            "none of its accounts, press \"I checked: it is on none of them\" to book it "
            "again.")


def mark_unsure(ctx: Ctx) -> int:
    """An attempt still waiting after ``unsure_after_minutes`` (the program stopped
    mid-call) is unsure. Returns how many."""
    since = ctx.now() - int(ctx.cfg().unsure_after_minutes) * MINUTE_MS
    count = 0
    for attempt in ctx.store.all("SELECT * FROM attempt WHERE outcome='pending' AND "
                                 "started < ?", (since,)):
        _finish(ctx, attempt["post_id"], attempt["id"], "unsure", "No answer came back.")
        post = ctx.store.post(attempt["post_id"])
        if post and post["state"] == "booking":
            sched = ctx.schedulers.get(post["scheduler"] or "")
            ctx.store.move_post(post["id"], "unsure", note=unsure_note(
                ctx, post["id"], sched.label if sched else "the scheduler",
                "The booking got no answer."))
        count += 1
    return count


def clear_unsure(ctx: Ctx, post_id: int) -> dict[str, Any]:
    """The owner looked and it is not there. With no copy known anywhere, the post goes
    back in the queue to be booked again. With copies booked for certain on some accounts,
    the owner's word is about the rest only: the post is booked on those, and "Book the
    rest" books the others. With a copy an unclear answer named, it is refused: that
    copy may be there, and only the scheduler's own list can settle it."""
    store = ctx.store
    open_ = [a for a in store.attempts(post_id) if a["outcome"] == "unsure"]
    if not open_:
        raise BookingError("This post has no unsure booking to clear. Nothing was changed.")
    post = store.post(post_id) or {}
    live = store.upstream(post_id, live=True)
    named = [u["account"] for u in live if u["state"] == "unconfirmed"]
    if named:
        raise BookingError(
            f"The scheduler named a copy of this post for {labels_of(ctx, post, named)}, so "
            "it may be booked there. Nothing was changed. Posts looks for it at its next "
            "check; if you remove it there, Posts sees that it is gone.")
    for attempt in open_:
        store.update("attempt", attempt["id"], {"outcome": "cleared", "finished": ctx.now()})
        ctx.tape("booking.result", {"post": post_id, "attempt": attempt["id"],
                                    "outcome": "cleared"}, subject=f"post:{post_id}")
    post = store.post(post_id) or {}
    if post.get("state") != "unsure":
        return post
    if live:
        lack = labels_of(ctx, post, lacking_accounts(ctx, {**post, "state": "booked"}))
        return store.move_post(post_id, "booked", note=(
            f"You checked: it is not on {lack}. It is booked on the accounts that have it. "
            f"Press \"Book the rest\" to book it for {lack}."))
    return store.move_post(post_id, "queued", note="You checked: it is on none of its "
                           "accounts, so it was not booked.")


# --- changes to a queued or booked post -----------------------------------------------------


def move(ctx: Ctx, post_id: int, slot_ms: int) -> dict[str, Any]:
    store = ctx.store
    post = store.post(post_id)
    if post is None:
        raise BookingError("There is no such post. Nothing was changed.")
    if post["state"] not in ("queued", "booked"):
        raise BookingError(f"This post is {states.WORDS[post['state']]}, so it cannot be "
                           "moved. Nothing was changed.")
    page = store.page(post["page_id"]) or {}
    cfg = ctx.cfg()
    sched = scheduler_for(ctx, post=post)
    refused = [slot_is_ahead(slot_ms, ctx.now(), int(cfg.book_lead_minutes))]
    if refused[0] is None:
        refused += [under_daily_limit(ctx, page, slot_ms, int(cfg.posts_per_page_per_day),
                                      post_id),
                    minute_is_free(ctx, sched.id, page, slot_ms, post_id)]
    if post["state"] == "booked":
        refused.append(no_open_attempt(ctx, post))
    refused = [r.replace("Nothing was sent.", "Nothing was changed.") for r in refused if r]
    if refused:
        raise BookingError(refused[0])
    if post["state"] == "queued":
        return store.move_post(post_id, "queued", slot_ms=slot_ms)
    attempt_id = _attempt(ctx, post, "move", sched, {"slot_ms": slot_ms})
    copies = store.upstream(post_id, live=True)
    outcome = _ask(sched.move, post_id, copies, slot_ms)
    if outcome.kind == "sent":
        _finish(ctx, post_id, attempt_id, "sent", outcome.detail)
        _copies_at(ctx, post_id, [c["account"] for c in copies], slot_ms)
        return store.move_post(post_id, "booked", slot_ms=slot_ms, note="")
    if outcome.kind == "partial":
        reached = [c["account"] for c in outcome.upstream]
        _finish(ctx, post_id, attempt_id, "refused", outcome.detail)
        _copies_at(ctx, post_id, reached, slot_ms)
        return store.move_post(post_id, "booked", slot_ms=slot_ms, out_of_step=1, note=(
            f"The move reached {sched.label} for only some accounts, so this post is out of "
            f"step. {outcome.detail} Move it again, or put it right on {sched.label}."))
    if outcome.kind in ("refused", "cannot"):
        _finish(ctx, post_id, attempt_id, "refused", outcome.detail)
        return store.move_post(post_id, "booked", note=(
            f"{sched.label} did not take the move: {outcome.detail} It is still booked for "
            "its old time, here and there. Choose another time, or move it there."))
    _finish(ctx, post_id, attempt_id, "unsure", outcome.detail)
    return store.move_post(post_id, "booked", out_of_step=1, note=(
        f"The move got no clear answer from {sched.label}, so this post may be out of step. "
        f"{outcome.detail} Look on {sched.label} to see which time it holds."))


def _copies_at(ctx: Ctx, post_id: int, accounts: list[str], slot_ms: int) -> None:
    for account in accounts:
        ctx.store.conn.execute("UPDATE upstream SET slot_ms=? WHERE post_id=? AND account=? "
                               "AND state IN (?, ?)", (slot_ms, post_id, account,
                                                       *LIVE_UPSTREAM))
    ctx.store.conn.commit()


def push_text(ctx: Ctx, post_id: int) -> dict[str, Any]:
    """After an edit of a booked post: send the new text through ``edit_text``, or say
    plainly that it did not reach the scheduler."""
    store = ctx.store
    post = store.post(post_id) or {}
    sched = scheduler_for(ctx, post=post)
    did_not = (f"This change did not reach {sched.label}. Book again with this text, or "
               "change it there.")
    if not sched.can_edit:
        return store.move_post(post_id, "booked", out_of_step=1, note=did_not)
    if no_open_attempt(ctx, post):
        return store.move_post(post_id, "booked", out_of_step=1, note=did_not)
    piece = store.piece(post["piece_id"]) or {}
    text = booked_text(post, piece, str(ctx.cfg().sponsor_tag))
    attempt_id = _attempt(ctx, post, "edit", sched, {"chars": len(text)})
    outcome = _ask(sched.edit_text, post_id, store.upstream(post_id, live=True), text)
    if outcome.kind == "sent":
        _finish(ctx, post_id, attempt_id, "sent", outcome.detail)
        return store.move_post(post_id, "booked", out_of_step=0, note="")
    _finish(ctx, post_id, attempt_id, "unsure" if outcome.kind == "unsure" else "refused",
            outcome.detail)
    return store.move_post(post_id, "booked", out_of_step=1,
                           note=f"{did_not} {outcome.detail}".strip())


def cancel(ctx: Ctx, post_id: int) -> dict[str, Any]:
    """A queued post is declined; a booked one is taken off the scheduler first, every
    copy, and stays booked if any copy could not be taken off."""
    store = ctx.store
    post = store.post(post_id)
    if post is None:
        raise BookingError("There is no such post. Nothing was changed.")
    if post["state"] == "queued":
        return store.move_post(post_id, "declined", note="Cancelled before it was booked.")
    if post["state"] in ("failed", "missing"):
        return store.move_post(post_id, "declined", note=(
            "Declined. Nothing was sent to the scheduler; anything still there for it is "
            "yours to remove there."))
    if post["state"] != "booked":
        raise BookingError(f"This post is {states.WORDS[post['state']]}, so it cannot be "
                           "cancelled now. Nothing was changed.")
    sched = scheduler_for(ctx, post=post)
    blocked = no_open_attempt(ctx, post)
    if blocked:
        raise BookingError(blocked.replace("Nothing was sent.", "Nothing was changed."))
    attempt_id = _attempt(ctx, post, "cancel", sched, {})
    outcome = _ask(sched.cancel, post_id, store.upstream(post_id, live=True))
    if outcome.kind == "sent":
        _finish(ctx, post_id, attempt_id, "sent", outcome.detail)
        store.conn.execute("UPDATE upstream SET state='cancelled' WHERE post_id=?", (post_id,))
        store.conn.commit()
        return store.move_post(post_id, "declined", note=f"Taken off {sched.label}.")
    _finish(ctx, post_id, attempt_id, "unsure" if outcome.kind == "unsure" else "refused",
            outcome.detail)
    if outcome.kind == "partial":
        for copy in outcome.upstream:
            store.conn.execute("UPDATE upstream SET state='cancelled' WHERE post_id=? AND "
                               "account=?", (post_id, copy["account"]))
        store.conn.commit()
        store.move_post(post_id, "booked", out_of_step=1, note=(
            f"Taken off {sched.label} for only some accounts: {outcome.detail}"))
        raise BookingError(f"{sched.label} took it off only some accounts: {outcome.detail} "
                           f"It is still booked on the others. Try again, or remove it on "
                           f"{sched.label}.")
    raise BookingError(f"{sched.label} did not take it off: {outcome.detail} It is still "
                       f"booked. Try again, or remove it on {sched.label}.")


def book_again(ctx: Ctx, post_id: int) -> dict[str, Any]:
    """A post the scheduler could not publish (``failed``) or that vanished from it
    (``missing``) goes back in the queue, with a new proposed time, to be booked again
    by the owner's click. Nothing is sent here."""
    store = ctx.store
    post = store.post(post_id)
    if post is None or post["state"] not in ("failed", "missing"):
        raise BookingError("Only a post that failed or went missing on the scheduler can be "
                           "put back in the queue. Nothing was changed.")
    store.conn.execute("UPDATE upstream SET state='gone' WHERE post_id=? AND state IN "
                       "('booked', 'unconfirmed')", (post_id,))
    store.conn.commit()
    page = store.page(post["page_id"]) or {}
    slot = propose_slot(ctx, page, skip_post=post_id)
    return store.move_post(post_id, "queued", slot_ms=slot, out_of_step=0, note=(
        "Back in the queue. Nothing was sent; book it when you are ready."))


def posted_by_hand(ctx: Ctx, post_id: int) -> dict[str, Any]:
    """The owner posted it themselves."""
    store = ctx.store
    post = store.post(post_id)
    if post is None or post["state"] not in ("queued", "booked"):
        raise BookingError("Only a queued post, or one on your By hand list, can be marked "
                           "posted. Nothing was changed.")
    if post["state"] == "booked" and post["scheduler"] not in ("by_hand", ""):
        raise BookingError("This post is booked on a scheduler, which says when it goes "
                           "out. Nothing was changed.")
    done = store.move_post(post_id, "went_out", slot_ms=post["slot_ms"] or ctx.now())
    ctx.tape("post.went_out", {"post": post_id}, subject=f"post:{post_id}")
    ctx.tape("posted", kinds.posted(done, store.piece(done["piece_id"])),
             subject=f"post:{post_id}")
    return done
