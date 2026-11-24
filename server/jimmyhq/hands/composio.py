"""Composio as a provider of hands.

Composio holds the owner's sign-ins to many outside services (mail, calendars, chat) and
runs one named tool at a time on a connected account. This adapter is a thin client of its
REST API and nothing more: it lists what an account can do, starts a connection, and
executes exactly one tool with exactly the arguments it is given. It never lets a model
choose tools (Composio's sessions and tool router are not used), never logs an argument or
a result, and never puts the key in a message.

What it speaks (https://docs.composio.dev/reference.md, base ``/api/v3.1``, header
``x-api-key``):

====================================  ===================================================
``GET  /connected_accounts``          the active accounts of this install's user
``GET  /auth_configs``                the sign-in set-up for an app
``POST /auth_configs``                make a Composio-managed one when there is none
``POST /connected_accounts/link``     start a connection; answers the consent page
``GET  /tools``                       what an app offers, with input schemas and hints
``POST /tools/execute/{slug}``        run one tool on one connected account
====================================  ===================================================

Facts the documentation does not settle, and what is done about each:

* Where a tool carries its hints. They are read from the tool's ``tags`` (``readOnlyHint``,
  ``createHint``, ``updateHint``, ``destructiveHint``). A tool with none is judged by the
  verbs in its name, and a tool with neither is "change, guessed".
* Whether ``user_id`` is needed to execute. It is sent with the connected account's id, and
  connections are made under the same ``user_id`` (``options.user_id``).
* Whether an execution can be pinned to the tool version the owner reviewed. It cannot be
  through the arguments this adapter is given, so it runs the latest; the owner's review is
  guarded by ``Action.version``, which changes with the tool's version, description,
  schema and tags.
* Idempotency: the API documents no key, so ``key`` is not sent. The core's run ledger is
  what stops a retry.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from jimmyhq.hands.provider import (
    Account,
    Action,
    Connect,
    Effect,
    Health,
    Outcome,
    ProviderRecord,
    ProviderType,
    RecordField,
    Wiring,
    register_type,
    unregister_type,
)
from jimmyhq.hands.rules import Invalid
from jimmyhq.hands.service import NotReady

TYPE = "composio"
BASE_URL = "https://backend.composio.dev/api/v3.1"
DEFAULT_USER = "jimmyhq"
PAGE_SIZE = 100
TOOLS_PAGE_SIZE = 200
MAX_PAGES = 30
CONNECT_TIMEOUT_S = 10.0
DESCRIPTION_CHARS = 600
MESSAGE_CHARS = 240
APP = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TOOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")

# --- effect ------------------------------------------------------------------------------

# Composio's own hints, best effort by its own account. Each names one kind of effect.
HINTS: dict[str, Effect] = {
    "readonlyhint": "read", "createhint": "create", "updatehint": "update",
    "destructivehint": "delete",
}

# The adapter's table: the verbs in a tool's name, by what they do. A name is split into
# words, so ``GMAIL_LIST_FORWARDING_ADDRESSES`` has the words list, forwarding and
# addresses, and only ``list`` is a verb here. Deliberately small and cautious: a word
# that is often a noun is left out.
VERBS: dict[str, tuple[str, ...]] = {
    "spend": ("pay", "charge", "refund", "purchase", "buy", "transfer", "withdraw",
              "donate", "payout", "checkout"),
    "delete": ("delete", "remove", "destroy", "drop", "purge", "trash", "clear", "erase",
               "revoke", "unsubscribe", "unlink", "disconnect"),
    "send": ("send", "reply", "forward", "post", "publish", "broadcast", "notify", "tweet"),
    "access": ("grant", "share", "invite", "authorize", "authorise", "permit", "unshare"),
    "create": ("create", "insert", "upload", "append", "import", "make", "duplicate", "copy",
               "clone", "schedule", "register", "add", "write", "submit"),
    "update": ("update", "edit", "modify", "patch", "set", "rename", "move", "archive",
               "unarchive", "restore", "mark", "replace", "merge", "assign", "reschedule",
               "complete", "close", "reopen", "snooze", "put"),
    "change": ("run", "execute", "trigger", "start", "stop", "pause", "resume", "sync",
               "refresh", "cancel", "enable", "disable", "toggle", "reset", "restart",
               "retry", "launch", "deploy", "apply", "approve", "reject", "accept",
               "decline", "respond", "rsvp", "vote", "like", "follow", "unfollow", "join",
               "leave", "block", "unblock", "convert"),
    "read": ("get", "list", "fetch", "search", "find", "read", "retrieve", "query", "lookup",
             "view", "count", "describe", "download", "show", "browse"),
}
# The more cautious a verb's effect, the earlier it is. A name with two verbs gets the first.
SEVERITY = ("spend", "delete", "send", "access", "create", "update", "change", "read")
# A hint that says create or update gives way to a verb that says one of these, because
# Composio has no hint for them and they matter more.
OVERRIDES = ("spend", "delete", "send", "access")

RECIPIENTS = frozenset({
    "to", "cc", "bcc", "recipient", "recipients", "recipient_email", "recipient_emails",
    "to_email", "to_emails", "to_address", "to_addresses", "attendees", "invitees",
})
AMOUNTS = frozenset({
    "amount", "amount_cents", "amount_in_cents", "amount_minor", "total_amount",
    "payment_amount", "price",
})


def words(slug: str, toolkit: str = "") -> list[str]:
    """The lower-case words of a tool's name, without the app's own name in front."""
    text = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")
    head = re.sub(r"[^a-z0-9]+", "_", toolkit.lower()).strip("_")
    if head and text.startswith(head + "_"):
        text = text[len(head) + 1:]
    return [w for w in text.split("_") if w]


def verb_effect(slug: str, toolkit: str = "") -> str | None:
    """Rule ``composio_verbs_in_a_name``: the effect the verbs in a tool's name point to,
    or None when they point to nothing known. A name counts as a reading only if it starts
    with a reading verb and has no other verb in it; any other verb wins, the most
    cautious first."""
    found = words(slug, toolkit)
    kinds = {kind for w in found for kind, verbs in VERBS.items() if w in verbs}
    for kind in SEVERITY[:-1]:
        if kind in kinds:
            return kind
    if found and found[0] in VERBS["read"]:
        return "read"
    return None


def hint_effect(tags: Any) -> str | None:
    """Rule ``composio_hints_are_read_from_tags``: the effect Composio's hints give, or
    None when it gave none. A tool marked read only and also marked as creating, updating
    or deleting is not a reading: the hints disagree, so the cautious ones are kept."""
    if not isinstance(tags, list):
        return None
    named = {HINTS[t.lower()] for t in tags if isinstance(t, str) and t.lower() in HINTS}
    for kind in ("delete", "create", "update"):
        if kind in named:
            return kind
    return "read" if "read" in named else None


def decide_effect(slug: str, tags: Any, toolkit: str = "") -> tuple[Effect, str]:
    """Rule ``composio_effect_is_never_a_guessed_read``: what a tool does, and where that
    came from: the provider's hints, the adapter's table of verbs, or a guess.

    * a hint and no conflicting verb: the hint (``provider``);
    * a read hint but a verb that says otherwise: the verb (``adapter``);
    * a create or update hint but a verb for sending, deleting, spending or sharing: the
      verb (``adapter``), because Composio has no hint for those;
    * no hint: the verb (``adapter``);
    * neither: ``change`` (``guess``). Nothing is ever a read unless something said so."""
    hinted, verb = hint_effect(tags), verb_effect(slug, toolkit)
    if hinted == "read":
        if verb is None or verb == "read":
            return "read", "provider"
        return verb, "adapter"  # type: ignore[return-value]
    if hinted is not None:
        if verb in OVERRIDES and verb != hinted:
            return verb, "adapter"  # type: ignore[return-value]
        return hinted, "provider"  # type: ignore[return-value]
    if verb is not None:
        return verb, "adapter"  # type: ignore[return-value]
    return "change", "guess"


def _numeric(schema: Any) -> bool:
    kind = schema.get("type") if isinstance(schema, dict) else None
    kinds = kind if isinstance(kind, list) else [kind]
    return any(k in ("number", "integer") for k in kinds)


def clear_arguments(schema: dict[str, Any]) -> tuple[tuple[str, ...], str | None]:
    """Rule ``composio_clear_arguments_only``: which arguments name who receives it, and
    which carries money, when the schema makes that clear. An amount is named only when
    exactly one numeric argument has an amount's name."""
    props = schema.get("properties")
    props = props if isinstance(props, dict) else {}
    who = tuple(n for n in props if isinstance(n, str) and n.lower() in RECIPIENTS)
    amounts = [n for n, s in props.items()
               if isinstance(n, str) and n.lower() in AMOUNTS and _numeric(s)]
    return who, amounts[0] if len(amounts) == 1 else None


def fingerprint(tool: dict[str, Any]) -> str:
    """Rule ``composio_version_changes_with_the_tool``: what the owner reviewed, as one
    string. It changes when the tool's version, description, schema or hints change."""
    seen = {k: tool.get(k) for k in ("slug", "description", "input_parameters", "tags")}
    digest = hashlib.sha256(json.dumps(seen, sort_keys=True, default=str).encode()).hexdigest()
    version = tool.get("version")
    return f"{version}:{digest[:12]}" if isinstance(version, str) and version else digest[:12]


def to_action(tool: dict[str, Any], toolkit: str = "") -> Action | None:
    """One Composio tool as an ``Action``, or None for one that cannot be used."""
    slug = tool.get("slug")
    if not isinstance(slug, str) or not TOOL.match(slug) or tool.get("is_deprecated"):
        return None
    schema = tool.get("input_parameters")
    schema = dict(schema) if isinstance(schema, dict) else {}
    schema.setdefault("type", "object")
    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    effect, source = decide_effect(slug, tool.get("tags"), toolkit)
    who, money = clear_arguments(schema) if effect != "read" else ((), None)
    said = " ".join(str(tool.get("human_description") or tool.get("description") or "").split())
    if len(said) > DESCRIPTION_CHARS:
        said = said[:DESCRIPTION_CHARS].rstrip() + "..."
    name = tool.get("name")
    return Action(name=slug, label=str(name) if name else slug, description=said,
                  arguments=schema, effect=effect, effect_from=source, recipients=who,
                  amount=money, undo=False, version=fingerprint(tool))


# --- the wire ----------------------------------------------------------------------------


class Said(Exception):  # noqa: N818 - a sentence for the owner, not a fault
    """Something Composio said, as a sentence that tells the owner what to do."""

    def __init__(self, kind: str, sentence: str):
        super().__init__(sentence)
        self.kind = kind
        self.sentence = sentence


class Unsure(Exception):  # noqa: N818 - not knowing is the point
    """The request may have reached Composio and what became of it is not known. The core
    records a change as "outcome unknown" and never retries it."""


def _trim(value: Any, key: str | None) -> str:
    text = " ".join(str(value or "").split())
    if key:
        text = text.replace(key, "[key]")
    return text[:MESSAGE_CHARS]


def provider_message(response: httpx.Response, key: str | None = None) -> str:
    """The reason Composio gave, trimmed and with the key taken out if it was echoed."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return _trim(error.get("message") or error.get("suggested_fix"), key)
        return _trim(error or body.get("message"), key)
    return ""


def wait_of(response: httpx.Response) -> int | None:
    """Seconds Composio asked us to wait, if it said so as a number."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return max(1, min(3600, round(seconds))) if seconds > 0 else None


def _wait_sentence(seconds: int | None) -> str:
    if seconds is None:
        return "Composio is limiting how fast this key may ask. Wait a little and try again."
    return (f"Composio is limiting how fast this key may ask. Wait {seconds} "
            f"second{'s' if seconds != 1 else ''} and try again.")


EXPIRED_WORDS = ("expired", "inactive", "reauth", "re-auth", "reconnect", "invalid_grant",
                 "refresh token", "revoked", "not active", "initiated", "initializing",
                 "unauthorized", "unauthorised")
LOGIN_ADVICE = "Connect the account again under Settings, Integrations."


def said_for(response: httpx.Response, key: str | None, *, running: bool = False) -> Said:
    """Rule ``composio_errors_say_what_to_do``: a refusal from Composio as a sentence
    that says what to do. ``running`` is true for the execution of a tool."""
    status, why = response.status_code, provider_message(response, key)
    low = why.lower()
    detail = f" Composio said: {why}" if why else ""
    if status in (401, 403):
        return Said("auth", "Composio refused the key. Check the key saved for this provider "
                            "under Settings, Integrations.")
    if status == 402:
        return Said("billing", "Composio says the account has no balance left. Check billing "
                               "with Composio, then try again.")
    if status == 429:
        return Said("rate_limited", _wait_sentence(wait_of(response)))
    if status == 410:
        return Said("gone", "Composio no longer offers that tool. Refresh the list of "
                            "actions and switch on a replacement." + detail)
    if status == 404 and running:
        account, tool = "account" in low, "tool" in low
        if account and not tool:
            return Said("not_connected", "Composio does not know this connected account any "
                                         "more, so it is not connected. " + LOGIN_ADVICE)
        if tool and not account:
            return Said("unknown_tool", "Composio does not know that tool. The service may "
                                        "have changed it: refresh the list of actions.")
        return Said("unknown_tool", "Composio does not know that tool or this account. "
                                    "Refresh the list of actions; if that does not help, "
                                    + LOGIN_ADVICE[0].lower() + LOGIN_ADVICE[1:])
    if status == 404:
        return Said("not_found", "Composio could not find what was asked for. Check the "
                                 "app's name and try again." + detail)
    if status == 422 or (status == 400 and any(w in low for w in EXPIRED_WORDS)):
        return Said("expired", "The connection to this account has expired or is not "
                               "active. " + LOGIN_ADVICE)
    if status == 501:
        return Said("unsupported", "Composio does not support that." + detail)
    if 400 <= status < 500:
        if running:
            return Said("bad_arguments", "Composio did not accept those arguments. Check "
                                         "them against the action's list of arguments and "
                                         "try again." + detail)
        return Said("bad_request", "Composio did not accept the request." + detail)
    if 300 <= status < 400:
        return Said("redirected", "Composio answered with a redirect, which is not "
                                  "followed. Check the provider's address.")
    return Said("unavailable", "Composio is having trouble right now. Try again shortly.")


def said_for_failure(text: str) -> str:
    """A tool that ran and said no: its own words, with advice added where the words say
    the sign-in is the problem or the service asked us to slow down."""
    low = text.lower()
    if any(w in low for w in EXPIRED_WORDS):
        return f"{text} {LOGIN_ADVICE}"
    if "rate limit" in low or "too many requests" in low or "429" in low:
        return f"{text} The service is limiting requests: wait a little and try again."
    return text


class Wire:
    """One key, one base address, one client. Nothing here keeps state between calls."""

    def __init__(self, base: str, key: str, http: Any, timeouts: httpx.Timeout):
        self.base, self.key, self.http, self.timeouts = base.rstrip("/"), key, http, timeouts

    async def send(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                   body: dict[str, Any] | None = None) -> httpx.Response:
        """One request. Raises ``Said`` when it certainly did not leave, ``Unsure`` when it
        may have."""
        headers = {"x-api-key": self.key, "accept": "application/json"}
        url = f"{self.base}{path}"
        try:
            if isinstance(self.http, httpx.AsyncClient):
                return await self.http.request(method, url, params=params, json=body,
                                               headers=headers, timeout=self.timeouts,
                                               follow_redirects=False)
            async with httpx.AsyncClient(transport=self.http, timeout=self.timeouts,
                                         follow_redirects=False) as client:
                return await client.request(method, url, params=params, json=body,
                                            headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.InvalidURL,
                httpx.UnsupportedProtocol) as exc:
            raise Said("unreachable", "Composio could not be reached. Check this machine's "
                                      "connection and the provider's address.") from exc
        except httpx.TimeoutException as exc:
            raise Unsure("Composio did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise Unsure("the connection to Composio broke") from exc

    async def get_json(self, path: str, params: dict[str, Any] | None = None
                       ) -> dict[str, Any]:
        """A GET that must give an object, or ``Said`` why not."""
        try:
            response = await self.send("GET", path, params=params)
        except Unsure as exc:
            raise Said("unreachable", "Composio did not answer in time. Try again.") from exc
        if response.status_code != 200:
            raise said_for(response, self.key)
        return self.object(response)

    def object(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise Said("bad_answer", "Composio gave an answer that made no sense. Try again.")
        return body

    async def pages(self, path: str, params: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Every item of a listing, following ``next_cursor``."""
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            body = await self.get_json(path, params | ({"cursor": cursor} if cursor else {}))
            items = body.get("items")
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict):
                    yield item
            cursor = body.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                return
            seen.add(cursor)
        raise Said("too_long", "Composio's list is too long to read. Narrow it down in "
                               "Composio, then try again.")


# --- the adapter -------------------------------------------------------------------------


def _timeouts(options: dict[str, Any], wiring: Wiring) -> httpx.Timeout:
    """How long one request may wait: the provider's own ``timeout_s`` when it is a
    number from 1 to 300, else the owner's setting ``hands.composio_timeout_s``."""
    read = wiring.limit(options, "timeout_s", "composio_timeout_s", low=1, high=300)
    if read is None:
        read = float(wiring.setting("composio_timeout_s"))
    return httpx.Timeout(read, connect=min(CONNECT_TIMEOUT_S, read))


def _user(options: dict[str, Any]) -> str:
    user = options.get("user_id")
    return user.strip() if isinstance(user, str) and user.strip() else DEFAULT_USER


def _toolkit_of(item: dict[str, Any]) -> str | None:
    toolkit = item.get("toolkit")
    slug = toolkit.get("slug") if isinstance(toolkit, dict) else None
    return slug.lower() if isinstance(slug, str) and slug else None


def _label(item: dict[str, Any], toolkit: str | None) -> str:
    """A name that tells accounts apart without saying who they belong to: the owner's
    alias for it, else Composio's short identifier, else the app's name."""
    for field in ("alias", "word_id"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    return (toolkit or "account").replace("_", " ").title()[:80]


def _active(item: dict[str, Any]) -> bool:
    status = item.get("status")
    return (isinstance(status, str) and status.upper() == "ACTIVE"
            and not item.get("is_disabled"))


class ComposioProvider:
    """Speaks to Composio for the owner. A model only ever sees the tools the owner
    switched on, never this."""

    type = TYPE

    def __init__(self, record: ProviderRecord, wiring: Wiring):
        self.record = record
        self.user = _user(record.options)
        self.wire = Wire(record.base_url or BASE_URL, wiring.key or "", wiring.http,
                         _timeouts(record.options, wiring))

    # --- the owner's side ---------------------------------------------------------

    async def check(self) -> Health:
        try:
            await self.wire.get_json("/connected_accounts", {"limit": 1})
        except Said as said:
            return Health(False, said.sentence)
        return Health(True)

    async def connect(self, app: str | None) -> Connect:
        """Start a connection to one app (a Composio toolkit, such as ``gmail``). The
        owner's browser opens the page this returns; the account is found afterwards by
        ``accounts()``."""
        slug = (app or "").strip().lower()
        if not APP.match(slug):
            raise Invalid("Say which app to connect: its short name at Composio, in lower "
                          "case (for example gmail).")
        try:
            config = await self._auth_config(slug)
            body = await self._link(config)
        except Said as said:
            raise NotReady(said.kind, said.sentence) from None
        url = body.get("redirect_url")
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            raise NotReady("bad_answer", "Composio did not give a page to open. Try again.")
        return Connect("redirect", url=url)

    async def _auth_config(self, toolkit: str) -> str:
        """The id of the sign-in set-up for an app: one that is already enabled, else a
        new one that uses Composio's own app."""
        async for item in self.wire.pages(
                "/auth_configs", {"toolkit_slug": toolkit, "limit": PAGE_SIZE}):
            if isinstance(item.get("id"), str) and item.get("status", "ENABLED") == "ENABLED":
                return item["id"]
        try:
            response = await self.wire.send("POST", "/auth_configs", body={
                "toolkit": {"slug": toolkit},
                "auth_config": {"type": "use_composio_managed_auth"}})
        except Unsure:
            raise Said("unreachable", "Composio did not answer in time. Try again.") from None
        if response.status_code not in (200, 201):
            said = said_for(response, self.wire.key)
            if said.kind in ("bad_request", "not_found", "unsupported"):
                raise Said("no_managed_auth", f"Composio cannot sign in to \"{toolkit}\" for "
                                              "you. Set up that app in Composio first, or "
                                              "check its name.")
            raise said
        made = self.wire.object(response).get("auth_config")
        if not isinstance(made, dict) or not isinstance(made.get("id"), str):
            raise Said("bad_answer", "Composio gave an answer that made no sense. Try again.")
        return made["id"]

    async def _link(self, config: str) -> dict[str, Any]:
        try:
            response = await self.wire.send("POST", "/connected_accounts/link", body={
                "auth_config_id": config, "user_id": self.user})
        except Unsure:
            raise Said("unreachable", "Composio did not answer in time. Try again.") from None
        if response.status_code not in (200, 201):
            raise said_for(response, self.wire.key)
        return self.wire.object(response)

    async def accounts(self) -> list[Account]:
        """Active connected accounts made for this install's user."""
        found = []
        try:
            async for item in self.wire.pages("/connected_accounts", {
                    "statuses": "ACTIVE", "user_ids": self.user, "limit": PAGE_SIZE,
                    "order_by": "created_at", "order_direction": "asc"}):
                if not _active(item) or not isinstance(item.get("id"), str):
                    continue
                if isinstance(item.get("user_id"), str) and item["user_id"] != self.user:
                    continue
                toolkit = _toolkit_of(item)
                found.append(Account(id=item["id"], label=_label(item, toolkit), app=toolkit))
        except Said as said:
            raise NotReady(said.kind, said.sentence) from None
        return found

    async def actions(self, account: Account) -> list[Action]:
        """What the account's app offers, every page of it."""
        try:
            toolkit = await self._toolkit(account)
            tools = [to_action(t, toolkit) async for t in self.wire.pages(
                "/tools", {"toolkit_slug": toolkit, "limit": TOOLS_PAGE_SIZE})]
        except Said as said:
            raise NotReady(said.kind, said.sentence) from None
        seen: set[str] = set()
        out = []
        for action in tools:
            if action is not None and action.name not in seen:
                seen.add(action.name)
                out.append(action)
        return out

    async def _toolkit(self, account: Account) -> str:
        """The app this account is for, as Composio says (never as the owner typed it)."""
        body = await self.wire.get_json("/connected_accounts", {
            "connected_account_ids": account.id, "limit": 1})
        items = body.get("items")
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("id") == account.id:
                if not _active(item):
                    raise Said("expired", "This account is no longer active at Composio. "
                               + LOGIN_ADVICE)
                toolkit = _toolkit_of(item)
                if toolkit:
                    return toolkit
        raise Said("not_connected", "Composio does not know this connected account any "
                                    "more, so it is not connected. " + LOGIN_ADVICE)

    # --- the run ------------------------------------------------------------------

    async def run(self, account: Account, name: str, arguments: dict[str, Any], *,
                  key: str) -> Outcome:
        """Run one tool with exactly these arguments. ``key`` is not sent: Composio
        documents no idempotency key, so a repeat is prevented by the run ledger, not here.

        Answers ``ok=False`` when Composio certainly did not do it; raises when it may
        have, so the core can call the outcome unknown instead of retrying."""
        if not isinstance(name, str) or not TOOL.match(name):
            return Outcome(False, error="Composio does not know that tool. Refresh the list "
                                        "of actions.")
        try:
            response = await self.wire.send(
                "POST", f"/tools/execute/{quote(name, safe='')}",
                body={"connected_account_id": account.id, "user_id": self.user,
                      "arguments": arguments})
        except Said as said:
            return Outcome(False, error=said.sentence)
        status = response.status_code
        if status in (408, 500, 502, 503, 504):
            raise Unsure(f"Composio answered {status}")
        if status != 200:
            return Outcome(False, error=said_for(response, self.wire.key, running=True)
                           .sentence)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise Unsure("Composio's answer could not be read")
        ref = body.get("log_id") if isinstance(body.get("log_id"), str) else None
        said = body.get("error")
        if body.get("successful") is False or said:
            text = _trim(said, self.wire.key) or "it did not say why"
            return Outcome(False, ref=ref, error=said_for_failure(text))
        return Outcome(True, data=body.get("data"), ref=ref)


def make(record: ProviderRecord, wiring: Wiring) -> ComposioProvider:
    return ComposioProvider(record, wiring)


NOTE = ("Composio keeps the sign-in tokens for accounts connected through it. Its key is "
        "saved here; the tokens are not.")


def register() -> ProviderType:
    """Make the "composio" type available."""
    return register_type(ProviderType(
        TYPE, "Composio", make, needs_key=True, note=NOTE,
        description="A hosted service that connects accounts of many apps (mail, "
                    "calendars, documents) and runs their actions for you.",
        fields=(
            RecordField("base_url", "Address (optional)", where="record",
                        help="Only for a Composio of your own. Empty uses Composio's."),
            RecordField("user_id", "User id at Composio (optional)",
                        help="Whose connections these are at Composio. Empty uses "
                             f"\"{DEFAULT_USER}\". Only accounts made under it are listed."),
            RecordField("timeout_s", "Seconds to wait for one request (optional)",
                        kind="number", help="Empty uses the number under Settings, "
                                            "Integrations."),
        ),
        tokens="provider",
        tokens_note="Composio keeps the sign-in tokens of the accounts you connect "
                    "through it. JimmyHQ keeps only Composio's key.",
        # Composio calls its own hints best effort, and where it gives none the effect
        # is read from the tool's name. Neither is enough to start a reading at "alone".
        trusted_hints=False))


def unregister() -> None:
    unregister_type(TYPE)
