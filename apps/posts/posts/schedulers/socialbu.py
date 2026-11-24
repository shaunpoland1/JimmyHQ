"""SocialBu: books each post on the SocialBu accounts the owner listed on its page.

Every request goes through JimmyHQ's ``net.fetch`` to exactly ``socialbu.com``, which the
manifest asks for, with the owner's key from the app's Settings page. With no key the
scheduler says so and nothing is sent. Nothing of any one owner's is built in: the
accounts are the ids the owner typed on each page.

Each answer is classified once, by ``classify``: a definite yes, a definite no (with the
service's own words), or no answer (a timeout, a server error, an answer that cannot be
read). The booking path turns no answer into ``unsure``; nothing here ever tries again.

What the service does that shapes this file (from its public reference, and from an older
client's experience):

- ``POST /posts`` books one post for many accounts and answers ``{"posts": [...]}``, one
  per account, in any order: copies are matched to accounts by the ``account_id`` each
  comes back with, never by the order sent.
- Two posts in one minute on one account are refused (HTTP 400 with a ``message``). A past
  ``publish_at`` is not refused: it publishes at once. Both are refused here first.
- Pictures are uploaded first with ``POST /upload_media_by_url``, the file sent inside the
  request as a data URL, so the only host ever asked is SocialBu's own.
- ``PATCH /posts/{id}`` changes a scheduled post; ``existing_attachments`` is the final
  set, so an edit sends back the saved ones exactly as ``GET /posts/{id}`` gave them.
- ``DELETE`` of a post that is already gone (404 or 410) counts as done.

Reading, for the page's "Add an account" and "Fill in from its posts" (the reference is the
OpenAPI file behind socialbu.com/developers/docs, socialbu.com/openapi.yaml, read for this):

- ``accounts()`` asks ``GET /posts/supported-options`` with no ``account_ids``, which "returns
  the raw publish handler option definitions for ... all available accounts" as
  ``{"accounts": [{account_id, account_type, account_name, options}]}`` and answers 404
  ``{"message": "No matching accounts found."}`` when there are none. It is used because its
  account fields are the only ones the reference names: ``GET /accounts`` is documented
  (``items``, ``nextPage``) but not what an item holds.
- ``recent()`` asks ``GET /posts?type=published&perPage&page&accounts[]``. Confirmed by the
  OpenAPI file: the path, ``type`` taking ``published``, ``perPage`` at most 100, ``page``,
  and that an ``accounts`` filter of account ids exists. Assumed, not settled by it: that
  the filter is written ``accounts[]=`` (one for each id); and every name inside a listed
  post, which the reference leaves open (``additionalProperties: true``). So the text is
  read from the first present of ``content`` (the name the create and update calls and
  ``POST /posts/{id}/publish`` take), ``text``, ``message`` and ``caption``, and the time
  from the first readable of ``publish_at``, ``published_at``, ``publishedAt``,
  ``created_at`` and ``createdAt``; each post is kept only for an account asked for, by
  its ``account_id`` (assumed too). If answers arrive but none holds readable text, that
  is said, not passed over.

Reading what is scheduled, for the Scheduled view (``listing()``): ``GET /posts?type=
scheduled&page=n``, the same call the older client uses for its calendar, answering
``{"items": [...], "nextPage": n}``, about 20 a page. ``type=scheduled`` is proven by that
older client; the reference's own text for it was not re-read here. Inside an item only
``id``, ``account_id`` and ``publish_at`` are names the rest of this file already depends on;
the text is read from the same ``TEXT_NAMES`` as ``recent()``, and the pictures are counted
from ``attachments`` (the name a single post's answer uses), neither confirmed for a listed
post. A picture's address is never read: the view shows a count only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jimmyhq_app import CallError

from posts.schedulers import Booking, Outcome, Seen, Unavailable

log = logging.getLogger("posts.socialbu")

HOST = "socialbu.com"
BASE = f"https://{HOST}/api/v1"
SECRET = "socialbu_key"  # noqa: S105 - the name of the secret, not its value
LABEL = "SocialBu"
MINUTE_MS = 60_000
MB = 1024 * 1024
LIST_PER_PAGE = 100  # the most the reference allows
LIST_MOST_PAGES = 50  # past this the list is not trusted: reconcile skips it
RECENT_MOST_PAGES = 5  # published posts are read this many pages deep, at most
POST_TEXT_MOST = 2000  # characters of one published post kept
LISTED_TEXT_MOST = 3000  # characters of one scheduled post kept for the Scheduled view
SERVICE_TIME = "%Y-%m-%d %H:%M:%S"  # UTC, as the reference gives it

# The picture types the service takes, by the ending JimmyHQ gives an upload.
TAKES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
         "webp": "image/webp"}

# Platform refusals that mean nothing left this computer.
NOT_SENT = frozenset({"not_allowed", "invalid", "not_found", "quota"})
GONE = frozenset({404, 410})

NO_KEY_READ = ("Posts has no SocialBu key. Type it in on Posts' Settings page under Apps, "
               "then try again.")
NO_KEY = ("Posts has no SocialBu key, so nothing was sent. Type it in on Posts' Settings "
          "page under Apps, then try again.")


# --- the rules, one function each -----------------------------------------------------------


def scrub(text: str, key: str | None) -> str:
    """Text that may be shown or kept, with the key taken out wherever it appears."""
    text = str(text or "")
    if key:
        text = text.replace(key, "[your key]")
    return text


def to_service_time(ms: int) -> str:
    """Unix milliseconds as the service writes a time: ``Y-m-d H:i:s`` in UTC."""
    return datetime.fromtimestamp(ms / 1000, UTC).strftime(SERVICE_TIME)


def from_service_time(text: Any) -> int | None:
    """The service's time as unix milliseconds, or None when it cannot be read. A time
    with no zone is UTC, as the reference says."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip().replace("T", " ").replace("Z", "+00:00")
    for shape in (SERVICE_TIME, "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f%z",
                  "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            when = datetime.strptime(raw, shape)  # noqa: DTZ007 - a zone is added below
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return int(when.timestamp() * 1000)
    return None


def same_minute(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and a // MINUTE_MS == b // MINUTE_MS


def account_number(account_id: Any) -> int | None:
    """The service's account id as the number it is, or None when it is not one."""
    text = str(account_id or "").strip()
    return int(text) if text.isdigit() else None


def service_message(data: Any, text: str = "") -> str:
    """What the service said, in its own words: ``message``, else the first of its
    ``errors``, else the start of what came back."""
    if isinstance(data, dict):
        if isinstance(data.get("message"), str) and data["message"].strip():
            return data["message"].strip()[:300]
        errors = data.get("errors")
        if isinstance(errors, dict):
            for value in errors.values():
                first = value[0] if isinstance(value, list) and value else value
                if isinstance(first, str) and first.strip():
                    return first.strip()[:300]
        if isinstance(data.get("error"), str) and data["error"].strip():
            return data["error"].strip()[:300]
    return str(text or "").strip()[:200]


def collision_sentence(said: str) -> str | None:
    """The service's refusal of a second post in one minute, as a sentence the owner can
    act on; None when it said something else."""
    if "already scheduled at this time" not in said.lower():
        return None
    return ("SocialBu already has a post in that minute on one of this brand's accounts, "
            "perhaps one made there directly. Nothing was booked. Choose another minute, "
            "then book again.")


def still_ahead(slot_ms: int, now_ms: int) -> bool:
    """SocialBu publishes a past time at once rather than refusing it, so a time that has
    passed (while pictures uploaded, say) is never sent."""
    return slot_ms > now_ms


@dataclass(frozen=True)
class Answer:
    """One request's result. ``kind`` is ``yes``, ``no`` (a definite refusal; ``said`` has
    the service's words) or ``none`` (no answer that can be trusted either way)."""

    kind: str
    status: int | None = None
    data: Any = None
    said: str = ""


def classify(reply: dict[str, Any] | None, error: Exception | None, key: str | None,
             *, empty_ok: bool = False) -> Answer:
    """One answer, sorted. A refusal by JimmyHQ before sending is a no; a timeout, a
    broken connection, a server error, a redirect or a body that cannot be read is no
    answer; a 2xx that says ``success: false`` is a no; any other 4xx is a no."""
    if error is not None:
        message = scrub(getattr(error, "message", "") or str(error), key)
        if isinstance(error, CallError) and error.code in NOT_SENT:
            return Answer("no", said=message)
        return Answer("none", said=message or "No answer came back.")
    if not isinstance(reply, dict) or not isinstance(reply.get("status"), int):
        return Answer("none", said="The answer could not be read.")
    status, text = reply["status"], str(reply.get("text") or "")
    data: Any = None
    if text.strip():
        try:
            import json  # noqa: PLC0415

            data = json.loads(text)
        except ValueError:
            data = None
    said = scrub(service_message(data, text if data is None else ""), key)
    if 200 <= status < 300:
        if data is None and not (empty_ok and not text.strip()):
            return Answer("none", status, None, "SocialBu said yes but its answer could not "
                                                "be read.")
        if isinstance(data, dict) and data.get("success") is False:
            return Answer("no", status, data, said or "SocialBu said it did not succeed.")
        return Answer("yes", status, data, said)
    if 400 <= status < 500 and status != 408:
        return Answer("no", status, data, said or f"SocialBu refused it (HTTP {status}).")
    return Answer("none", status, data, said or f"SocialBu gave no clear answer (HTTP "
                                                f"{status}).")


def match_copies(posts: Any, accounts: list[dict[str, str]]
                 ) -> tuple[list[dict[str, str]], list[str]]:
    """The copies that came back, matched to the accounts asked by the ``account_id`` each
    carries, never by order. Returns ``(copies, labels of accounts with none)``. A copy for
    an account not asked, or with no id, is left out."""
    by_number = {str(account_number(a["account_id"])): a for a in accounts}
    found: dict[str, str] = {}
    for item in posts if isinstance(posts, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        account = by_number.get(str(item.get("account_id")))
        if account is not None and account["account"] not in found:
            found[account["account"]] = str(item["id"])
    copies = [{"account": k, "external_id": v} for k, v in found.items()]
    missing = [a["label"] for a in accounts if a["account"] not in found]
    return copies, missing


def copy_state(post: dict[str, Any]) -> tuple[str, str]:
    """One upstream post as ``(state, said)``: ``published``, ``failed`` (with the
    service's words), or ``scheduled``."""
    if post.get("published") or post.get("_published"):
        return "published", ""
    error = post.get("error")
    if isinstance(error, str) and error.strip():
        return "failed", error.strip()[:300]
    return "scheduled", ""


def figures_of(post: dict[str, Any]) -> dict[str, float] | None:
    """The engagement figures on one upstream post, ``{metric: number}``, from its
    ``insights`` list of ``{type, value}``; None when it carries none."""
    rows = post.get("insights")
    if not isinstance(rows, list):
        return None
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("type"), str):
            continue
        value = row.get("value")
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        out[row["type"][:40]] = out.get(row["type"][:40], 0) + float(value)
    return out


def kept_attachments(post: dict[str, Any]) -> list[Any] | None:
    """The attachments to send back with an edit, exactly as the service gave them, or
    None when the post shows none (the field is then left out, which keeps what is
    there)."""
    found = post.get("attachments")
    return list(found) if isinstance(found, list) else None


def picture_problem(picture: dict[str, str], size: int | None, most_mb: int) -> str | None:
    """Why a picture cannot be sent, as a sentence, or None."""
    name = picture["name"]
    if size is None:
        return (f"The picture \"{name}\" is no longer in Posts' storage. Nothing was booked. "
                "Add it again, or book without it.")
    ending = picture["ref"].rsplit(".", 1)[-1].lower()
    if ending not in TAKES:
        return (f"SocialBu does not take pictures like \"{name}\" ({ending}). Nothing was "
                "booked. Use a JPEG, PNG, GIF or WebP picture instead.")
    if size > most_mb * MB:
        return (f"The picture \"{name}\" is {size / MB:.1f} MB, more than the {most_mb} MB "
                "Posts uploads. Nothing was booked. Use a smaller picture, or raise "
                "\"Largest picture to upload\".")
    return None


def summed(results: list[tuple[dict[str, Any], Answer]], what: str) -> Outcome:
    """One change made on each copy, summed up: all yes is sent; none reached and every
    no definite is refused; none reached with any no answer is unsure; some reached is
    partial, naming who has it and who has not."""
    reached = [copy for copy, answer in results if answer.kind == "yes"]
    failed = [(copy, answer) for copy, answer in results if answer.kind != "yes"]
    if not failed:
        return Outcome("sent", [{"account": c["account"], "external_id": c["external_id"]}
                                for c in reached])
    words = "; ".join(f"{_who(c)}: {a.said or 'no answer'}" for c, a in failed)
    if reached:
        return Outcome("partial", [{"account": c["account"], "external_id": c["external_id"]}
                                   for c in reached],
                       f"{what} reached {', '.join(_who(c) for c in reached)} but not "
                       f"{', '.join(_who(c) for c, _ in failed)} ({words}).")
    if any(a.kind == "none" for _, a in failed):
        return Outcome("unsure", detail=f"SocialBu gave no clear answer ({words}).")
    collision = next((collision_sentence(a.said) for _, a in failed
                      if collision_sentence(a.said)), None)
    return Outcome("refused", detail=collision or f"SocialBu said no ({words}).")


def accounts_listed(found: Any) -> list[dict[str, str]]:
    """``{account_id, label, network}`` for each account in the reference's
    ``accounts`` list, without repeats; an entry with no id is left out."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or isinstance(item.get("account_id"), bool):
            continue
        number = account_number(item.get("account_id"))
        if number is None or str(number) in seen:
            continue
        seen.add(str(number))
        name = " ".join(str(item.get("account_name") or "").split())[:80]
        out.append({"account_id": str(number), "label": name or f"Account {number}",
                    "network": " ".join(str(item.get("account_type") or "").split())[:40]})
    return out


# A listed post's text and time, by the names tried in order. None of these is confirmed for
# a listed post (the reference leaves an item open): ``content`` and ``publish_at`` are the
# names the create and update calls take; the others are the plain names a list may use.
TEXT_NAMES = ("content", "text", "message", "caption")
TIME_NAMES = ("publish_at", "published_at", "publishedAt", "created_at", "createdAt")


def first_text(item: dict[str, Any], names: tuple[str, ...]) -> str:
    """The first of ``names`` that holds text, stripped, or ''."""
    for name in names:
        value = item.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def posts_read(items: Any, wanted: set[str]) -> tuple[list[dict[str, Any]], int]:
    """The published posts in one page of the list, as ``{text, when_ms, account_id}``,
    for the accounts asked about only. The text is the first of ``TEXT_NAMES`` present and
    the time the first of ``TIME_NAMES`` that can be read. Returns them and how many of
    the items on the page were for those accounts but held no text Posts could read."""
    out: list[dict[str, Any]] = []
    unreadable = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        account = str(item.get("account_id") or "")
        if wanted and account not in wanted:
            continue
        text = first_text(item, TEXT_NAMES)
        if not text:
            unreadable += 1
            continue
        when = next((t for t in (from_service_time(item.get(n)) for n in TIME_NAMES)
                     if t is not None), None)
        out.append({"text": text[:POST_TEXT_MOST], "account_id": account, "when_ms": when})
    return out, unreadable


def listed_posts(items: Any) -> tuple[list[dict[str, Any]], int]:
    """The posts in one page of the scheduled list, as ``{id, account_id, when_ms, text,
    pictures}``, and how many entries were not posts Posts could read (not an object, or
    with no id). Never changes anything; the text is kept as it came, never read as markup."""
    out: list[dict[str, Any]] = []
    skipped = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or isinstance(item.get("id"), bool) \
                or not str(item.get("id") or "").strip():
            skipped += 1
            continue
        pictures = item.get("attachments")
        out.append({"id": str(item["id"]).strip(), "account_id": str(item.get("account_id") or ""),
                    "when_ms": from_service_time(item.get("publish_at")),
                    "text": first_text(item, TEXT_NAMES)[:LISTED_TEXT_MOST],
                    "pictures": len(pictures) if isinstance(pictures, list) else 0})
    return out, skipped


def _who(copy: dict[str, Any]) -> str:
    """An account as the owner knows it: its label when known, else its id."""
    if copy.get("label"):
        return str(copy["label"])
    number = str(copy.get("account", "")).partition(":")[2]
    return f"account {number}" if number else "an account"


# --- the scheduler --------------------------------------------------------------------------


class SocialBu:
    id = "socialbu"
    label = LABEL
    sends_out = True
    can_edit = True

    def __init__(self, ctx: Any):
        self.ctx = ctx

    # --- asking ---------------------------------------------------------------------------

    def _key(self) -> str | None:
        try:
            value = self.ctx.app.secrets.get(SECRET)
        except CallError as error:
            log.info("could not read the SocialBu key: %s", error.code)
            return None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _ask(self, key: str, method: str, path: str, *, json: Any = None,
             files: bool = False, empty_ok: bool = False) -> Answer:
        """One request, classified. Never retried."""
        cfg = self.ctx.cfg()
        try:
            reply = self.ctx.app.net.fetch(
                f"{BASE}{path}", method=method, json=json,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                fresh_connection=files,
                timeout_s=None if files else int(cfg.request_timeout_s))
        except Exception as error:  # noqa: BLE001 - every failure is sorted by classify
            answer = classify(None, error, key)
        else:
            answer = classify(reply, None, key, empty_ok=empty_ok)
        log.info("SocialBu %s %s: %s %s", method, path.split("?")[0], answer.kind,
                 answer.status or "")
        return answer

    def _read(self, key: str, external_id: str) -> tuple[Answer, dict[str, Any]]:
        answer = self._ask(key, "GET", f"/posts/{external_id}")
        post: dict[str, Any] = {}
        if answer.kind == "yes" and isinstance(answer.data, dict):
            inner = answer.data.get("post")
            post = inner if isinstance(inner, dict) else answer.data
        return answer, post

    def _copies(self, upstream: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [copy for copy in upstream if copy.get("external_id")]

    # --- the protocol ---------------------------------------------------------------------

    def needs(self, page: dict[str, Any]) -> str | None:
        if self._key() is None:
            return ("Posts has no SocialBu key. Type it in on Posts' Settings page under "
                    "Apps, then book again.")
        mine = [a for a in page.get("accounts") or [] if a.get("scheduler") == self.id]
        if not mine:
            return ("This brand has no SocialBu account. Add one under Brands, with SocialBu "
                    "and the account's id, then book again.")
        for account in mine:
            if account_number(account.get("account_id")) is None:
                return (f"The SocialBu account id \"{account.get('account_id')}\" on this "
                        "brand is not a number. Copy the number SocialBu shows for that "
                        "account onto the brand, then book again.")
        return None

    def _upload(self, key: str, picture: dict[str, str]) -> tuple[str | None, str]:
        """``(upload_token, "")``, or ``(None, why)``."""
        answer = self._ask(key, "POST", "/upload_media_by_url", files=True, json={
            "url": {"$file": picture["ref"], "as": "data_url"},
            "name": picture["name"][:255]})
        token = answer.data.get("upload_token") if isinstance(answer.data, dict) else None
        if answer.kind == "yes" and isinstance(token, str) and token:
            return token, ""
        return None, answer.said or "SocialBu gave no upload token"

    def _size(self, picture: dict[str, str]) -> int | None:
        try:
            return self.ctx.app.files.path(picture["ref"]).stat().st_size
        except (OSError, ValueError, RuntimeError):
            return None

    def book(self, booking: Booking) -> Outcome:
        key = self._key()
        if key is None:
            return Outcome("refused", detail=NO_KEY)
        most = int(self.ctx.cfg().upload_max_mb)
        for picture in booking.pictures:
            problem = picture_problem(picture, self._size(picture), most)
            if problem:
                return Outcome("refused", detail=problem)
        tokens = []
        for picture in booking.pictures:
            token, why = self._upload(key, picture)
            if token is None:
                return Outcome("refused", detail=(
                    f"The picture \"{picture['name']}\" did not upload ({why}). Nothing was "
                    "booked. Try again, or book it without that picture."))
            tokens.append(token)
        if not still_ahead(booking.slot_ms, self.ctx.now()):
            return Outcome("refused", detail="That time passed before it could be sent, and "
                           "SocialBu would publish it at once. Choose a time in the future.")
        body: dict[str, Any] = {
            "accounts": [account_number(a["account_id"]) for a in booking.accounts],
            "publish_at": to_service_time(booking.slot_ms), "content": booking.text}
        if tokens:
            body["existing_attachments"] = [{"upload_token": t} for t in tokens]
        answer = self._ask(key, "POST", "/posts", json=body)
        posts = answer.data.get("posts") if isinstance(answer.data, dict) else None
        copies, missing = match_copies(posts, booking.accounts)
        if answer.kind == "none":
            return Outcome("unsure", copies, answer.said)
        if answer.kind == "no":
            if copies:  # it said no, yet named copies: never called booked
                return Outcome("unsure", copies, f"SocialBu said no ({answer.said}) but "
                               f"named {len(copies)} copies.")
            return Outcome("refused", detail=collision_sentence(answer.said) or answer.said)
        if missing:
            have = [a["label"] for a in booking.accounts if a["label"] not in missing]
            return Outcome("unsure", copies, (
                f"SocialBu answered for {len(copies)} of {len(booking.accounts)} accounts "
                f"({', '.join(have) or 'none'}); nothing came back for "
                f"{', '.join(missing)}."))
        return Outcome("sent", copies)

    def status(self, post_id: int, upstream: list[dict[str, Any]]) -> Seen:
        key = self._key()
        if key is None:
            return Seen(False, detail=NO_KEY)
        copies = []
        for copy in self._copies(upstream):
            answer, post = self._read(key, copy["external_id"])
            if answer.kind == "no" and answer.status in GONE:
                copies.append({"account": copy["account"], "external_id": copy["external_id"],
                               "state": "gone", "slot_ms": None, "said": "", "figures": None})
                continue
            if answer.kind != "yes" or not post:
                return Seen(False, detail=answer.said or "SocialBu gave no clear answer.")
            state, said = copy_state(post)
            copies.append({"account": copy["account"], "external_id": copy["external_id"],
                           "state": state, "said": scrub(said, key),
                           "slot_ms": from_service_time(post.get("publish_at")),
                           "figures": figures_of(post)})
        return Seen(True, copies)

    def _preflight(self, key: str, copies: list[dict[str, Any]]
                   ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], Outcome | None]:
        """Before any change: each copy exists, has not gone out, and may be edited.
        Returns each copy with the post as the service gave it, or the reason not to go
        on (nothing was changed)."""
        found = []
        for copy in copies:
            answer, post = self._read(key, copy["external_id"])
            who = _who(copy)
            if answer.kind == "no" and answer.status in GONE:
                return [], Outcome("refused", detail=f"The copy for {who} is no longer on "
                                   "SocialBu. Nothing was changed. Book it again if it "
                                   "should still go out.")
            if answer.kind != "yes" or not post:
                return [], Outcome("refused", detail=f"SocialBu could not be asked about "
                                   f"the copy for {who} ({answer.said or 'no answer'}). "
                                   "Nothing was changed. Try again later.")
            if copy_state(post)[0] == "published":
                return [], Outcome("refused", detail=f"It already went out on {who}. Nothing "
                                   "was changed.")
            if post.get("can_edit") is False:
                return [], Outcome("refused", detail=f"SocialBu does not let the copy for "
                                   f"{who} be changed now. Nothing was changed. Change it on "
                                   "SocialBu instead.")
            found.append((copy, post))
        return found, None

    def _change_each(self, upstream: list[dict[str, Any]], what: str,
                     body_for: Any) -> Outcome:
        key = self._key()
        if key is None:
            return Outcome("refused", detail=NO_KEY)
        copies = self._copies(upstream)
        if not copies:
            return Outcome("refused", detail="Posts holds no SocialBu copy of this post. "
                           "Nothing was changed. Book it again instead.")
        found, stop = self._preflight(key, copies)
        if stop is not None:
            return stop
        results = []
        for copy, post in found:
            results.append((copy, self._ask(key, "PATCH", f"/posts/{copy['external_id']}",
                                            json=body_for(post))))
        return summed(results, what)

    def move(self, post_id: int, upstream: list[dict[str, Any]], slot_ms: int) -> Outcome:
        if not still_ahead(slot_ms, self.ctx.now()):
            return Outcome("refused", detail="That time has passed, and SocialBu would "
                           "publish it at once. Choose a time in the future.")
        return self._change_each(upstream, "The move",
                                 lambda post: {"publish_at": to_service_time(slot_ms)})

    def edit_text(self, post_id: int, upstream: list[dict[str, Any]], text: str) -> Outcome:
        def body(post: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {"content": text}
            kept = kept_attachments(post)
            if kept is not None:
                out["existing_attachments"] = kept
            return out
        return self._change_each(upstream, "The change", body)

    def cancel(self, post_id: int, upstream: list[dict[str, Any]]) -> Outcome:
        key = self._key()
        if key is None:
            return Outcome("refused", detail=NO_KEY)
        results = []
        for copy in self._copies(upstream):
            answer = self._ask(key, "DELETE", f"/posts/{copy['external_id']}", empty_ok=True)
            if answer.kind == "no" and answer.status in GONE:
                answer = Answer("yes", answer.status, said="It was already gone.")
            results.append((copy, answer))
        return summed(results, "Taking it off")

    def scheduled(self) -> list[dict[str, Any]]:
        """Every post SocialBu holds that has not gone out, as ``{account, external_id,
        slot_ms}``. Any page that fails, or too many pages, raises ``Unavailable``: a list
        read in part is never taken as the whole."""
        key = self._key()
        if key is None:
            raise Unavailable(NO_KEY)
        out: list[dict[str, Any]] = []
        page = 1
        for _ in range(LIST_MOST_PAGES):
            answer = self._ask(key, "GET", f"/posts?perPage={LIST_PER_PAGE}&page={page}")
            if answer.kind != "yes" or not isinstance(answer.data, dict):
                raise Unavailable(answer.said or "SocialBu's list could not be read.")
            for item in answer.data.get("items") or []:
                if isinstance(item, dict) and item.get("id") and item.get("account_id"):
                    out.append({"account": f"{self.id}:{item['account_id']}",
                                "external_id": str(item["id"]),
                                "slot_ms": from_service_time(item.get("publish_at"))})
            following = answer.data.get("nextPage")
            if not following:
                return out
            page = int(following)
        raise Unavailable(f"SocialBu's list ran past {LIST_MOST_PAGES} pages, so it was not "
                          "trusted.")

    # --- reading, for the Scheduled view ----------------------------------------------------

    def has_key(self) -> bool:
        return self._key() is not None

    def listing(self, most_pages: int) -> dict[str, Any]:
        """Everything SocialBu holds that is scheduled, whoever booked it: ``{posts, pages,
        cut, skipped}``. At most ``most_pages`` pages are read; ``cut`` is True when
        SocialBu said there were more. Reads only. A page that fails, or answers in a shape
        Posts does not know, raises ``Unavailable``: a list read in part is never shown as
        the whole."""
        key = self._key()
        if key is None:
            raise Unavailable(NO_KEY_READ)
        posts: list[dict[str, Any]] = []
        skipped = 0
        page = 1
        for read in range(1, max(1, int(most_pages)) + 1):
            answer = self._ask(key, "GET", f"/posts?type=scheduled&page={page}")
            if answer.kind != "yes":
                raise self._unavailable(answer, "the posts it has scheduled")
            data = answer.data
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise Unavailable("SocialBu answered in a shape Posts does not know, so its "
                                  "list was not used. Nothing was changed.")
            found, bad = listed_posts(data["items"])
            posts += found
            skipped += bad
            following = data.get("nextPage")
            if not following:
                return {"posts": posts, "pages": read, "cut": False, "skipped": skipped}
            if isinstance(following, bool) or not str(following).isdigit() \
                    or int(following) <= page:
                raise Unavailable("SocialBu gave a next page that makes no sense, so its list "
                                  "was not used. Nothing was changed.")
            page = int(following)
        return {"posts": posts, "pages": read, "cut": True, "skipped": skipped}

    # --- reading, for the page's forms ------------------------------------------------------

    def _unavailable(self, answer: Answer, what: str) -> Unavailable:
        said = answer.said or "no answer came back"
        if answer.kind == "no":
            return Unavailable(f"SocialBu said no when Posts asked for {what} ({said}). "
                               "Nothing was changed.")
        return Unavailable(f"SocialBu gave no clear answer when Posts asked for {what} "
                           f"({said}). Nothing was changed. Try again later.")

    def accounts(self) -> list[dict[str, str]]:
        """The accounts the owner has on SocialBu, as ``{account_id, label, network}``.
        One request, never retried. ``Unavailable`` when it cannot tell."""
        key = self._key()
        if key is None:
            raise Unavailable(NO_KEY_READ)
        answer = self._ask(key, "GET", "/posts/supported-options")
        if answer.kind == "no" and answer.status == 404 \
                and "no matching accounts" in answer.said.lower():
            return []
        if answer.kind != "yes" or not isinstance(answer.data, dict):
            raise self._unavailable(answer, "its accounts")
        return accounts_listed(answer.data.get("accounts"))

    def recent(self, account_ids: list[str], limit: int) -> list[dict[str, Any]]:
        """The newest posts that went out on these accounts, newest first, as ``{text,
        when_ms, account_id}``: at most ``limit``. A failure of the first page is
        ``Unavailable``; a later page failing leaves what was read."""
        key = self._key()
        if key is None:
            raise Unavailable(NO_KEY_READ)
        numbers = [str(n) for n in (account_number(a) for a in account_ids) if n is not None]
        if not numbers:
            raise Unavailable("None of those accounts has a SocialBu number, so its posts "
                              "cannot be read. Copy the number SocialBu shows for each "
                              "account, then try again.")
        wanted = set(numbers)
        filters = "".join(f"&accounts%5B%5D={n}" for n in numbers)
        out: list[dict[str, Any]] = []
        unreadable = 0
        page = 1
        for _ in range(RECENT_MOST_PAGES):
            answer = self._ask(key, "GET", f"/posts?type=published&perPage={LIST_PER_PAGE}"
                                           f"&page={page}{filters}")
            if answer.kind != "yes" or not isinstance(answer.data, dict):
                if page == 1:
                    raise self._unavailable(answer, "its published posts")
                break
            found, bad = posts_read(answer.data.get("items"), wanted)
            out += found
            unreadable += bad
            following = answer.data.get("nextPage")
            if not following or not str(following).isdigit():
                break
            page = int(following)
        if not out and unreadable:
            raise Unavailable("SocialBu answered, but the posts it gave held no text Posts "
                              "could read. Nothing was changed.")
        out.sort(key=lambda post: post["when_ms"] or 0, reverse=True)
        return out[:max(0, int(limit))]
