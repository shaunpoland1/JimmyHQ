"""Buffer: books each post as text on the Buffer channels the owner listed on its page.

Every request goes through JimmyHQ's ``net.fetch`` to exactly ``api.buffer.com``, which the
manifest asks for, with the owner's key from the app's Settings page. With no key the
scheduler says so and nothing is sent. Nothing of any one owner's is built in: the
channels are the ids the owner typed on each page. Text posts only, for now: a booking
with pictures is refused before anything is sent.

Each answer is classified once, by ``classify``: a definite yes, a definite no (with the
service's own words), or no answer (a timeout, a server error, an answer that cannot be
read, an error kind the documentation does not call a refusal). The booking path turns no
answer into ``unsure``; nothing here ever tries again.

What the service does that shapes this file. Everything below is from Buffer's own
documentation, read for this file (nothing was tried against the real service):

- One GraphQL endpoint, a ``POST`` to the host's root with a JSON body ``{"query": ...}``
  and ``Authorization: Bearer <key>`` (guides/authentication, examples/create-scheduled-post).
  The documentation shows no ``variables``, so values are written into the query as quoted
  literals (``quoted``) and never anything else.
- ``createPost`` makes one post for one channel; there is no call for several, so a
  booking is one request per channel, in order. ``mode: customScheduled`` with ``dueAt``
  (ISO 8601, UTC) schedules at a time; ``schedulingType: automatic`` publishes by itself
  (guides/posts-and-scheduling). The type page lists ``assets`` and ``needsApproval`` as
  required, though the guide's examples leave them out: both are sent, empty and false
  (types/CreatePostInput). There is no idempotency key anywhere in the documentation, so
  a second ``createPost`` would make a second post: that is why nothing is ever retried,
  and why ``scheduled()`` exists, for reconcile to find a booking that got no answer.
- The answer to a mutation is a union: ``PostActionSuccess`` (with the ``post``) or an
  error type that implements ``MutationError`` (guides/error-handling,
  types/PostActionPayload). ``__typename`` is asked for so each is told apart.
- GraphQL always answers HTTP 200, even for a failure, except a rate limit, which is HTTP
  429 with ``RATE_LIMIT_EXCEEDED`` (guides/error-handling, guides/api-limits). A failure
  is either a typed error in ``data`` (user-fixable: ``InvalidInputError``,
  ``LimitReachedError``, ``NotFoundError``, ``UnauthorizedError``: definite no) or an
  ``errors`` array with ``extensions.code``: ``UNAUTHORIZED``, ``FORBIDDEN``,
  ``NOT_FOUND`` and ``RATE_LIMIT_EXCEEDED`` are definite no; ``UNEXPECTED``, any other
  code, and any typed error the documentation does not call user-fixable are no answer.
  Any HTTP status other than 200 and 429 is not described, so it is no answer.
- ``post(input: {id})`` reads one post: ``status`` (``draft``, ``needs_approval``,
  ``scheduled``, ``sending``, ``sent``, ``error``), ``dueAt``, ``error { message }`` and
  ``metrics { type value unit }``, null until it is sent, so one read tells both
  (types/Post, types/PostStatus, guides/post-metrics). Many are read in one request as
  aliased ``post`` lookups, at most 30 aliases a query (guides/efficient-api-usage,
  guides/api-limits; ``BATCH_MOST`` is 25). A post that is no longer a scheduled one (a
  draft, or waiting for approval) will not go out by itself, so it is reported as gone; a
  create answered with such a status is never called booked. A post being sent is never
  changed or taken off: it may already be out. Buffer gathers figures once a day, so they
  are asked for no more often than that (``figures_every_ms``).
- ``editPost(input: {id, text, dueAt, mode})`` changes a post; what is left out is kept
  (types/EditPostInput). ``deletePost(input: {id})`` answers ``DeletePostSuccess`` or an
  error (types/DeletePostInput, types/DeletePostPayload, types/DeletePostSuccess).
- ``account { organizations { id } }`` lists the organizations, and ``posts(first, after,
  input: {organizationId, filter: {status: [...]}, sort: [...]})`` lists posts with cursor
  pages (guides/getting-started, examples/get-scheduled-posts, types/PostsInput,
  types/PostsFiltersInput, types/PostSortInput). That list is what ``scheduled()`` reads,
  with each post's ``text``, and filtered by ``channelIds`` and by ``dueAt`` (a
  DateTimeComparator of ``start`` and ``end``, types/DateTimeComparator) to the channels
  and minutes of the unsure bookings it is asked about.
- Rate limits are per plan: 150 requests in 15 minutes and 600 in 24 hours on the free
  plan, shared with everything else on the account (guides/api-limits). A 429 is a
  refusal at the door and costs nothing; its ``Retry-After`` (seconds) is kept, nothing is
  sent to Buffer until it has passed, and the queue says so.
- Buffer checks the text against each network's length limit and refuses with a typed error
  whose message says so (guides/character-limits): that message is passed on as it is.

- Reading, for the page's "Add an account" and "Fill in from its posts": ``channels(input:
  {organizationId})`` is a plain list of ``Channel`` (not a connection, not paged), and the
  fields used are ``id``, ``name``, ``displayName`` and ``service`` (examples/get-channels,
  types/Channel, types/ChannelsInput, types/Service). The sent posts of channels come from
  ``posts(first, after, input: {organizationId, filter: {status: [sent], channelIds},
  sort: [{field: dueAt, direction: desc}]})`` asking ``id text sentAt createdAt channelId``
  (examples/get-posts-for-channels, get-paginated-posts, types/PostsFiltersInput, types/Post).
  What the documentation does not settle: the largest ``first`` allowed (the examples' 20 is
  used), whether ``sentAt`` is filled for posts made on Buffer's own site (``createdAt`` is
  the fallback), and the sort for sent posts (the example sorts by ``dueAt``).

Not settled by the documentation either, and how this file reads it (each has a test):

- An answer with both ``data`` and ``errors``: the error guide shows only ``data: null``
  but says even a success may carry errors, so any ``errors`` beside data that holds
  anything is no answer (unsure), never a no.
- A request Buffer could not read: its pages name no code for it. The GraphQL server's
  standard ``GRAPHQL_VALIDATION_FAILED`` and ``GRAPHQL_PARSE_FAILED``, with no data, on
  HTTP 200 or 400, are a definite no (GraphQL checks a request before running any of
  it), said as a fault in Posts and logged as an error. ``BAD_USER_INPUT`` is not taken
  so, since it can come from a field that already ran.
- Whether Buffer holds one post a minute on a channel: nothing says so, and the owner
  may make his own there. A post is found again by its id when Posts has it, else only
  when it is the one post in that minute on that channel and its text is the booked text
  (after the same tidying of spaces and line breaks); Buffer may change a post's text, so
  another text keeps the booking unsure, as does more than one post.
- Whether ``channelIds`` may name a channel of another organization than the one asked:
  the filter is sent only when the key sees one organization, and the list is cut to
  those channels here either way.
- Whether a request of many aliases counts as one against the rate limit: the efficient
  usage guide says aliased lookups are one request; the limits page does not say.
- What "not found" means for a copy whose time has passed: it may have gone out and been
  removed, so it is ``lost``, never taken as not sent.

The pages read: developers.buffer.com guides/getting-started, authentication,
posts-and-scheduling, error-handling, api-limits, character-limits, post-metrics,
api-standards, graphql-intro; examples/create-scheduled-post, get-channels,
get-scheduled-posts, get-paginated-posts, get-posts-for-channels, get-organizations;
types/CreatePostInput, EditPostInput, DeletePostInput, DeletePostPayload,
DeletePostSuccess, VoidMutationError, MutationError types (InvalidInputError,
LimitReachedError, NotFoundError, UnexpectedError, RestProxyError), PostActionPayload,
PostActionSuccess, Post, PostStatus, ShareMode, PostsInput, PostsFiltersInput,
PostSortInput, PostSortableKey, SortDirection, PostMetric, Channel, ChannelsInput, Service,
Account, Organization, DateTimeComparator, PostsResults, PostsEdge, PaginationPageInfo,
PostPublishingError, PostInput; guides/efficient-api-usage; reference.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jimmyhq_app import CallError

from posts.schedulers import Booking, Outcome, Seen, Unavailable

log = logging.getLogger("posts.buffer")

HOST = "api.buffer.com"
URL = f"https://{HOST}"
SECRET = "buffer_key"  # noqa: S105 - the name of the secret, not its value
LABEL = "Buffer"
MINUTE_MS = 60_000
LIST_PER_PAGE = 20  # the page size the documentation's own examples use
BATCH_MOST = 25  # copies read in one request, as aliases: the documentation caps 30 a query
WAIT_UNSAID_MS = 15 * MINUTE_MS  # a rate limit that gives no Retry-After: its smallest window
DAY_MS = 24 * 60 * MINUTE_MS
WAIT_KEY = "buffer.wait_until"  # Posts' own record of when Buffer may be asked again
LIST_MOST_PAGES = 50  # past this the list is not trusted: reconcile skips it
RECENT_MOST_PAGES = 5  # sent posts are read this many pages deep for each organization
POST_TEXT_MOST = 2000  # characters of one sent post kept

# Platform refusals that mean nothing left this computer.
NOT_SENT = frozenset({"not_allowed", "invalid", "not_found", "quota"})

# ``errors[].extensions.code`` values the documentation describes as a refusal: the
# request did not do anything. Any other code (UNEXPECTED, one not listed) is no answer.
REFUSED_CODES = frozenset({"UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND", "RATE_LIMIT_EXCEEDED"})
# The standard GraphQL codes for a request that was never run: it could not be read, or did
# not fit the schema. GraphQL checks a request before it runs any of it, so nothing was
# done. Buffer's pages do not list these (they are the GraphQL server's own); BAD_USER_INPUT
# is left out because it can come from inside a field that already ran.
INVALID_CODES = frozenset({"GRAPHQL_VALIDATION_FAILED", "GRAPHQL_PARSE_FAILED"})
NOT_UNDERSTOOD = ("Buffer did not understand the request. This is a fault in Posts, not in "
                  "your post: nothing was booked.")
# Typed errors in ``data`` the documentation calls user-fixable (validation, limits).
REFUSED_TYPES = frozenset({"InvalidInputError", "LimitReachedError", "NotFoundError",
                           "UnauthorizedError"})
SUCCESS_TYPES = frozenset({"PostActionSuccess", "DeletePostSuccess"})
# ``Post.status`` values, as the documentation lists them (types/PostStatus). ``sending``
# waits to go out, but is never changed or taken off: it may already be out.
WAITING = frozenset({"scheduled", "sending"})
NOT_GOING = frozenset({"draft", "needs_approval"})

NO_KEY_READ = ("Posts has no Buffer key. Type it in on Posts' Settings page under Apps, "
               "then try again.")
NO_KEY = ("Posts has no Buffer key, so nothing was sent. Type it in on Posts' Settings "
          "page under Apps, then try again.")
TEXT_ONLY = ("Buffer bookings are text only for now, and this post has a picture. Nothing "
             "was booked. Choose another tool for this brand, or book a post without a "
             "picture.")


# --- the rules, one function each -----------------------------------------------------------


def scrub(text: str, key: str | None) -> str:
    """Text that may be shown or kept, with the key taken out wherever it appears."""
    text = str(text or "")
    if key:
        text = text.replace(key, "[your key]")
    return text


def quoted(value: Any) -> str:
    """A value as a GraphQL string literal. JSON's string escapes are GraphQL's, so a
    quote, a backslash or a line break in a post's text cannot end the string early."""
    return json.dumps(str(value), ensure_ascii=False)


def to_service_time(ms: int) -> str:
    """Unix milliseconds as the service writes a time: ISO 8601 in UTC, with milliseconds
    and a ``Z``, as the documentation's own examples do."""
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def from_service_time(text: Any) -> int | None:
    """The service's time as unix milliseconds, or None when it cannot be read. A time with
    no zone is taken as UTC."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        when = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return int(when.timestamp() * 1000)


def same_minute(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and a // MINUTE_MS == b // MINUTE_MS


def still_ahead(slot_ms: int, now_ms: int) -> bool:
    """A time that has passed is never sent: a moment may pass while Posts works."""
    return slot_ms > now_ms


def service_message(errors: Any) -> str:
    """The first of the service's own ``errors`` messages, or ''."""
    for error in errors if isinstance(errors, list) else []:
        if isinstance(error, dict) and isinstance(error.get("message"), str) \
                and error["message"].strip():
            return error["message"].strip()[:300]
    return ""


def error_codes(errors: Any) -> list[str]:
    """Each entry's ``extensions.code``, '' where it has none."""
    out = []
    for error in errors if isinstance(errors, list) else []:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        code = extensions.get("code") if isinstance(extensions, dict) else None
        out.append(code if isinstance(code, str) else "")
    return out


def retry_after(reply: dict[str, Any]) -> str:
    """The seconds a rate limit says to wait, as a sentence, or ''."""
    headers = reply.get("headers")
    if isinstance(headers, dict):
        for name, value in headers.items():
            if str(name).lower() == "retry-after" and str(value).strip().isdigit():
                return f" Buffer says to wait {str(value).strip()} seconds."
    return ""


@dataclass(frozen=True)
class Answer:
    """One request's result. ``kind`` is ``yes``, ``no`` (a definite refusal; ``said`` has
    the service's words) or ``none`` (no answer that can be trusted either way). ``data``
    is the answer's ``data`` object. ``gone`` is True when every error was NOT_FOUND;
    ``invalid`` when Buffer could not read the request at all (a fault in Posts);
    ``wait_s`` is the ``Retry-After`` of a rate limit, in seconds, when it gave one."""

    kind: str
    status: int | None = None
    data: dict[str, Any] | None = None
    said: str = ""
    gone: bool = False
    invalid: bool = False
    limited: bool = False
    wait_s: int | None = None
    words: str = ""  # the service's own words when ``said`` is Posts' sentence


def has_data(data: Any) -> bool:
    """Whether an answer's ``data`` holds anything: a field that is not null."""
    return isinstance(data, dict) and any(value is not None for value in data.values())


def wait_seconds(reply: dict[str, Any]) -> int | None:
    """A rate limit's ``Retry-After``, in whole seconds, or None when it gave none."""
    headers = reply.get("headers")
    if isinstance(headers, dict):
        for name, value in headers.items():
            if str(name).lower() == "retry-after" and str(value).strip().isdigit():
                return int(str(value).strip())
    return None


def classify(reply: dict[str, Any] | None, error: Exception | None, key: str | None) -> Answer:
    """One answer, sorted. A refusal by JimmyHQ before sending is a no; a timeout, a broken
    connection, a status the documentation does not describe or a body that cannot be read
    is no answer; HTTP 429 is a no; an ``errors`` array beside ``data`` that holds anything
    is no answer (Buffer may have done it: the documentation says even a success can carry
    errors); an ``errors`` array with no data is a no only when every code in it is one the
    documentation calls a refusal, or one that says the request was never run (``invalid``);
    otherwise the answer has ``data`` and is a yes here (``payload`` then reads the
    mutation's own union)."""
    if error is not None:
        message = scrub(getattr(error, "message", "") or str(error), key)
        if isinstance(error, CallError) and error.code in NOT_SENT:
            return Answer("no", said=message)
        return Answer("none", said=message or "No answer came back.")
    if not isinstance(reply, dict) or not isinstance(reply.get("status"), int):
        return Answer("none", said="The answer could not be read.")
    status, text = reply["status"], str(reply.get("text") or "")
    body: Any = None
    if text.strip():
        try:
            body = json.loads(text)
        except ValueError:
            body = None
    errors = body.get("errors") if isinstance(body, dict) else None
    said = scrub(service_message(errors), key)
    if status == 429:
        return Answer("no", status, said=(said or "Buffer said there were too many requests.")
                      + retry_after(reply), limited=True, wait_s=wait_seconds(reply))
    codes = error_codes(errors) if errors else []
    never_run = bool(codes) and all(c in INVALID_CODES for c in codes) and isinstance(
        body, dict) and not has_data(body.get("data"))
    if never_run and status in (200, 400):  # a GraphQL server may answer 400 for these
        return Answer("no", status, said=NOT_UNDERSTOOD, invalid=True, words=said)
    if status != 200:
        return Answer("none", status, said=said or f"Buffer gave no clear answer (HTTP "
                                                   f"{status}).")
    if not isinstance(body, dict):
        return Answer("none", status, said="Buffer said yes but its answer could not be read.")
    if errors:
        if has_data(body.get("data")):
            return Answer("none", status, said=(
                "Buffer answered with a result and an error together, so it cannot be told "
                "whether it did it" + (f" (it said: {said})." if said else ".")))
        if codes and all(code in REFUSED_CODES for code in codes):
            return Answer("no", status, said=said or "Buffer refused it.",
                          gone=all(code == "NOT_FOUND" for code in codes))
        return Answer("none", status, said=said or "Buffer reported an error without saying "
                                                   "whether it did it.")
    data = body.get("data")
    if not isinstance(data, dict):
        return Answer("none", status, said="Buffer said yes but its answer held no data.")
    return Answer("yes", status, data)


@dataclass(frozen=True)
class Result:
    """What one mutation did to one post: ``yes`` (with the post's ``id``), ``no`` or
    ``none``, with the service's words."""

    kind: str
    said: str = ""
    id: str = ""
    post: dict[str, Any] | None = None
    gone: bool = False
    invalid: bool = False


def payload(answer: Answer, field: str) -> Result:
    """A mutation's union read: the success type is a yes (a yes without a post id is no
    answer), a user-fixable error type is a no, any other type is no answer."""
    if answer.kind != "yes":
        return Result(answer.kind, answer.said, gone=answer.gone, invalid=answer.invalid)
    found = (answer.data or {}).get(field)
    if not isinstance(found, dict):
        return Result("none", "Buffer said yes but its answer could not be read.")
    kind = found.get("__typename")
    message = str(found.get("message") or "").strip()[:300]
    if kind in SUCCESS_TYPES:
        post = found.get("post") if isinstance(found.get("post"), dict) else found
        post_id = post.get("id")
        if post_id is None or str(post_id).strip() == "":
            return Result("none", "Buffer said yes but gave no id for the post.")
        return Result("yes", id=str(post_id), post=post)
    if kind in REFUSED_TYPES:
        return Result("no", message or "Buffer refused it.", gone=kind == "NotFoundError")
    return Result("none", message or "Buffer gave an answer Posts does not recognise.")


def figures_of(post: dict[str, Any]) -> dict[str, float] | None:
    """The engagement figures on one post, ``{metric: number}``, from its ``metrics`` list of
    ``{type, value, unit}``; percentages are left out (they are not counts, and the figures
    are summed). None when it carries none."""
    rows = post.get("metrics")
    if not isinstance(rows, list):
        return None
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("type"), str):
            continue
        value = row.get("value")
        if row.get("unit") == "percentage" or isinstance(value, bool) \
                or not isinstance(value, int | float):
            continue
        out[row["type"][:40]] = out.get(row["type"][:40], 0) + float(value)
    return out


def gone_state(slot_ms: Any, now_ms: int) -> str:
    """A copy Buffer cannot find: ``gone`` while its time is ahead (it was taken off before
    it went out), ``lost`` once its time has passed or is not known (it may have gone out
    and then been removed, so it is never taken as not sent)."""
    return "gone" if isinstance(slot_ms, int) and slot_ms > now_ms else "lost"


def copy_state(post: dict[str, Any]) -> tuple[str, str] | None:
    """One post as ``(state, said)``: ``scheduled``, ``published``, ``failed`` (with the
    service's words) or ``gone`` (a draft, or waiting for approval: it will not go out by
    itself). None when the status is not one the documentation lists."""
    status = post.get("status")
    if status in WAITING:
        return "scheduled", ""
    if status == "sent":
        return "published", ""
    if status == "error":
        error = post.get("error")
        said = error.get("message") if isinstance(error, dict) else ""
        return "failed", str(said or "").strip()[:300]
    if status in NOT_GOING:
        return "gone", ""
    return None


def not_waiting_sentence(status: Any) -> str:
    """Why a post Buffer made is not booked: it was filed as something that will not go out
    by itself."""
    if status == "draft":
        return "Buffer made it as a draft there: finish it on Buffer or remove it."
    if status == "needs_approval":
        return ("Buffer made it as waiting for approval there: finish it on Buffer or remove "
                "it.")
    shown = f"\"{str(status)[:40]}\"" if status else "no status"
    return (f"Buffer made it but gave it {shown}, not scheduled: look at it on Buffer, then "
            "finish it there or remove it.")


def _who(copy: dict[str, Any]) -> str:
    """A channel as the owner knows it: its label when known, else its id."""
    if copy.get("label"):
        return str(copy["label"])
    number = str(copy.get("account", "")).partition(":")[2]
    return f"channel {number}" if number else "a channel"


def summed(results: list[tuple[dict[str, Any], Result]], what: str) -> Outcome:
    """One change made on each copy, summed up: all yes is sent; none reached and every
    no definite is refused; none reached with any no answer is unsure; some reached is
    partial, naming who has it and who has not."""
    reached = [copy for copy, result in results if result.kind == "yes"]
    failed = [(copy, result) for copy, result in results if result.kind != "yes"]
    if not failed:
        return Outcome("sent", [{"account": c["account"], "external_id": c["external_id"]}
                                for c in reached])
    words = "; ".join(f"{_who(c)}: {r.said or 'no answer'}" for c, r in failed)
    if reached:
        return Outcome("partial", [{"account": c["account"], "external_id": c["external_id"]}
                                   for c in reached],
                       f"{what} reached {', '.join(_who(c) for c in reached)} but not "
                       f"{', '.join(_who(c) for c, _ in failed)} ({words}).")
    if any(r.kind == "none" for _, r in failed):
        return Outcome("unsure", detail=f"Buffer gave no clear answer ({words}).")
    return Outcome("refused", detail=f"Buffer said no ({words}).")


# --- the requests, one function each --------------------------------------------------------

SUCCESS = "... on PostActionSuccess { post { id status dueAt channelId } }"
ERROR = "... on MutationError { message }"


def create_query(channel_id: str, text: str, slot_ms: int) -> str:
    return (f"mutation {{ createPost(input: {{text: {quoted(text)}, "
            f"channelId: {quoted(channel_id)}, schedulingType: automatic, "
            f"mode: customScheduled, dueAt: {quoted(to_service_time(slot_ms))}, "
            f"assets: [], needsApproval: false}}) {{ __typename {SUCCESS} {ERROR} }} }}")


def edit_query(post_id: str, *, text: str | None = None, slot_ms: int | None = None) -> str:
    fields = [f"id: {quoted(post_id)}"]
    if text is not None:
        fields.append(f"text: {quoted(text)}")
    if slot_ms is not None:
        fields += [f"dueAt: {quoted(to_service_time(slot_ms))}", "mode: customScheduled"]
    return (f"mutation {{ editPost(input: {{{', '.join(fields)}}}) {{ __typename {SUCCESS} "
            f"{ERROR} }} }}")


def delete_query(post_id: str) -> str:
    return (f"mutation {{ deletePost(input: {{id: {quoted(post_id)}}}) {{ __typename "
            f"... on DeletePostSuccess {{ id }} {ERROR} }} }}")


POST_FIELDS = "id status dueAt channelId error { message } metrics { type value unit }"


def read_query(post_id: str) -> str:
    """One post, with its figures: ``metrics`` is null until it is sent (types/Post), so
    one read tells both what became of it and how it did."""
    return f"query {{ post(input: {{id: {quoted(post_id)}}}) {{ {POST_FIELDS} }} }}"


def batch_query(post_ids: list[str]) -> str:
    """Several posts in one request, each under its own alias ``c0``, ``c1``…
    (guides/efficient-api-usage: aliased ``post`` lookups are one request; at most 30
    aliases a query, guides/api-limits)."""
    parts = " ".join(f"c{n}: post(input: {{id: {quoted(pid)}}}) {{ {POST_FIELDS} }}"
                     for n, pid in enumerate(post_ids))
    return f"query {{ {parts} }}"


def waiting_sentence(until_ms: int, now_ms: int) -> str:
    """Why nothing is asked of Buffer now: it said to wait."""
    minutes = max(1, -(-(until_ms - now_ms) // MINUTE_MS))
    span = f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes < 120 else \
        f"{round(minutes / 60)} hours"
    return (f"Buffer said Posts had asked too often, so Posts waits {span} more before "
            "asking it anything. Nothing was sent. Try again after that.")


ORGANIZATIONS = "query { account { organizations { id } } }"


def list_query(organization_id: str, after: str | None, *,
               channel_ids: list[str] | None = None,
               window: tuple[int, int] | None = None) -> str:
    """The scheduled posts of one organization, with their text: only on ``channel_ids``
    and due inside ``window`` (``(start_ms, end_ms)``) when given (types/PostsFiltersInput:
    ``channelIds``, and ``dueAt``, a DateTimeComparator of ``start`` and ``end``)."""
    cursor = f", after: {quoted(after)}" if after else ""
    more = ""
    if channel_ids:
        more += f", channelIds: [{', '.join(quoted(c) for c in channel_ids)}]"
    if window is not None:
        more += (f", dueAt: {{start: {quoted(to_service_time(window[0]))}, end: "
                 f"{quoted(to_service_time(window[1]))}}}")
    return (f"query {{ posts(first: {LIST_PER_PAGE}{cursor}, input: {{organizationId: "
            f"{quoted(organization_id)}, filter: {{status: [scheduled, sending]{more}}}, "
            "sort: [{field: dueAt, direction: asc}]}) { edges { node { id channelId dueAt "
            "status text } } pageInfo { hasNextPage endCursor } } }")


def channels_query(organization_id: str) -> str:
    return (f"query {{ channels(input: {{organizationId: {quoted(organization_id)}}}) "
            "{ id name displayName service } }")


def sent_query(organization_id: str, channel_ids: list[str], after: str | None) -> str:
    cursor = f", after: {quoted(after)}" if after else ""
    ids = ", ".join(quoted(c) for c in channel_ids)
    return (f"query {{ posts(first: {LIST_PER_PAGE}{cursor}, input: {{organizationId: "
            f"{quoted(organization_id)}, filter: {{status: [sent], channelIds: [{ids}]}}, "
            "sort: [{field: dueAt, direction: desc}]}) { edges { node { id text sentAt "
            "createdAt channelId } } pageInfo { hasNextPage endCursor } } }")


def channels_listed(found: Any) -> list[dict[str, str]]:
    """``{account_id, label, network}`` for each ``Channel`` in an answer, without repeats;
    an entry with no id is left out. The label is its display name, else its name."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or not item.get("id") or str(item["id"]) in seen:
            continue
        seen.add(str(item["id"]))
        name = " ".join(str(item.get("displayName") or item.get("name") or "").split())[:80]
        out.append({"account_id": str(item["id"]), "label": name or f"Channel {item['id']}",
                    "network": " ".join(str(item.get("service") or "").split())[:40]})
    return out


def sent_read(found: Any, wanted: set[str]) -> list[dict[str, Any]]:
    """The sent posts in one answer's ``edges``, as ``{text, when_ms, account_id}``, for
    the channels asked about only; a post with no text is left out."""
    out: list[dict[str, Any]] = []
    for edge in (found or {}).get("edges") or [] if isinstance(found, dict) else []:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        channel = str(node.get("channelId") or "")
        text = node.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text or (wanted and channel not in wanted):
            continue
        when = from_service_time(node.get("sentAt"))
        out.append({"text": text[:POST_TEXT_MOST], "account_id": channel,
                    "when_ms": when if when is not None else from_service_time(
                        node.get("createdAt"))})
    return out


# --- the scheduler --------------------------------------------------------------------------


class Buffer:
    id = "buffer"
    label = LABEL
    sends_out = True
    can_edit = True
    # Buffer gathers a sent post's figures once a day (types/Post, metricsUpdatedAt), so
    # they are asked for at most once a day, and no more once they stop changing.
    figures_every_ms = DAY_MS

    def __init__(self, ctx: Any):
        self.ctx = ctx

    # --- asking ---------------------------------------------------------------------------

    def _key(self) -> str | None:
        try:
            value = self.ctx.app.secrets.get(SECRET)
        except CallError as error:
            log.info("could not read the Buffer key: %s", error.code)
            return None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def wait_until(self) -> int | None:
        """When Buffer may be asked again after a rate limit, or None when it may now."""
        try:
            until = self.ctx.store.get_meta(WAIT_KEY)
        except Exception:  # noqa: BLE001 - no record: no wait
            return None
        return int(until) if isinstance(until, int) and until > self.ctx.now() else None

    def _ask(self, key: str, what: str, query: str) -> Answer:
        """One request, classified. Never retried. While Buffer has asked Posts to wait (a
        rate limit's ``Retry-After``), nothing is sent: the answer is a no that says so."""
        until = self.wait_until()
        if until is not None:
            return Answer("no", said=waiting_sentence(until, self.ctx.now()), limited=True)
        cfg = self.ctx.cfg()
        try:
            reply = self.ctx.app.net.fetch(
                URL, method="POST", json={"query": query},
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                timeout_s=int(cfg.request_timeout_s))
        except Exception as error:  # noqa: BLE001 - every failure is sorted by classify
            answer = classify(None, error, key)
        else:
            answer = classify(reply, None, key)
        log.info("Buffer %s: %s %s", what, answer.kind, answer.status or "")
        if answer.limited:  # wait as long as Buffer said, and say so on the screen
            wait = answer.wait_s * 1000 if answer.wait_s is not None else WAIT_UNSAID_MS
            self.ctx.store.set_meta(WAIT_KEY, self.ctx.now() + wait)
        if answer.invalid:  # a fault in Posts' own request: loud, so it is not missed
            log.error("Buffer did not understand Posts' %s request: %s", what,
                      answer.words or "no words")
        return answer

    def _mutate(self, key: str, field: str, query: str) -> Result:
        result = payload(self._ask(key, field, query), field)
        return Result(result.kind, scrub(result.said, key), result.id, result.post,
                      result.gone, result.invalid)

    def _read(self, key: str, external_id: str) -> tuple[Answer, dict[str, Any]]:
        answer = self._ask(key, "post", read_query(external_id))
        post = (answer.data or {}).get("post") if answer.kind == "yes" else None
        return answer, post if isinstance(post, dict) else {}

    def _read_many(self, key: str, external_ids: list[str]
                   ) -> dict[str, tuple[Answer, dict[str, Any]]]:
        """Each post read, ``BATCH_MOST`` to a request. A batch whose answer is not a clear
        yes holding every post (an error for one of them, say) is read again one post at a
        time; a rate limit stops it all, and what was not read is left as that no."""
        out: dict[str, tuple[Answer, dict[str, Any]]] = {}
        ids = list(dict.fromkeys(external_ids))
        for start in range(0, len(ids), BATCH_MOST):
            group = ids[start:start + BATCH_MOST]
            if len(group) > 1:
                answer = self._ask(key, "posts by id", batch_query(group))
                found = (answer.data or {}) if answer.kind == "yes" else {}
                posts = [found.get(f"c{n}") for n in range(len(group))]
                if answer.kind == "yes" and all(isinstance(p, dict) for p in posts):
                    out.update({pid: (answer, post) for pid, post in zip(group, posts,
                                                                          strict=True)})
                    continue
                if answer.limited:
                    out.update({pid: (answer, {}) for pid in ids[start:]})
                    return out
            for pid in group:
                out[pid] = self._read(key, pid)
                if out[pid][0].limited:
                    out.update({p: out[pid] for p in ids if p not in out})
                    return out
        return out

    def _organizations(self, key: str) -> list[str]:
        """The ids of the organizations this key can see. ``Unavailable`` when it cannot
        tell, or sees none."""
        answer = self._ask(key, "organizations", ORGANIZATIONS)
        account = (answer.data or {}).get("account") if answer.kind == "yes" else None
        orgs = account.get("organizations") if isinstance(account, dict) else None
        ids = [str(o["id"]) for o in orgs if isinstance(o, dict) and o.get("id")] \
            if isinstance(orgs, list) else []
        if not ids:
            raise Unavailable(answer.said or "Buffer lists no organization for this key.")
        return ids

    def _copies(self, upstream: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [copy for copy in upstream if copy.get("external_id")]

    # --- the protocol ---------------------------------------------------------------------

    def needs(self, page: dict[str, Any]) -> str | None:
        if self._key() is None:
            return ("Posts has no Buffer key. Type it in on Posts' Settings page under "
                    "Apps, then book again.")
        mine = [a for a in page.get("accounts") or [] if a.get("scheduler") == self.id]
        if not mine:
            return ("This brand has no Buffer channel. Add one under Brands, with Buffer and "
                    "the channel's id, then book again.")
        return None

    def book(self, booking: Booking) -> Outcome:
        key = self._key()
        if key is None:
            return Outcome("refused", detail=NO_KEY)
        if booking.pictures:
            return Outcome("refused", detail=TEXT_ONLY)
        if not still_ahead(booking.slot_ms, self.ctx.now()):
            return Outcome("refused", detail="That time passed before it could be sent. "
                           "Nothing was booked. Choose a time in the future.")
        done: list[dict[str, Any]] = []
        stopped: tuple[dict[str, str], Result] | None = None
        for account in booking.accounts:
            result = self._mutate(key, "createPost",
                                  create_query(account["account_id"], booking.text,
                                               booking.slot_ms))
            if result.kind == "yes" and result.post and result.post.get("channelId") not in (
                    None, account["account_id"]):
                result = Result("none", "Buffer's answer named another channel.")
            if result.kind == "yes" and (result.post or {}).get("status") not in WAITING:
                stopped, named = (account, Result("none", not_waiting_sentence(
                    (result.post or {}).get("status")), result.id)), result.id
                break  # it exists, but will not go out by itself: never called booked
            if result.kind != "yes":
                stopped, named = (account, result), ""
                break  # the rest are not tried: nothing more goes out after a bad answer
            done.append({"account": account["account"], "external_id": result.id})
        if stopped is None:
            return Outcome("sent", done)
        account, result = stopped
        held = ({"account": account["account"], "external_id": named},) if named else ()
        left = [a["label"] for a in booking.accounts[len(done) + 1:]]
        not_tried = f" It was not tried for {', '.join(left)}." if left else ""
        if not done:
            if result.invalid:
                return Outcome("refused", detail=NOT_UNDERSTOOD)
            if result.kind == "no":
                return Outcome("refused", detail=f"Buffer said no for {account['label']}: "
                               f"{result.said}{not_tried}")
            return Outcome("unsure", list(held), detail=f"Buffer gave no clear answer for "
                           f"{account['label']} ({result.said}).{not_tried}")
        names = {a["account"]: a["label"] for a in booking.accounts}
        have = ", ".join(names.get(c["account"], c["account"]) for c in done)
        maybe = " It may be booked there too." if result.kind == "none" and not named else ""
        return Outcome("partial", done, (
            f"Buffer booked it for {len(done)} of {len(booking.accounts)} channels "
            f"({have}); for {account['label']} it said: {result.said or 'nothing clear'}."
            f"{maybe}{not_tried}"), named=held,
            unclear=(account["account"],) if result.kind == "none" and not named else ())

    def status(self, post_id: int, upstream: list[dict[str, Any]]) -> Seen:
        return self.status_many([(post_id, upstream)])[post_id]

    def status_many(self, asked: list[tuple[int, list[dict[str, Any]]]]) -> dict[int, Seen]:
        """``status`` for many posts at once: one read for each copy, with its figures,
        batched into as few requests as the documentation allows."""
        key = self._key()
        if key is None:
            return {post_id: Seen(False, detail=NO_KEY) for post_id, _ in asked}
        every = [c["external_id"] for _, upstream in asked for c in self._copies(upstream)]
        reads = self._read_many(key, every) if every else {}
        return {post_id: self._seen(key, upstream, reads) for post_id, upstream in asked}

    def _seen(self, key: str, upstream: list[dict[str, Any]],
              reads: dict[str, tuple[Answer, dict[str, Any]]]) -> Seen:
        copies = []
        for copy in self._copies(upstream):
            answer, post = reads[copy["external_id"]]
            base = {"account": copy["account"], "external_id": copy["external_id"]}
            if answer.kind == "no" and answer.gone:
                copies.append({**base, "state": gone_state(copy.get("slot_ms"),
                                                           self.ctx.now()),
                               "slot_ms": None, "said": "", "figures": None})
                continue
            if answer.kind != "yes" or not post:
                return Seen(False, detail=answer.said or "Buffer gave no clear answer.")
            seen = copy_state(post)
            if seen is None:
                return Seen(False, detail="Buffer gave a status Posts does not know.")
            state, said = seen
            copies.append({**base, "state": state, "said": scrub(said, key),
                           "slot_ms": from_service_time(post.get("dueAt")),
                           "figures": figures_of(post) if state == "published" else None,
                           "status": post.get("status")})
        return Seen(True, copies)

    def _preflight(self, key: str, copies: list[dict[str, Any]]) -> Outcome | None:
        """Before any change: each copy exists, has not gone out and is still a scheduled
        post. Returns the reason not to go on (nothing was changed), or None."""
        for copy in copies:
            answer, post = self._read(key, copy["external_id"])
            who = _who(copy)
            if answer.kind == "no" and answer.gone:
                return Outcome("refused", detail=f"The copy for {who} is no longer on "
                               "Buffer. Nothing was changed. Book it again if it should "
                               "still go out.")
            if answer.kind != "yes" or not post:
                return Outcome("refused", detail=f"Buffer could not be asked about the "
                               f"copy for {who} ({answer.said or 'no answer'}). Nothing "
                               "was changed. Try again later.")
            seen = copy_state(post)
            if seen is None:
                return Outcome("refused", detail=f"Buffer shows the copy for {who} in a "
                               "state Posts does not know. Nothing was changed. Change it "
                               "on Buffer instead.")
            if seen[0] == "published":
                return Outcome("refused", detail=f"It already went out on {who}. Nothing "
                               "was changed.")
            if post.get("status") == "sending":
                return Outcome("refused", detail=f"Buffer is sending the copy for {who} "
                               "right now, so it may already be out. Nothing was changed. "
                               "Look on the account once it has gone.")
            if seen[0] != "scheduled":
                return Outcome("refused", detail=f"The copy for {who} is no longer a "
                               "scheduled post on Buffer. Nothing was changed. Book it "
                               "again if it should still go out.")
        return None

    def _change_each(self, upstream: list[dict[str, Any]], what: str, query_for: Any,
                     check: Any = None) -> Outcome:
        key = self._key()
        if key is None:
            return Outcome("refused", detail=NO_KEY)
        copies = self._copies(upstream)
        if not copies:
            return Outcome("refused", detail="Posts holds no Buffer copy of this post. "
                           "Nothing was changed. Book it again instead.")
        stop = self._preflight(key, copies)
        if stop is not None:
            return stop
        results = []
        for copy in copies:
            result = self._mutate(key, "editPost", query_for(copy["external_id"]))
            if result.kind == "yes" and check is not None and not check(result.post or {}):
                result = Result("none", "Buffer said yes but shows something else now.")
            results.append((copy, result))
        return summed(results, what)

    def move(self, post_id: int, upstream: list[dict[str, Any]], slot_ms: int) -> Outcome:
        if not still_ahead(slot_ms, self.ctx.now()):
            return Outcome("refused", detail="That time has passed. Nothing was changed. "
                           "Choose a time in the future.")

        def lands(post: dict[str, Any]) -> bool:
            shown = from_service_time(post.get("dueAt"))
            return shown is None or same_minute(shown, slot_ms)

        return self._change_each(upstream, "The move",
                                 lambda pid: edit_query(pid, slot_ms=slot_ms), lands)

    def edit_text(self, post_id: int, upstream: list[dict[str, Any]], text: str) -> Outcome:
        return self._change_each(upstream, "The change", lambda pid: edit_query(pid, text=text))

    def cancel(self, post_id: int, upstream: list[dict[str, Any]]) -> Outcome:
        key = self._key()
        if key is None:
            return Outcome("refused", detail=NO_KEY)
        results = []
        for copy in self._copies(upstream):
            read, post = self._read(key, copy["external_id"])
            seen = copy_state(post) if read.kind == "yes" and post else None
            if read.kind == "no" and read.gone:
                result = Result("yes", "It was already gone.", copy["external_id"])
            elif seen is not None and seen[0] == "published":
                result = Result("no", "It already went out, so it was not taken off.")
            elif read.kind == "yes" and post.get("status") == "sending":
                result = Result("no", "Buffer is sending it right now, so it may already be "
                                "out; it was not taken off.")
            else:  # a read that could not tell does not stop the delete
                result = self._mutate(key, "deletePost", delete_query(copy["external_id"]))
                if result.kind == "no" and result.gone:
                    result = Result("yes", "It was already gone.", copy["external_id"])
            results.append((copy, result))
        return summed(results, "Taking it off")

    def scheduled(self, accounts: list[str] | None = None,
                  around_ms: Any = None) -> list[dict[str, Any]]:
        """The posts Buffer holds that are scheduled or being sent, as ``{account,
        external_id, slot_ms, text, status}``. With ``accounts`` (Posts' own keys,
        ``buffer:<channel>``) and ``around_ms`` (a time, or several), only those channels
        and the minutes around those times are asked for: the channel filter is sent only
        when the key sees one organization (whether Buffer takes another organization's
        channel id there is not documented), and the list is cut to those channels here
        either way. Any request that fails, or too many pages, raises ``Unavailable``: a
        list read in part is never taken as the whole."""
        key = self._key()
        if key is None:
            raise Unavailable(NO_KEY)
        ids = self._organizations(key)
        channels = sorted({str(a).partition(":")[2] for a in accounts or []
                           if str(a).startswith(f"{self.id}:")}) or None
        times = [int(t) for t in (around_ms if isinstance(around_ms, list | tuple)
                                  else [around_ms]) if isinstance(t, int)]
        window = (min(times) - MINUTE_MS, max(times) + 2 * MINUTE_MS) if times else None
        out: list[dict[str, Any]] = []
        for organization in ids:
            after: str | None = None
            for _ in range(LIST_MOST_PAGES):
                answer = self._ask(key, "posts", list_query(
                    str(organization), after, window=window,
                    channel_ids=channels if len(ids) == 1 else None))
                found = (answer.data or {}).get("posts") if answer.kind == "yes" else None
                if not isinstance(found, dict):
                    raise Unavailable(answer.said or "Buffer's list could not be read.")
                for edge in found.get("edges") or []:
                    node = edge.get("node") if isinstance(edge, dict) else None
                    if not (isinstance(node, dict) and node.get("id") and node.get("channelId")):
                        continue
                    if channels and str(node["channelId"]) not in channels:
                        continue
                    out.append({"account": f"{self.id}:{node['channelId']}",
                                "external_id": str(node["id"]),
                                "slot_ms": from_service_time(node.get("dueAt")),
                                "text": node.get("text") if isinstance(node.get("text"), str)
                                else None, "status": node.get("status")})
                info = found.get("pageInfo")
                after = info.get("endCursor") if isinstance(info, dict) else None
                if not (isinstance(info, dict) and info.get("hasNextPage") and after):
                    break
            else:
                raise Unavailable(f"Buffer's list ran past {LIST_MOST_PAGES} pages, so it "
                                  "was not trusted.")
        return out

    # --- reading, for the page's forms ------------------------------------------------------

    def accounts(self) -> list[dict[str, str]]:
        """The channels the owner has on Buffer, as ``{account_id, label, network}``, from
        every organization the key can see. A request that fails raises ``Unavailable``:
        a list read in part is never given as the whole."""
        key = self._key()
        if key is None:
            raise Unavailable(NO_KEY_READ)
        out: list[dict[str, str]] = []
        for organization in self._organizations(key):
            answer = self._ask(key, "channels", channels_query(organization))
            found = (answer.data or {}).get("channels") if answer.kind == "yes" else None
            if not isinstance(found, list):
                said = answer.said or "no clear answer"
                raise Unavailable(f"Buffer could not list its channels ({said}). Nothing was "
                                  "changed. Try again later.")
            out += [c for c in channels_listed(found)
                    if c["account_id"] not in {o["account_id"] for o in out}]
        return out

    def recent(self, account_ids: list[str], limit: int) -> list[dict[str, Any]]:
        """The newest posts that went out on these channels, newest first, as ``{text,
        when_ms, account_id}``: at most ``limit``. A failure of the first request is
        ``Unavailable``; a later page failing leaves what was read."""
        key = self._key()
        if key is None:
            raise Unavailable(NO_KEY_READ)
        channels = [str(a) for a in account_ids if str(a).strip()]
        if not channels:
            raise Unavailable("There is no Buffer channel to read posts from. Add one first.")
        wanted = set(channels)
        out: list[dict[str, Any]] = []
        first = True
        for organization in self._organizations(key):
            after: str | None = None
            for _ in range(RECENT_MOST_PAGES):
                answer = self._ask(key, "posts", sent_query(organization, channels, after))
                found = (answer.data or {}).get("posts") if answer.kind == "yes" else None
                if not isinstance(found, dict):
                    if first:
                        said = answer.said or "no clear answer"
                        raise Unavailable(f"Buffer could not list its sent posts ({said}). "
                                          "Nothing was changed. Try again later.")
                    break
                first = False
                out += sent_read(found, wanted)
                info = found.get("pageInfo")
                after = info.get("endCursor") if isinstance(info, dict) else None
                if len(out) >= limit or not (isinstance(info, dict)
                                             and info.get("hasNextPage") and after):
                    break
        out.sort(key=lambda post: post["when_ms"] or 0, reverse=True)
        return out[:max(0, int(limit))]
