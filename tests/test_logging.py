from __future__ import annotations

import logging

import pytest

from jimmyhq.cli import SCRUBBED_LOGGERS, _NoQueryStrings, scrub_logs

KEY = "jhqi_" + "B" * 43


def test_what_was_typed_into_a_search_never_reaches_the_access_log():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/memory/search?q=something+private&k=5", "1.1", 200), None,
    )
    assert _NoQueryStrings().filter(record) is True
    line = record.getMessage()
    assert "/api/memory/search" in line and "private" not in line and "?" not in line


def test_a_request_without_a_query_is_logged_unchanged():
    args = ("127.0.0.1:5000", "GET", "/api/health", "1.1", 200)
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                               '%s - "%s %s HTTP/%s" %d', args, None)
    _NoQueryStrings().filter(record)
    assert record.args == args


@pytest.mark.parametrize("msg,args", [
    ("plain message with no arguments " + KEY, None),
    ("a door was knocked at /api/in/house/event/k/" + KEY, None),
    ("%s", (f"x {KEY} y",)),
    ("%(path)s", ({"path": f"/api/in/house/event/k/{KEY}"},)),
    ('%s - "%s %s HTTP/%s" %d', ("192.0.2.1:5", "POST", f"/api/in/house/event/k/%6A{KEY[1:]}",
                                 "1.1", 202)),
])
def test_a_device_key_is_cleaned_from_the_message_as_well_as_its_arguments(msg, args):
    for name in ("uvicorn.error", "jimmyhq.door", "uvicorn.access"):
        record = logging.LogRecord(name, logging.INFO, "", 0, msg, args, None)
        assert _NoQueryStrings().filter(record) is True
        line = record.getMessage()
        assert KEY not in line and KEY[5:] not in line, (name, line)


def test_a_question_mark_in_another_loggers_message_is_left_alone():
    record = logging.LogRecord("jimmyhq.web", logging.INFO, "", 0, "%s %s %s",
                               ("one", "two", "is it?"), None)
    _NoQueryStrings().filter(record)
    assert record.getMessage() == "one two is it?"


def test_the_scrub_sits_on_uvicorns_loggers_jimmyhqs_own_and_every_handler(monkeypatch):
    for name in SCRUBBED_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "filters", [])
    handler = logging.Handler()
    scrub_logs([handler])
    scrub_logs([handler])  # twice is still once
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "jimmyhq"):
        found = [f for f in logging.getLogger(name).filters if isinstance(f, _NoQueryStrings)]
        assert len(found) == 1, name
    assert len([f for f in handler.filters if isinstance(f, _NoQueryStrings)]) == 1


def test_a_key_logged_by_any_jimmyhq_or_uvicorn_logger_never_reaches_the_file(monkeypatch):
    for name in SCRUBBED_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "filters", [])
    seen: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(self.format(record))

    handler = Keep()
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [handler])
    monkeypatch.setattr(root, "level", logging.INFO)
    scrub_logs([handler])
    for name in ("uvicorn", "uvicorn.error", "jimmyhq", "jimmyhq.door", "jimmyhq.apps.inbound"):
        logging.getLogger(name).warning("refused /api/in/house/event/k/" + KEY)
        logging.getLogger(name).warning("refused %s", KEY)
    assert len(seen) == 10 and all(KEY not in line for line in seen), seen
