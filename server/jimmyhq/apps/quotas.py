"""How much an app may use: every limit is a setting, and every count survives a restart.

There are two sorts of limit:

- a **rate**: so many in a window of time (requests to JimmyHQ a second, model calls an hour).
  ``take`` counts one and refuses the one that would go over; ``add`` records an amount
  only known afterwards (tokens); ``ensure_room`` refuses once a window is already spent.
- a **level**: so much held at once (megabytes stored, records, open cards). The code
  that holds the thing reads ``limit`` and compares.

    quotas.take(core, "water", "calls_per_second")       # QuotaExceeded when over
    quotas.limit(core, "water", "storage_mb")            # 50, or this app's override

Importing this module declares every setting the bridge reads, on the settings page ``apps``.
Each limit is the setting ``apps.quota.<name>``. The owner overrides one for one app with
a line in ``apps.quota_overrides`` such as ``water.model_calls_per_hour=100``.

Counts are rows in ``<data>/apps/apps.db`` (table ``quota_counts``), written before the
call they count is carried out, so stopping and starting JimmyHQ never hands an app a
fresh allowance. A window is a fixed slice of the clock (unix milliseconds, UTC), not a
sliding one: ``retry_after_s`` is the time until the slice ends.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.apps import dbfile

# The section "apps" is declared there; importing it first means these settings always
# have a home, whichever of the two modules a process imports first.
from jimmyhq.apps import settings as _section  # noqa: F401
from jimmyhq.ids import now_ms
from jimmyhq.settings import Setting, SettingError, declare

if TYPE_CHECKING:
    from jimmyhq.core import Core

SECTION = "apps_limits"
OVERRIDES = "apps.quota_overrides"
LOCAL_ONLY = "apps.sandboxed_local_models_only"
FETCH_MAX_KB = "apps.net_fetch_max_kb"
FETCH_TIMEOUT_S = "apps.net_fetch_timeout_s"
FETCH_DEFAULT_KB = "apps.net_fetch_default_kb"
FETCH_DEFAULT_S = "apps.net_fetch_default_s"
WEB_READ_KEEP = "apps.web_read_keep_minutes"
_APP_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}\Z")
_OVERRIDE = re.compile(r"^([a-z][a-z0-9_]{1,30})\.([a-z_]+)=(\d{1,12})\Z")


@dataclass(frozen=True)
class Quota:
    name: str
    default: int
    label: str
    help: str
    window_s: int | None = None  # None for a level
    what: str = ""  # "bridge calls", for the refusal sentence
    span: str = ""  # "a second"
    most: int = 1_000_000_000
    least: int = 0
    part: str = "apps_limits"  # the headed part of the Apps settings page it is shown in
    advanced: bool = False  # kept behind "Show advanced settings"

    @property
    def key(self) -> str:
        return f"apps.quota.{self.name}"


QUOTAS: tuple[Quota, ...] = (
    Quota("storage_mb", 50, "Storage for each app (MB)",
          "The most one app may keep in its own storage, keys and records together.",
          most=100_000, part="apps_storage"),
    Quota("value_kb", 256, "Largest single value (KB)",
          "The most one stored value, one record or one request from an app may weigh.",
          most=100_000, part="apps_storage", advanced=True),
    Quota("records", 20_000, "Records for each app",
          "The most records one app may keep across all its tables.",
          part="apps_storage", advanced=True),
    Quota("table_records", 50_000, "Records in one table",
          "The most records one table of an app may hold.", least=1,
          part="apps_storage", advanced=True),
    Quota("record_kb", 64, "Largest single record (KB)",
          "The most one record may weigh, all its fields together.", most=100_000, least=1,
          part="apps_storage", advanced=True),
    Quota("record_text_chars", 2_000, "Longest text in a record (characters)",
          "The most characters one text field of a record may hold.", most=1_000_000,
          least=1, part="apps_storage", advanced=True),
    Quota("open_cards", 10, "Open Bench cards for each app",
          "The most cards one app may have waiting on your Bench at once."),
    Quota("calls_per_second", 20, "Requests to JimmyHQ a second",
          "How many requests one app may make of JimmyHQ in a second.",
          window_s=1, what="requests to JimmyHQ", span="a second", advanced=True),
    Quota("calls_per_minute", 600, "Requests to JimmyHQ a minute",
          "How many requests one app may make of JimmyHQ in a minute.",
          window_s=60, what="requests to JimmyHQ", span="a minute", advanced=True),
    Quota("calls_per_day", 50_000, "Requests to JimmyHQ a day",
          "How many requests one app may make of JimmyHQ in a day.",
          window_s=86_400, what="requests to JimmyHQ", span="a day", advanced=True),
    Quota("model_calls_per_hour", 30, "Model calls an hour",
          "How many times one app may ask your AI models in an hour.",
          window_s=3_600, what="model calls", span="an hour"),
    Quota("model_calls_per_day", 200, "Model calls a day",
          "How many times one app may ask your AI models in a day.",
          window_s=86_400, what="model calls", span="a day"),
    Quota("model_tokens_per_day", 200_000, "Model tokens a day",
          "How many tokens, asked and answered together, one app's model calls may use in "
          "a day.", window_s=86_400, what="model tokens", span="a day"),
    Quota("fetches_per_hour", 60, "Network requests an hour",
          "How many requests one app may send to the hosts you allowed it, in an hour.",
          window_s=3_600, what="network requests", span="an hour"),
    Quota("tasks_per_day", 50, "New tasks a day",
          "How many tasks one app may add to your list in a day.",
          window_s=86_400, what="new tasks", span="a day"),
    # Similarity (model.embed), pictures for a model and the web (web.search, web.read).
    Quota("embed_calls_per_hour", 120, "Similarity calls an hour",
          "How many times one app may ask your embeddings model to compare texts, in an "
          "hour.", window_s=3_600, what="similarity calls", span="an hour"),
    Quota("embed_texts", 64, "Texts in one similarity call",
          "The most texts one app may send in one similarity call.", most=4_096, least=1,
          advanced=True),
    Quota("embed_text_chars", 8_000, "Longest text in a similarity call (characters)",
          "The most characters one text in a similarity call may hold.", most=200_000,
          least=1, advanced=True),
    Quota("images_per_ask", 8, "Pictures in one model call",
          "The most pictures from its own data one app may show a model in one call.",
          most=100, advanced=True),
    Quota("web_searches_per_day", 50, "Web searches a day",
          "How many web searches one app may make in a day, through your web lookup "
          "settings. What it searches for goes to your search provider.",
          window_s=86_400, what="web searches", span="a day"),
    Quota("web_reads_per_day", 200, "Web pages read a day",
          "How many public web pages one app may read in a day, through your web lookup "
          "settings.", window_s=86_400, what="web pages read", span="a day"),
    # Feeds between apps (feed.read).
    Quota("feed_reads_per_day", 1_000, "Feed reads a day",
          "How many times one app may read another app's feed in a day.",
          window_s=86_400, what="feed reads", span="a day"),
    Quota("feed_items_per_read", 100, "Items in one feed read",
          "The most items one read of another app's feed hands over.", most=500, least=1,
          advanced=True),
)
BY_NAME: dict[str, Quota] = {q.name: q for q in QUOTAS}


class QuotaExceeded(Exception):
    """An app is over a limit. ``message`` says the number and what to do;
    ``retry_after_s`` is when asking again can work (None for a level)."""

    def __init__(self, message: str, retry_after_s: int | None = None, *, name: str = ""):
        super().__init__(message)
        self.message = message
        self.retry_after_s = retry_after_s
        self.name = name


# --- settings -------------------------------------------------------------------------------


def parse_override(line: Any) -> tuple[str, str, int]:
    """One override line as (app id, quota name, number), or ``SettingError``."""
    match = _OVERRIDE.match(line.strip()) if isinstance(line, str) else None
    example = "water.model_calls_per_hour=100"
    if match is None:
        raise SettingError(
            f"Each override is an app's id, a dot, a limit's name, an equals sign and a "
            f"whole number, such as {example}. Nothing was saved.")
    app_id, name, number = match.group(1), match.group(2), int(match.group(3))
    quota = BY_NAME.get(name)
    if quota is None:
        raise SettingError(
            f"There is no limit called \"{name}\", so nothing was saved. The limits are: "
            f"{', '.join(BY_NAME)}.")
    if number > quota.most:
        raise SettingError(
            f"\"{name}\" cannot be more than {quota.most}, so nothing was saved. Write a "
            "smaller number.")
    return app_id, name, number


def _check_overrides(value: list[str]) -> list[str]:
    seen: dict[tuple[str, str], str] = {}
    for line in value:
        app_id, name, number = parse_override(line)
        if (app_id, name) in seen:
            raise SettingError(
                f"\"{app_id}.{name}\" is listed twice, so nothing was saved. Keep one line "
                "for it.")
        seen[(app_id, name)] = f"{app_id}.{name}={number}"
    return list(seen.values())


def _declare() -> None:
    for quota in QUOTAS:
        declare(Setting(key=quota.key, section=quota.part, type="int", default=quota.default,
                        min=quota.least, max=quota.most, advanced=quota.advanced,
                        label=quota.label, help=quota.help))
    declare(Setting(
        key=OVERRIDES, section=SECTION, type="list", default=[], unique=True,
        validate=_check_overrides, label="Limits for one app",
        help="A different limit for one app, one to a line, written as the app's id, a dot, "
             "the limit's name, an equals sign and the number: "
             "water.model_calls_per_hour=100. The names are: " + ", ".join(BY_NAME) + "."))

    declare(Setting(
        key=LOCAL_ONLY, section=SECTION, type="bool", default=False,
        label="Sandboxed apps use local models only",
        help="When on, an app that asks your AI models is only ever answered by a model on "
             "this computer or your own network, whatever your roles say. When off, apps "
             "follow your roles, and a role that uses a hosted model sends what the app asks "
             "to that provider."))
    declare(Setting(
        key=FETCH_MAX_KB, section=SECTION, type="int", default=100_000, min=1, max=100_000,
        advanced=True,
        label="Largest network answer for an app (KB)",
        help="The ceiling: the most one answer from a host may weigh when JimmyHQ fetches it "
             "for an app, whatever the app says it expects. A host an app says it expects a "
             "large answer from gets that much, up to this; any other host gets the next "
             "setting. A larger answer is refused, not cut short."))
    declare(Setting(
        key=FETCH_TIMEOUT_S, section=SECTION, type="int", default=300, min=1, max=300,
        advanced=True,
        label="Longest wait for an app's network request (seconds)",
        help="The ceiling: how long JimmyHQ waits for a host to answer a request it makes for "
             "an app, whatever the app says it expects. A host an app says it expects to wait "
             "for gets that long, up to this; any other host gets the next setting."))
    declare(Setting(
        key=FETCH_DEFAULT_KB, section=SECTION, type="int", default=1024, min=1, max=100_000,
        advanced=True,
        label="Network answer size for a host an app says nothing about (KB)",
        help="The most one answer may weigh from a host the app's manifest does not say what "
             "to expect from. Never more than the ceiling above."))
    declare(Setting(
        key=FETCH_DEFAULT_S, section=SECTION, type="int", default=20, min=1, max=300,
        advanced=True,
        label="Wait for a host an app says nothing about (seconds)",
        help="How long to wait for a host the app's manifest does not say what to expect "
             "from. Never more than the ceiling above."))
    declare(Setting(
        key=WEB_READ_KEEP, section=SECTION, type="int", default=10, min=0, max=1440,
        advanced=True,
        label="How long a web page an app read is kept (minutes)",
        help="An app reads a long page a piece at a time. For this long after it first reads "
             "a page, the next pieces come from the copy JimmyHQ kept in memory, so the page "
             "is not fetched again. 0 fetches it every time."))


_declare()


def limit(core: Core, app_id: str, name: str) -> int:
    """The limit that applies to this app: its own override if the owner wrote one,
    otherwise the setting."""
    quota = BY_NAME[name]
    for line in core.settings.get(OVERRIDES):
        try:
            who, which, number = parse_override(line)
        except SettingError:
            continue
        if who == app_id and which == name:
            return number
    return int(core.settings.get(quota.key))


def limits(core: Core, app_id: str) -> dict[str, int]:
    """Every limit as it applies to this app."""
    return {q.name: limit(core, app_id, q.name) for q in QUOTAS}


# --- the refusal sentences ------------------------------------------------------------------


def wait_words(seconds: int) -> str:
    """A wait in the largest unit that reads naturally: "1 second", "12 minutes"."""
    for size, unit in ((3600, "hour"), (60, "minute"), (1, "second")):
        if seconds >= size:
            n = math.ceil(seconds / size)
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return "1 second"


def rate_sentence(quota: Quota, most: int, retry_after_s: int) -> str:
    """What an app's author reads when a rate is spent."""
    if most <= 0:
        return (f"You may not make any {quota.what}: the owner has set \"{quota.label}\" to "
                f"0 for this app. Nothing was done. Ask them to raise it under Settings, "
                "Apps.")
    return (f"You have used all {most} {quota.what} this app may make in {quota.span}. "
            f"Nothing was done. Try again in {wait_words(retry_after_s)}, or ask the owner "
            f"to raise \"{quota.label}\" under Settings, Apps.")


# --- counting -------------------------------------------------------------------------------


def state_db(core: Core) -> Path:
    """The apps module's working database, where the counts are kept."""
    return Path(core.config.data) / "apps" / "apps.db"


class Counters:
    """The counts for every app, kept in SQLite so a restart loses nothing."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        storeversion.prepare(self._db, dbfile.STORE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _used(self, app_id: str, name: str, start: int) -> int:
        row = self._db.execute(
            "SELECT window_start, used FROM quota_counts WHERE app_id=? AND name=?",
            (app_id, name)).fetchone()
        return row[1] if row and row[0] == start else 0

    def _store(self, app_id: str, name: str, start: int, used: int) -> None:
        self._db.execute(
            "INSERT INTO quota_counts (app_id, name, window_start, used) VALUES (?,?,?,?) "
            "ON CONFLICT (app_id, name) DO UPDATE SET window_start=excluded.window_start, "
            "used=excluded.used", (app_id, name, start, used))

    def used(self, app_id: str, name: str, window_s: int, now: int) -> int:
        with self._lock:
            return self._used(app_id, name, _start(now, window_s))

    def take(self, app_id: str, name: str, window_s: int, most: int, amount: int,
             now: int) -> int | None:
        """Count ``amount`` if it fits and answer None, or count nothing and answer the
        seconds until the window ends."""
        start = _start(now, window_s)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                used = self._used(app_id, name, start)
                if used + amount > most:
                    self._db.execute("ROLLBACK")
                    return _wait(now, start, window_s)
                self._store(app_id, name, start, used + amount)
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
        return None

    def add(self, app_id: str, name: str, window_s: int, amount: int, now: int) -> int:
        """Count an amount whatever the limit, and answer the new total."""
        start = _start(now, window_s)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                total = self._used(app_id, name, start) + amount
                self._store(app_id, name, start, total)
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
        return total

    def forget(self, app_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM quota_counts WHERE app_id=?", (app_id,))


def _start(now: int, window_s: int) -> int:
    size = window_s * 1000
    return now - now % size


def _wait(now: int, start: int, window_s: int) -> int:
    return max(1, math.ceil((start + window_s * 1000 - now) / 1000))


def counters(core: Core) -> Counters:
    return core.service("apps.quotas", lambda c: Counters(state_db(c)))


def _rate(name: str) -> Quota:
    quota = BY_NAME[name]
    if quota.window_s is None:
        raise ValueError(f"{name} is a level, not a rate")
    return quota


def _checked(app_id: str) -> str:
    if not isinstance(app_id, str) or not _APP_ID.match(app_id):
        raise ValueError("not an app id")
    return app_id


def take(core: Core, app_id: str, name: str, amount: int = 1, *, now: int | None = None) -> None:
    """Count one use of a rate, or raise ``QuotaExceeded`` and count nothing."""
    quota, most = _rate(name), limit(core, _checked(app_id), name)
    wait = counters(core).take(app_id, name, quota.window_s or 1, most, amount,
                               now_ms() if now is None else now)
    if wait is not None:
        raise QuotaExceeded(rate_sentence(quota, most, wait), wait, name=name)


def ensure_room(core: Core, app_id: str, name: str, *, now: int | None = None) -> None:
    """Raise ``QuotaExceeded`` if this window's allowance is already spent."""
    quota, most = _rate(name), limit(core, _checked(app_id), name)
    now = now_ms() if now is None else now
    window = quota.window_s or 1
    if counters(core).used(app_id, name, window, now) >= most:
        wait = _wait(now, _start(now, window), window)
        raise QuotaExceeded(rate_sentence(quota, most, wait), wait, name=name)


def add(core: Core, app_id: str, name: str, amount: int, *, now: int | None = None) -> int:
    """Record an amount only known after the fact (tokens). Never refuses."""
    quota = _rate(name)
    return counters(core).add(_checked(app_id), name, quota.window_s or 1, max(0, int(amount)),
                              now_ms() if now is None else now)


def used(core: Core, app_id: str, name: str, *, now: int | None = None) -> int:
    quota = _rate(name)
    return counters(core).used(_checked(app_id), name, quota.window_s or 1,
                               now_ms() if now is None else now)


def usage(core: Core, app_id: str) -> dict[str, dict[str, int]]:
    """Each rate's count and limit in the current window, for the Apps screen."""
    return {q.name: {"used": used(core, app_id, q.name), "limit": limit(core, app_id, q.name)}
            for q in QUOTAS if q.window_s is not None}
