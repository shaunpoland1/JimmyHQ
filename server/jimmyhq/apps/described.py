"""A described app: records and views declared as data, kept and shown by JimmyHQ itself.

The manifest's ``tables`` say what a record holds; ``views`` say how to look at them. This
module turns that description into checked operations over the app's own record storage:

    app = Described(manifest.tables, manifest.views, store)
    app.create("book", {"title": "Dune", "stars": 5})   # -> a record
    app.list("book", view="all", limit=50)               # -> {"records": [...], "next": ...}
    app.handle("records.list", {"table": "book"})       # what the bridge calls

A described app can only keep and show records. There is no code in it and no network. The
one thing it computes is the arithmetic a ``computed`` field declares (``price * count``):
numbers, field names, ``+ - * /`` and brackets, read by the small parser below and never
by ``eval``. A computed field is worked out when a record is read and is never stored.

The storage is ``appdata.Records``, the app's own database (``RecordStore`` below names
what is needed of it). Records are never written to the Tape. Stored times are unix
milliseconds UTC.

One shape of record, whoever asks (an app's page through ``hq.records``, or JimmyHQ's own
screen for a described app)::

    {"id": 12, "label": "Dune", "values": {"title": "Dune", "stars": 5, "per_page": 3},
     "created": 1795000000000, "updated": 1795000000000}

``values`` holds every declared field, computed ones worked out and empty ones null.
The id and the two times sit beside the values, so a field may have any name.

Every refusal is a ``RecordError``: a ``code`` the bridge passes on (``invalid``,
``not_found``, ``too_large``, ``quota``), a sentence naming the field, and the words
"nothing was saved" so the person knows where they stand.
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import math
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

# --- limits ---------------------------------------------------------------------------------
# How much one record and one table may hold is the owner's to set (``Caps``: the bridge
# fills it from the settings under Settings, Apps). These three are the defaults.

MAX_TEXT = 2000  # characters in one text value
MAX_RECORD_BYTES = 64 * 1024  # one record as stored
MAX_RECORDS = 50_000  # records in one table
MAX_LABEL = 120  # characters of a record's label
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_SEARCH = 200
MAX_FILTER_RULES = 10
MAX_SORT_KEYS = 5
MAX_FORMULA_DEPTH = 16
MAX_SAFE = 2**53  # the largest whole number a browser keeps exactly

OPS = ("eq", "ne", "lt", "le", "gt", "ge", "contains", "empty", "not_empty")
TOTAL_KINDS = ("sum", "avg", "min", "max", "count")
CALLS = ("records.list", "records.get", "records.create", "records.update", "records.delete")
# Things every record has that are not fields: usable in a sort.
_PSEUDO = {"id": "int", "created": "int", "updated": "int"}


@dataclass(frozen=True)
class Caps:
    """The most one record and one table may hold."""

    text_chars: int = MAX_TEXT
    record_bytes: int = MAX_RECORD_BYTES
    table_records: int = MAX_RECORDS


class RecordError(ValueError):
    """A request was refused. ``code`` is one of the bridge's codes; the message says what
    to do instead and that nothing changed."""

    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


class DescribedError(ValueError):
    """The description cannot be run (a formula that does not read, for example)."""

    def __init__(self, problems: Iterable[str]):
        self.problems = tuple(problems)
        super().__init__(" ".join(self.problems))


# --- the storage seam -----------------------------------------------------------------------


@dataclass(frozen=True)
class Stored:
    """One record as the storage keeps it: its values under the field names written."""

    id: int
    data: dict[str, Any]
    created: int
    updated: int


class RecordStore(Protocol):
    """What a described app needs from its per-app record storage. Ids are whole numbers,
    given by the store, counted across all the app's tables and never used twice, so a
    record keeps its id when its table is renamed. Times are unix milliseconds UTC."""

    def get(self, table: str, record_id: int) -> Stored | None: ...

    def all(self, table: str) -> list[Stored]:
        """Every record of the table, oldest first."""
        ...

    def count(self, table: str) -> int: ...

    def insert(self, table: str, data: dict[str, Any], now: int) -> Stored: ...

    def replace(self, table: str, record_id: int, data: dict[str, Any], now: int
                ) -> Stored | None:
        """Overwrite the record's data; None if it is not there."""
        ...

    def delete(self, table: str, record_id: int) -> bool: ...

    def rename_table(self, old: str, new: str) -> int:
        """Move every record of ``old`` to ``new``; returns how many."""
        ...


class MemoryStore:
    """The whole seam in a dict. For checking an app's description without a database, and
    the reference the bridge's SQLite storage is tested against."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[int, Stored]] = {}
        self._last = 0

    def get(self, table: str, record_id: int) -> Stored | None:
        return self._rows.get(table, {}).get(record_id)

    def all(self, table: str) -> list[Stored]:
        return list(self._rows.get(table, {}).values())

    def count(self, table: str) -> int:
        return len(self._rows.get(table, {}))

    def insert(self, table: str, data: dict[str, Any], now: int) -> Stored:
        self._last += 1
        record_id = self._last
        row = Stored(record_id, json.loads(json.dumps(data)), now, now)
        self._rows.setdefault(table, {})[record_id] = row
        return row

    def replace(self, table: str, record_id: int, data: dict[str, Any], now: int
                ) -> Stored | None:
        old = self.get(table, record_id)
        if old is None:
            return None
        row = Stored(record_id, json.loads(json.dumps(data)), old.created, now)
        self._rows[table][record_id] = row
        return row

    def delete(self, table: str, record_id: int) -> bool:
        return self._rows.get(table, {}).pop(record_id, None) is not None

    def rename_table(self, old: str, new: str) -> int:
        moved = self._rows.pop(old, {})
        self._rows.setdefault(new, {}).update(moved)
        return len(moved)


# --- words ----------------------------------------------------------------------------------


def humanise(name: str) -> str:
    """``finished_on`` -> ``Finished on``: the label a field or table gets when it has none."""
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def field_label(name: str, spec: Mapping[str, Any]) -> str:
    return str(spec.get("label") or humanise(name))


def _clip(text: str, most: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= most else text[: most - 1].rstrip() + "…"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _shown(value: Any) -> str:
    """A value in a sentence: as JSON, so text has its quotes, short and printable."""
    text = json.dumps(value, ensure_ascii=True)
    return text if len(text) <= 40 else text[:39] + "…\""


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _bound_text(spec: Mapping[str, Any], *, whole: bool) -> str:
    low, high = spec.get("min"), spec.get("max")
    kind = "a whole number" if whole else "a number"
    if low is not None and high is not None:
        return f"{kind} from {low:g} to {high:g}"
    if low is not None:
        return f"{kind} of {low:g} or more"
    if high is not None:
        return f"{kind} of {high:g} or less"
    return kind


# --- one named rule per field type ----------------------------------------------------------
# Each takes the value, the field's label and its spec, and returns the value to keep or
# raises RecordError. "Nothing was saved." is added by ``_refuse``.


def _refuse(label: str, what: str, field: str) -> RecordError:
    return RecordError("invalid", f"\"{label}\" {what} Nothing was saved.", field)


def check_text(value: Any, label: str, field: str, most: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise _refuse(label, f"must be text, not {_shown(value)}.", field)
    if len(value) > most:
        raise _refuse(label, f"is {len(value)} characters long. Shorten it to {most} or "
                             "fewer.", field)
    for char in value:
        if char in "\n\t":
            continue
        if unicodedata.category(char).startswith("C"):
            raise _refuse(label, "has an invisible or control character in it. Remove it.",
                          field)
    return value


def _check_range(value: int | float, label: str, field: str, spec: Mapping[str, Any],
                 whole: bool) -> None:
    low, high = spec.get("min"), spec.get("max")
    if (low is not None and value < low) or (high is not None and value > high):
        raise _refuse(label, f"must be {_bound_text(spec, whole=whole)}, not {value:g}.",
                      field)


def check_int(value: Any, label: str, field: str, spec: Mapping[str, Any]) -> int:
    if isinstance(value, float) and value.is_integer() and abs(value) <= MAX_SAFE:
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _refuse(label, f"must be {_bound_text(spec, whole=True)}, not "
                             f"{_shown(value)}.", field)
    if abs(value) > MAX_SAFE:
        raise _refuse(label, "is too large. Use a number below 9007199254740992.", field)
    _check_range(value, label, field, spec, True)
    return value


def check_number(value: Any, label: str, field: str, spec: Mapping[str, Any]) -> int | float:
    if not _is_number(value) or (isinstance(value, float) and not math.isfinite(value)):
        raise _refuse(label, f"must be {_bound_text(spec, whole=False)}, not "
                             f"{_shown(value)}.", field)
    if abs(value) > MAX_SAFE:
        raise _refuse(label, "is too large. Use a number below 9007199254740992.", field)
    _check_range(value, label, field, spec, False)
    return value


def check_bool(value: Any, label: str, field: str) -> bool:
    if not isinstance(value, bool):
        raise _refuse(label, f"must be yes or no (true or false), not {_shown(value)}.", field)
    return value


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z", re.ASCII)


def check_date(value: Any, label: str, field: str) -> str:
    ok = isinstance(value, str) and _DATE.match(value) is not None
    if ok:
        try:
            date.fromisoformat(value)
        except ValueError:
            ok = False
    if not ok:
        raise _refuse(label, f"must be a real date written year-month-day, such as "
                             f"2030-01-31, not {_shown(value)}.", field)
    return value


def check_choice(value: Any, label: str, field: str, spec: Mapping[str, Any]) -> str:
    options = spec.get("options", [])
    if not isinstance(value, str) or value not in options:
        raise _refuse(label, f"must be one of: {', '.join(options)}. It is "
                             f"{_shown(value)}.", field)
    return value


def check_money(value: Any, label: str, field: str, spec: Mapping[str, Any]) -> int:
    currency = spec.get("currency", "")
    if isinstance(value, float) and value.is_integer() and abs(value) <= MAX_SAFE:
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _refuse(label, f"must be an amount of {currency} as a whole number of its "
                             f"smallest unit, not {_shown(value)}.", field)
    if abs(value) > MAX_SAFE:
        raise _refuse(label, "is too large. Use an amount below 9007199254740992.", field)
    _check_range(value, label, field, spec, True)
    return value


def check_link_shape(value: Any, label: str, field: str, spec: Mapping[str, Any]) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _refuse(label, f"must be the id of a record in \"{spec.get('table')}\", not "
                             f"{_shown(value)}.", field)
    return value


# --- computed fields: arithmetic and nothing else -------------------------------------------

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([a-z][a-z0-9_]*)|(.))", re.ASCII)


class Formula:
    """``price * count``: numbers, field names, ``+ - * /``, brackets and a leading minus.

    Parsed once, by recursive descent, into nested tuples. There is no call, no name lookup
    beyond the record's own numeric fields, and no ``eval``."""

    def __init__(self, text: str):
        self.text = text
        self._tokens = self._split(text)
        self._at = 0
        self._depth = 0
        self.tree = self._expr()
        if self._at != len(self._tokens):
            raise ValueError(f"it does not make sense at \"{self._tokens[self._at][1]}\"")
        self.names = frozenset(self._names(self.tree))

    @staticmethod
    def _split(text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if m is None or m.end() == pos:
                break
            number, name, other = m.groups()
            if number is not None:
                out.append(("num", number))
            elif name is not None:
                out.append(("name", name))
            elif other in "+-*/()":
                out.append(("op", other))
            else:
                raise ValueError(f"it uses \"{other}\", and only numbers, field names, "
                                 "+ - * / and brackets are allowed")
            pos = m.end()
        if not text.strip():
            raise ValueError("it is empty")
        return out

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._at] if self._at < len(self._tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise ValueError("it ends too soon")
        self._at += 1
        return token

    def _expr(self) -> Any:
        node = self._term()
        while (t := self._peek()) and t[1] in ("+", "-") and t[0] == "op":
            self._at += 1
            node = (t[1], node, self._term())
        return node

    def _term(self) -> Any:
        node = self._factor()
        while (t := self._peek()) and t[1] in ("*", "/") and t[0] == "op":
            self._at += 1
            node = (t[1], node, self._factor())
        return node

    def _factor(self) -> Any:
        kind, text = self._take()
        if kind == "num":
            return ("num", float(text))
        if kind == "name":
            return ("name", text)
        if text == "-":
            return ("neg", self._factor())
        if text == "(":
            self._depth += 1
            if self._depth > MAX_FORMULA_DEPTH:
                raise ValueError("its brackets are nested too deeply")
            node = self._expr()
            self._depth -= 1
            if self._peek() != ("op", ")"):
                raise ValueError("a bracket is not closed")
            self._at += 1
            return node
        raise ValueError(f"it does not make sense at \"{text}\"")

    @classmethod
    def _names(cls, node: Any) -> Iterable[str]:
        if node[0] == "name":
            yield node[1]
        elif node[0] in ("neg",):
            yield from cls._names(node[1])
        elif node[0] != "num":
            yield from cls._names(node[1])
            yield from cls._names(node[2])

    def evaluate(self, values: Mapping[str, Any]) -> int | float | None:
        """The result, or None when a field it uses is empty or it divides by zero."""
        try:
            result = self._run(self.tree, values)
        except ZeroDivisionError:
            return None
        if result is None or not math.isfinite(result):
            return None
        result = round(result, 6)
        return int(result) if result.is_integer() and abs(result) < MAX_SAFE else result

    def _run(self, node: Any, values: Mapping[str, Any]) -> float | None:
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "name":
            got = values.get(node[1])
            return float(got) if _is_number(got) else None
        if kind == "neg":
            inner = self._run(node[1], values)
            return None if inner is None else -inner
        left, right = self._run(node[1], values), self._run(node[2], values)
        if left is None or right is None:
            return None
        if kind == "+":
            return left + right
        if kind == "-":
            return left - right
        if kind == "*":
            return left * right
        return left / right


_NUMERIC = ("int", "number", "money")


def check(tables: Mapping[str, Any]) -> list[str]:
    """Sentences for anything in the tables that cannot be run. Empty when it is fine."""
    problems: list[str] = []
    for table, spec in tables.items():
        fields = spec.get("fields", {})
        for name, field in fields.items():
            if field.get("type") != "computed":
                continue
            at = f"tables.{table}.fields.{name}.formula"
            try:
                formula = Formula(field.get("formula", ""))
            except ValueError as error:
                problems.append(f"\"{at}\" is not a formula JimmyHQ can work out: {error}. "
                                "Write numbers, field names, + - * / and brackets, such as "
                                "\"price * count\".")
                continue
            for used in sorted(formula.names):
                kind = fields.get(used, {}).get("type")
                if kind not in _NUMERIC:
                    known = ", ".join(n for n, f in fields.items() if f.get("type") in _NUMERIC)
                    problems.append(
                        f"\"{at}\" uses \"{used}\", which is not a number field of the table "
                        f"\"{table}\". Use one of: {known or 'none: add a number field'}.")
    return problems


# --- sorting, filtering, totals -------------------------------------------------------------


def _norm(spec: Mapping[str, Any], value: Any) -> tuple[int, Any]:
    """A value as something comparable with its own kind, and never with another kind."""
    kind = spec.get("type")
    if kind == "choice":
        options = spec.get("options", [])
        return (0, options.index(value) if value in options else -1)
    if isinstance(value, bool):
        return (0, int(value))
    if _is_number(value):
        return (0, value)
    if isinstance(value, str):
        return (1, value.casefold())
    return (2, str(value))


def _cmp_one(spec: Mapping[str, Any], a: Any, b: Any) -> int:
    ka, kb = _norm(spec, a), _norm(spec, b)
    return (ka > kb) - (ka < kb)


def _compare(keys: list[tuple[str, bool]], specs: Mapping[str, Mapping[str, Any]],
             a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    """Order two flat rows (``{"id", ...field values}``): the keys in turn, an empty value
    last whichever way it runs, and the id to settle a tie."""
    for field, descending in keys:
        va, vb = a.get(field), b.get(field)
        if va is None and vb is None:
            continue
        if va is None:
            return 1
        if vb is None:
            return -1
        c = _cmp_one(specs[field], va, vb)
        if c:
            return -c if descending else c
    return (a["id"] > b["id"]) - (a["id"] < b["id"])


def matches(rule: Mapping[str, Any], spec: Mapping[str, Any], value: Any) -> bool:
    """Does a field's value pass one filter rule? A value of the wrong kind never matches
    a comparison, and never raises."""
    op = rule["op"]
    if op == "empty":
        return value is None
    if op == "not_empty":
        return value is not None
    want = rule.get("value")
    if op == "contains":
        if value is None or not isinstance(value, str) or not isinstance(want, str):
            return False
        return want.casefold() in value.casefold()
    if op == "eq":
        return value is not None and _cmp_one(spec, value, want) == 0 and _same_kind(value, want)
    if op == "ne":
        return value is None or _cmp_one(spec, value, want) != 0 or not _same_kind(value, want)
    if value is None or not _same_kind(value, want):
        return False
    c = _cmp_one(spec, value, want)
    return {"lt": c < 0, "le": c <= 0, "gt": c > 0, "ge": c >= 0}[op]


def _same_kind(a: Any, b: Any) -> bool:
    return _norm({}, a)[0] == _norm({}, b)[0]


def total(kind: str, spec: Mapping[str, Any], values: list[Any]) -> int | float | str | None:
    """One total over the non-empty values of a field. ``count`` works on any field; the
    others on numbers and money (and ``min``/``max`` on dates). Anything else is None."""
    present = [v for v in values if v is not None]
    if kind == "count":
        return len(present)
    if not present:
        return None
    field_type = spec.get("type")
    if field_type == "date" and kind in ("min", "max"):
        return min(present) if kind == "min" else max(present)
    if field_type not in _NUMERIC and field_type != "computed":
        return None
    numbers = [v for v in present if _is_number(v)]
    if not numbers:
        return None
    if kind == "min":
        return min(numbers)
    if kind == "max":
        return max(numbers)
    s = math.fsum(numbers)
    if kind == "sum":
        return int(s) if all(isinstance(n, int) for n in numbers) else round(s, 6)
    mean = s / len(numbers)
    if field_type == "money":
        return int(math.floor(mean + 0.5))
    return round(mean, 6)


# --- the app ----------------------------------------------------------------------------------


class Described:
    """The records of one described app, over its own storage."""

    def __init__(self, tables: Mapping[str, Any], views: Iterable[Mapping[str, Any]],
                 store: RecordStore, *, now: Callable[[], int] | None = None,
                 caps: Caps | None = None):
        problems = check(tables)
        if problems:
            raise DescribedError(problems)
        self.tables: Mapping[str, Any] = tables
        self.views: dict[str, Mapping[str, Any]] = {v["id"]: v for v in views}
        self.store = store
        self.caps = caps or Caps()
        self._now = now or (lambda: int(time.time() * 1000))
        self._formulas = {
            (t, n): Formula(f["formula"])
            for t, spec in tables.items() for n, f in spec["fields"].items()
            if f["type"] == "computed"}

    @classmethod
    def for_manifest(cls, manifest: Any, store: RecordStore, **kw: Any) -> Described:
        return cls(manifest.tables, manifest.views, store, **kw)

    # -- names

    def _table(self, name: Any) -> tuple[str, Mapping[str, Any]]:
        if not isinstance(name, str) or name not in self.tables:
            raise RecordError("not_found", (
                f"This app has no table called {_shown(name)}. Its tables are: "
                f"{', '.join(self.tables)}. Nothing was changed."))
        return name, self.tables[name]

    def _record_name(self, table: str) -> str:
        """The table's own name (``book``), which reads as one record; its label is plural."""
        return humanise(table).lower()

    # -- reading a stored record

    def _values(self, table: str, stored: Stored) -> dict[str, Any]:
        """Every field's value under its current name: an old name (``was``) is read when the
        new one is not there, an empty value is None, a computed value is worked out."""
        fields = self.tables[table]["fields"]
        out: dict[str, Any] = {}
        for name, spec in fields.items():
            if spec["type"] == "computed":
                continue
            value = stored.data.get(name)
            if name not in stored.data:
                for old in spec.get("was", ()):
                    if old in stored.data:
                        value = stored.data[old]
                        break
            out[name] = None if value in (None, "") else value
        for name, spec in fields.items():
            if spec["type"] == "computed":
                out[name] = self._formulas[(table, name)].evaluate(out)
        return out

    def label_of(self, table: str, record_id: int, values: Mapping[str, Any]) -> str:
        """What to call a record: its first text field, else its first filled field."""
        fields = self.tables[table]["fields"]
        order = [n for n, f in fields.items() if f["type"] == "text"]
        order += [n for n, f in fields.items() if f["type"] != "text"]
        for name in order:
            value = values.get(name)
            if value not in (None, "") and fields[name]["type"] not in ("link", "computed"):
                return _clip(str(value), MAX_LABEL)
        return f"#{record_id}"

    def _row(self, table: str, stored: Stored) -> dict[str, Any]:
        values = self._values(table, stored)
        return {"id": stored.id, "label": self.label_of(table, stored.id, values),
                "values": values, "created": stored.created, "updated": stored.updated}

    def _labels(self, table: str, rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        """For each link field, the labels of the records the given rows point at. A record
        that is not there has no entry."""
        out: dict[str, dict[str, str]] = {}
        for name, spec in self.tables[table]["fields"].items():
            if spec["type"] != "link":
                continue
            seen: dict[str, str] = {}
            for row in rows:
                target = row["values"].get(name)
                if target is None or str(target) in seen:
                    continue
                stored = self.store.get(spec["table"], target)
                if stored is not None:
                    seen[str(target)] = self.label_of(
                        spec["table"], target, self._values(spec["table"], stored))
            out[name] = seen
        return out

    # -- reading

    def get(self, table: str, record_id: Any) -> dict[str, Any]:
        table, _ = self._table(table)
        stored = self._stored(table, record_id)
        row = self._row(table, stored)
        return {"record": row, "labels": self._labels(table, [row])}

    def _stored(self, table: str, record_id: Any) -> Stored:
        stored = None
        if isinstance(record_id, int) and not isinstance(record_id, bool):
            stored = self.store.get(table, record_id)
        if stored is None:
            raise RecordError("not_found", (
                f"There is no {self._record_name(table)} with the id {_shown(record_id)}. "
                "It may have been deleted. Nothing was changed."))
        return stored

    def list(self, table: str, *, view: str | None = None,
             filter: Any = None, sort: Any = None, search: Any = None,  # noqa: A002
             limit: Any = None, after: Any = None) -> dict[str, Any]:
        """A page of records, with how many match in all and, for a view, its totals.

        ``view`` names a declared view: its filter and sort are the base, the caller's
        ``filter`` is added to it, the caller's ``sort`` replaces its sort. ``after`` is the
        ``next`` of the previous page."""
        table, spec = self._table(table)
        fields: Mapping[str, Mapping[str, Any]] = spec["fields"]
        declared = self._view(view, table)
        rules = list(declared.get("filter", [])) + self._rules(filter, fields)
        keys = self._sort_keys(sort if sort is not None else declared.get("sort"), fields)
        needle = self._search(search)
        page = self._limit(limit)

        rows = [self._row(table, s) for s in self.store.all(table)]
        flat = [{"id": r["id"], "created": r["created"], "updated": r["updated"],
                 **r["values"]} for r in rows]
        keep = [
            i for i, f in enumerate(flat)
            if all(matches(rule, fields[rule["field"]], f.get(rule["field"])) for rule in rules)
            and (needle is None or self._found(table, rows[i], needle))]
        specs = {**{n: {"type": t} for n, t in _PSEUDO.items()}, **fields}
        order = functools.cmp_to_key(
            lambda a, b: _compare(keys, specs, flat[a], flat[b]))
        kept = sorted(keep, key=order)

        start = 0
        if after is not None:
            cursor = self._cursor(after, keys)
            start = next((n for n, i in enumerate(kept)
                          if _compare(keys, specs, flat[i], cursor) > 0), len(kept))
        window = kept[start:start + page]
        more = start + page < len(kept)
        records = [rows[i] for i in window]
        out: dict[str, Any] = {
            "records": records, "total": len(kept), "labels": self._labels(table, records),
            "next": self._encode(flat[window[-1]], keys) if window and more else None}
        if declared.get("totals"):
            out["totals"] = {n: total(how, fields[n], [flat[i].get(n) for i in kept])
                             for n, how in declared["totals"].items()}
        if declared.get("group"):
            out["groups"] = self._groups(declared, fields, flat, kept)
        return out

    def _view(self, view: Any, table: str) -> Mapping[str, Any]:
        if view is None:
            return {}
        found = self.views.get(view) if isinstance(view, str) else None
        if found is None or found["table"] != table:
            raise RecordError("invalid", (
                f"This app has no view called {_shown(view)} for {self._record_name(table)}. "
                "Nothing was changed."))
        return found

    def _groups(self, view: Mapping[str, Any], fields: Mapping[str, Mapping[str, Any]],
                flat: list[dict[str, Any]], kept: list[int]) -> list[dict[str, Any]]:
        name = view["group"]
        buckets: dict[Any, list[dict[str, Any]]] = {}
        for i in kept:
            buckets.setdefault(flat[i].get(name), []).append(flat[i])
        spec = fields[name]
        values = [v for v in buckets if v is not None]
        values.sort(key=lambda v: _norm(spec, v))
        if None in buckets:
            values.append(None)
        if spec["type"] == "choice":  # an empty column is still a column
            values = [o for o in spec["options"] if o not in buckets] + values
            values.sort(key=lambda v: (v is None, _norm(spec, v) if v is not None else (0, 0)))
        out = []
        for value in values:
            members = buckets.get(value, [])
            group: dict[str, Any] = {"value": value, "count": len(members)}
            if view.get("totals"):
                group["totals"] = {n: total(how, fields[n], [m.get(n) for m in members])
                                   for n, how in view["totals"].items()}
            out.append(group)
        return out

    # -- what a caller may ask

    def _limit(self, limit: Any) -> int:
        if limit is None:
            return DEFAULT_LIMIT
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise RecordError("invalid", f"\"limit\" must be a whole number from 1 to "
                                         f"{MAX_LIMIT}, not {_shown(limit)}. Nothing was "
                                         "changed.", "limit")
        return limit

    def _search(self, search: Any) -> str | None:
        if search is None or search == "":
            return None
        if not isinstance(search, str) or len(search) > MAX_SEARCH:
            raise RecordError("invalid", f"\"search\" must be text of {MAX_SEARCH} characters "
                                         "or fewer. Nothing was changed.", "search")
        return search.casefold()

    def _found(self, table: str, row: dict[str, Any], needle: str) -> bool:
        fields = self.tables[table]["fields"]
        if needle in row["label"].casefold():
            return True
        return any(isinstance(row["values"].get(n), str) and needle in row["values"][n].casefold()
                   for n, f in fields.items() if f["type"] in ("text", "choice"))

    def _rules(self, filter: Any, fields: Mapping[str, Any]) -> list[dict[str, Any]]:  # noqa: A002
        if filter is None:
            return []
        example = '[{"field": "stars", "op": "ge", "value": 4}]'
        if not isinstance(filter, list) or len(filter) > MAX_FILTER_RULES:
            raise RecordError("invalid", f"\"filter\" must be a list of at most "
                                         f"{MAX_FILTER_RULES} rules such as {example}. "
                                         "Nothing was changed.", "filter")
        out = []
        for n, rule in enumerate(filter):
            at = f"filter[{n}]"
            if not isinstance(rule, dict) or set(rule) - {"field", "op", "value"}:
                raise RecordError("invalid", f"\"{at}\" must be a rule such as "
                                             f"{example[1:-1]}. Nothing was changed.", at)
            name, op = rule.get("field"), rule.get("op")
            if not isinstance(name, str) or name not in fields:
                raise RecordError("invalid", f"\"{at}.field\" names {_shown(name)}, which is "
                                             f"not a field. The fields are: "
                                             f"{', '.join(fields)}. Nothing was changed.", at)
            if op not in OPS:
                raise RecordError("invalid", f"\"{at}.op\" must be one of: {', '.join(OPS)}. "
                                             "Nothing was changed.", at)
            if op not in ("empty", "not_empty"):
                want = rule.get("value")
                if want is not None and not isinstance(want, bool | int | float | str):
                    raise RecordError("invalid", f"\"{at}.value\" must be text, a number or "
                                                 "yes or no. Nothing was changed.", at)
                if isinstance(want, str) and len(want) > self.caps.text_chars:
                    raise RecordError("invalid", f"\"{at}.value\" is too long. Nothing was "
                                                 "changed.", at)
                if "value" not in rule:
                    raise RecordError("invalid", f"\"{at}.value\" is missing. Nothing was "
                                                 "changed.", at)
            out.append(dict(rule))
        return out

    def _sort_keys(self, sort: Any, fields: Mapping[str, Any]) -> list[tuple[str, bool]]:
        if sort is None:
            return []
        if isinstance(sort, str):
            sort = [sort]
        if not isinstance(sort, list) or len(sort) > MAX_SORT_KEYS:
            raise RecordError("invalid", f"\"sort\" must be a list of at most {MAX_SORT_KEYS} "
                                         "field names, each with a leading minus to run "
                                         "backwards, such as [\"-finished\"]. Nothing was "
                                         "changed.", "sort")
        out = []
        for key in sort:
            bare = key[1:] if isinstance(key, str) and key.startswith("-") else key
            if not isinstance(bare, str) or (bare not in fields and bare not in _PSEUDO):
                raise RecordError("invalid", f"\"sort\" names {_shown(key)}, which is not a "
                                             f"field. The fields are: {', '.join(fields)}. "
                                             "Nothing was changed.", "sort")
            out.append((bare, bare != key))
        return out

    @staticmethod
    def _encode(row: Mapping[str, Any], keys: list[tuple[str, bool]]) -> str:
        body = {"id": row["id"], "k": [row.get(f) for f, _ in keys]}
        return base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")

    @staticmethod
    def _cursor(after: Any, keys: list[tuple[str, bool]]) -> dict[str, Any]:
        again = RecordError("invalid", "\"after\" is not a place this list gave you. Ask "
                                       "for the first page again. Nothing was changed.",
                            "after")
        if not isinstance(after, str) or len(after) > 2000:
            raise again
        try:
            body = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
            ok = (isinstance(body, dict) and isinstance(body.get("id"), int)
                  and isinstance(body.get("k"), list) and len(body["k"]) == len(keys)
                  and all(v is None or isinstance(v, bool | int | float | str)
                          for v in body["k"]))
        except (binascii.Error, ValueError, UnicodeError):
            raise again from None
        if not ok:
            raise again
        return {"id": body["id"], **{f: v for (f, _), v in zip(keys, body["k"], strict=True)}}

    # -- writing

    def create(self, table: str, values: Any) -> dict[str, Any]:
        table, _ = self._table(table)
        most = self.caps.table_records
        if self.store.count(table) >= most:
            raise RecordError("quota", (
                f"This app already keeps {most} {self._record_name(table)} records, which "
                "is as many as one table may hold. Nothing was saved. Delete some first, "
                "or ask the owner to raise \"Records in one table\" under Settings, Apps."))
        clean = self._clean(table, values, existing=None)
        stored = self.store.insert(table, clean, self._now())
        return self.get(table, stored.id)

    def update(self, table: str, record_id: Any, values: Any) -> dict[str, Any]:
        """Change the fields given; the rest stay. An empty value clears a field."""
        table, _ = self._table(table)
        stored = self._stored(table, record_id)
        kept = {n: v for n, v in self._values(table, stored).items()
                if self.tables[table]["fields"][n]["type"] != "computed" and v is not None}
        clean = self._clean(table, values, existing=kept)
        if self.store.replace(table, stored.id, clean, self._now()) is None:
            raise RecordError("not_found", (
                f"That {self._record_name(table)} was deleted while you were editing it. "
                "Nothing was saved."))
        return self.get(table, stored.id)

    def _clean(self, table: str, values: Any, *, existing: dict[str, Any] | None
               ) -> dict[str, Any]:
        """The record to store: ``existing`` with the given changes, every one checked."""
        fields: Mapping[str, Mapping[str, Any]] = self.tables[table]["fields"]
        if not isinstance(values, dict):
            raise RecordError("invalid", "The values must be an object of field names and "
                                         "values, such as {\"title\": \"Dune\"}. Nothing was "
                                         "saved.")
        out = dict(existing or {})
        for name, value in values.items():
            if name == "id":
                raise RecordError("invalid", "\"id\" is given by JimmyHQ and cannot be set. "
                                             "Remove it. Nothing was saved.", "id")
            spec = fields.get(name)
            if spec is None:
                raise RecordError("invalid", (
                    f"{self._record_name(table).capitalize()} has no field called "
                    f"{_shown(name)}. Its fields are: {', '.join(fields)}. Nothing was "
                    "saved."), name if isinstance(name, str) else None)
            label = field_label(name, spec)
            if spec["type"] == "computed":
                raise _refuse(label, "is worked out from other fields and cannot be set. "
                                     "Remove it.", name)
            if value is None or value == "":
                out.pop(name, None)
                continue
            if existing is not None and spec["type"] == "link" and value == existing.get(name):
                continue
            out[name] = self._check(table, name, spec, value)
        for name, spec in fields.items():
            if spec.get("required") and out.get(name) is None:
                raise _refuse(field_label(name, spec), "is needed. Fill it in.", name)
        if len(json.dumps(out)) > self.caps.record_bytes:
            raise RecordError("too_large", (
                f"This {self._record_name(table)} is more than "
                f"{self.caps.record_bytes // 1024} KB in all. Shorten some of the text. "
                "Nothing was saved."))
        return out

    def _check(self, table: str, name: str, spec: Mapping[str, Any], value: Any) -> Any:
        label, kind = field_label(name, spec), spec["type"]
        if kind == "text":
            return check_text(value, label, name, self.caps.text_chars)
        if kind == "int":
            return check_int(value, label, name, spec)
        if kind == "number":
            return check_number(value, label, name, spec)
        if kind == "bool":
            return check_bool(value, label, name)
        if kind == "date":
            return check_date(value, label, name)
        if kind == "choice":
            return check_choice(value, label, name, spec)
        if kind == "money":
            return check_money(value, label, name, spec)
        target = check_link_shape(value, label, name, spec)
        if self.store.get(spec["table"], target) is None:
            raise _refuse(label, (
                f"points at {self._record_name(spec['table'])} {target}, which is not there. "
                "Pick one from the list, or leave it empty."), name)
        return target

    def delete(self, table: str, record_id: Any, *, unlink: bool = False) -> dict[str, Any]:
        """Delete a record. Records that link to it stop it: nothing is deleted unless
        ``unlink`` says to clear those links, which a required link never allows."""
        table, _ = self._table(table)
        stored = self._stored(table, record_id)
        users = self._linked_to(table, stored.id)
        if users:
            count = sum(len(ids) for _, _, ids in users)
            where = " and ".join(f"{_plural(len(ids), 'record')} in {t}" for t, _, ids in users)
            required = any(self.tables[t]["fields"][f].get("required") for t, f, _ in users)
            if required or not unlink:
                why = ("that link is required, so it cannot be cleared. Delete or change "
                       "those first." if required else
                       "Delete or change those first, or choose to clear the links.")
                raise RecordError("invalid", (
                    f"You cannot delete this {self._record_name(table)} yet: {where} "
                    f"{'refers' if count == 1 else 'refer'} to it. {why} Nothing was deleted."))
            for t, f, ids in users:
                for other in ids:
                    row = self.store.get(t, other)
                    if row is not None:
                        data = {k: v for k, v in row.data.items() if k != f}
                        self.store.replace(t, other, data, self._now())
        self.store.delete(table, stored.id)
        return {"deleted": stored.id, "unlinked": sum(len(i) for _, _, i in users)}

    def _linked_to(self, table: str, record_id: int) -> list[tuple[str, str, list[int]]]:
        found = []
        for other, spec in self.tables.items():
            for name, field in spec["fields"].items():
                if field["type"] != "link" or field["table"] != table:
                    continue
                ids = [s.id for s in self.store.all(other)
                       if self._values(other, s).get(name) == record_id
                       and not (other == table and s.id == record_id)]
                if ids:
                    found.append((other, name, ids))
        return found

    # -- renames

    def migrate(self) -> int:
        """After an update that renamed a table (``was``), move its records under the new
        name. Field renames need nothing: they are read through ``was``."""
        moved = 0
        for name, spec in self.tables.items():
            for old in spec.get("was", ()):
                if old not in self.tables:
                    moved += self.store.rename_table(old, name)
        return moved

    # -- the bridge's door

    def handle(self, call: str, args: Any) -> dict[str, Any]:
        """Run one bridge call. Anything wrong is a RecordError."""
        if call not in CALLS:
            raise RecordError("invalid", f"{_shown(call)} is not a records call. The "
                                         f"calls are: {', '.join(CALLS)}.")
        if not isinstance(args, dict):
            raise RecordError("invalid", "The arguments must be an object, such as "
                                         "{\"table\": \"book\"}. Nothing was changed.")
        table = args.get("table")
        if call == "records.list":
            return self.list(table, view=args.get("view"), filter=args.get("filter"),
                             sort=args.get("sort"), search=args.get("search"),
                             limit=args.get("limit"), after=args.get("after"))
        if call == "records.get":
            return self.get(table, args.get("id"))
        if call == "records.create":
            return self.create(table, args.get("values"))
        if call == "records.update":
            return self.update(table, args.get("id"), args.get("values"))
        unlink = args.get("unlink", False)
        if not isinstance(unlink, bool):
            raise RecordError("invalid", "\"unlink\" must be true or false. Nothing was "
                                         "deleted.", "unlink")
        return self.delete(table, args.get("id"), unlink=unlink)
