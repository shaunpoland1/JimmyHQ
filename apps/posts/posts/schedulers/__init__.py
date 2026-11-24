"""The scheduler interface: what ``booking`` and ``reconcile`` call, and the schedulers.

A scheduler is handed a ``Booking`` and answers an ``Outcome``. It never decides
whether to book: ``booking.book`` does that, records the attempt first, and reads the
outcome. A scheduler that sends nothing out (``sends_out`` False) may be booked as soon as
the owner approves; any other waits for the owner's ladder.

    sched.needs(page) -> str | None              what the page lacks for it, as a sentence
    sched.book(booking) -> Outcome               sent (with upstream ids), refused or unsure
    sched.status(post_id, upstream) -> Seen      what each copy is now, or "could not tell"
    sched.move(post_id, upstream, slot_ms) -> Outcome
    sched.edit_text(post_id, upstream, text) -> Outcome   "cannot" when it takes no edits
    sched.cancel(post_id, upstream) -> Outcome
    sched.scheduled(accounts=None, around_ms=None) -> list[dict]
                                                 what it holds, for reconcile; a scheduler
                                                 may take the two to ask for less
    sched.accounts() -> list[dict]               optional: {account_id, label, network} for each
                                                 account the owner has there
    sched.recent(account_ids, limit) -> list     optional: {text, when_ms, account_id} for the
                                                 newest posts that went out on those accounts

An outcome is ``sent`` only on a definite yes, ``refused`` only on a definite no; anything
else (no answer, a timeout, a yes without ids) is ``unsure``. ``partial`` is a definite
answer that reached some copies and not others: ``upstream`` then holds the ones it
reached, and the post is out of step. To add a scheduler, give it these and list it in
``made``.

``accounts`` and ``recent`` are optional: a scheduler that sends nothing out has neither, and
``lists_accounts`` and ``reads_posts`` say which has what. Both only read, never change
anything, and raise ``Unavailable`` with a sentence the owner can read when they cannot
tell (no key, no answer, a refusal): a list read in part is never given as the whole.

A scheduler that sends out answers ``scheduled()`` with ``{account, external_id, slot_ms}``
for each post it holds, and ``text`` too when its list gives it (then a post is found
again only by its own text), and raises ``Unavailable`` when it cannot tell. One that
takes ``accounts`` (Posts' account keys) and ``around_ms`` (a list of times) may ask only
for those accounts and the minutes around those times; SocialBu reads its whole list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Booking:
    post_id: int
    text: str
    slot_ms: int
    accounts: list[dict[str, str]]  # {account, account_id, label}; account is the key
    pictures: tuple[dict[str, str], ...] = ()  # {ref, name}, in the order they are shown


@dataclass(frozen=True)
class Outcome:
    """What one call did. For a ``partial`` booking, ``upstream`` holds the copies made for
    certain; ``named`` the copies an unclear answer named (they exist, but not as asked:
    kept unconfirmed); ``unclear`` the accounts whose answer was not clear, so they may
    hold a copy Posts has no id for. ``unclear`` None means the scheduler did not say:
    every account not in ``upstream`` is then taken as unclear."""

    kind: str  # "sent", "refused", "unsure", "partial" or "cannot"
    upstream: list[dict[str, Any]] = field(default_factory=list)  # {account, external_id}
    detail: str = ""
    named: tuple[dict[str, Any], ...] = ()
    unclear: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Seen:
    """What a scheduler says about one booked post now. ``known`` False means it could not
    tell (no answer, a server error): reconcile then changes nothing.

    Each copy is ``{account, external_id, state, slot_ms, said, figures}``: ``state`` is
    ``scheduled``, ``published``, ``failed`` or ``gone``; ``said`` is the service's own
    words about a failure; ``figures`` is ``{metric: number}`` or None when it gives none.
    """

    known: bool
    copies: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""


class Unavailable(RuntimeError):
    """The scheduler could not be asked, or did not answer clearly. Nothing was changed."""


class Scheduler(Protocol):
    id: str
    label: str
    sends_out: bool
    can_edit: bool

    def needs(self, page: dict[str, Any]) -> str | None: ...
    def book(self, booking: Booking) -> Outcome: ...
    def status(self, post_id: int, upstream: list[dict[str, Any]]) -> Seen: ...
    def move(self, post_id: int, upstream: list[dict[str, Any]], slot_ms: int) -> Outcome: ...
    def edit_text(self, post_id: int, upstream: list[dict[str, Any]], text: str) -> Outcome: ...
    def cancel(self, post_id: int, upstream: list[dict[str, Any]]) -> Outcome: ...
    def scheduled(self) -> list[dict[str, Any]]: ...  # may take accounts=, around_ms=


def lists_accounts(scheduler: Any) -> bool:
    """Whether this scheduler can list the owner's accounts there (``accounts()``)."""
    return callable(getattr(scheduler, "accounts", None))


def reads_posts(scheduler: Any) -> bool:
    """Whether this scheduler can read posts that went out (``recent()``)."""
    return callable(getattr(scheduler, "recent", None))


def accounts_for(scheduler_id: str, page: dict[str, Any]) -> list[dict[str, str]]:
    """The page's accounts on this scheduler. A scheduler that sends nothing out books the
    page itself when the page lists no account for it."""
    mine = [a for a in page.get("accounts") or [] if a.get("scheduler") == scheduler_id]
    if mine:
        return [{"account": f"{scheduler_id}:{a['account_id']}", "account_id": a["account_id"],
                 "label": a.get("label") or a["account_id"]} for a in mine]
    return [{"account": f"page:{page['id']}", "account_id": "", "label": page["name"]}]


def made(ctx: Any) -> dict[str, Scheduler]:
    from posts.schedulers.buffer import Buffer  # noqa: PLC0415
    from posts.schedulers.by_hand import ByHand  # noqa: PLC0415
    from posts.schedulers.practice import Practice  # noqa: PLC0415
    from posts.schedulers.socialbu import SocialBu  # noqa: PLC0415

    return {"practice": Practice(ctx), "by_hand": ByHand(ctx), "socialbu": SocialBu(ctx),
            "buffer": Buffer(ctx)}
