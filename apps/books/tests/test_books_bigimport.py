"""A long history comes in: 10,000 rows, a file over 1 MB, within one call's time, and the
largest file Books takes is the owner's setting."""

from __future__ import annotations

import json
import time

import pytest
from jimmyhq_app import CallError

ROWS = 10_000
CALL_LIMIT_S = 30  # the platform gives up on a page's call after this long


def big_everything(rows: int = ROWS) -> str:
    """A version 2 file with one account and ``rows`` rows (about 1.5 MB at 10,000)."""
    shops = ["Corner Shop", "Hardware Barn", "Example Cafe", "Station Kiosk", "Garden Centre"]
    txns = []
    for n in range(rows):
        day = 1 + n % 28
        month = 1 + (n // 28) % 12
        txns.append({"id": n + 1, "account": 1, "date": f"2027-{month:02d}-{day:02d}",
                     "payee": f"{shops[n % 5]} number {n}", "category": 1 + n % 2,
                     "memo": f"Row {n} of an invented long history, for a size test",
                     "number": str(1000 + n) if n % 7 == 0 else "",
                     "amount_cents": -(100 + n % 9000), "state": "c" if n % 3 else "",
                     "transfer": None})
    return json.dumps({
        "format": "jimmyhq-books", "version": 2, "exported_ms": 1_800_000_000_000,
        "accounts": [{"id": 1, "name": "Example Bank", "kind": "checking",
                      "opening_cents": 5_000_000, "opening_date": "2027-01-01",
                      "closed": False}],
        "categories": [{"id": 1, "name": "Groceries", "income": False},
                       {"id": 2, "name": "Home", "income": False}],
        "transactions": txns, "reminders": [], "debts": [], "reconciliations": []})


def test_a_file_of_ten_thousand_rows_over_a_megabyte_comes_in_whole_within_a_calls_time(rig):
    text = big_everything()
    assert len(text.encode("utf-8")) > 1_500_000
    started = time.monotonic()
    token = rig.upload(text)
    said = rig.call("everything.import", {"token": token})
    took = time.monotonic() - started
    assert said["transactions"] == ROWS and said["accounts"] == 1
    assert took < CALL_LIMIT_S / 2, f"{took:.1f}s for upload and import together"
    bank = rig.call("overview")["accounts"][0]
    assert bank["rows"] == ROWS
    assert rig.store.one("SELECT count(*) AS n FROM txn WHERE number != ''")["n"] == 1429


def test_ten_thousand_rows_come_back_out_as_one_file_over_a_megabyte_with_nothing_left_out(
        make_rig):
    first_rig, second_rig = make_rig(), make_rig()
    first_rig.call("everything.import", {"token": first_rig.upload(big_everything())})
    first = first_rig.call("export.part", {"what": "json", "part": 0})
    text = first["text"]
    for part in range(1, first["parts"]):
        text += first_rig.call("export.part", {"token": first["token"], "part": part})["text"]
    assert first["parts"] > 1 and first["name"] == "books-everything"
    assert first["bytes"] == len(text.encode("utf-8")) > 1_000_000  # once refused; now saved whole
    assert len(json.loads(text)["transactions"]) == ROWS
    said = second_rig.call("everything.import", {"token": second_rig.upload(text)})
    assert said["transactions"] == ROWS  # and the one file is the whole backup


def test_the_export_is_not_packed_or_cut_to_fit_a_size_the_page_once_could_save(rig):
    rig.call("everything.import", {"token": rig.upload(big_everything(2000))})
    first = rig.call("export.part", {"what": "json", "part": 0})
    assert first["text"].startswith('{\n "format"')  # readable, not squeezed onto one line


def test_a_bank_file_of_ten_thousand_lines_previews_and_imports_within_a_calls_time(rig):
    bank = rig.account("Example Bank", opening="0")
    lines = ["Date,Description,Amount,Check Number"]
    for n in range(ROWS):
        lines.append(f"2027-{1 + n % 12:02d}-{1 + n % 28:02d},Shop {n % 400} item {n},"
                     f"-{1 + n % 90}.{n % 100:02d},{n if n % 50 == 0 else ''}")
    token = rig.upload("\r\n".join(lines) + "\r\n")
    started = time.monotonic()
    preview = rig.call("import.preview", {"account": bank, "token": token})
    done = rig.call("import.commit", {"account": bank, "token": token})
    took = time.monotonic() - started
    assert preview["lines"] == ROWS and preview["new"] == ROWS
    assert done["added"] == ROWS
    assert took < CALL_LIMIT_S / 2, f"{took:.1f}s for preview and import together"
    assert rig.store.one("SELECT count(*) AS n FROM txn WHERE number != ''")["n"] == 200


def test_a_file_over_the_setting_is_refused_as_it_uploads_and_nothing_is_kept(make_rig):
    small = make_rig(settings={"import_mb": 1})
    text = big_everything(7000)  # over 1 MB
    assert len(text.encode("utf-8")) > 1_000_000
    token = small.call("upload.start")["token"]
    assert small.call("upload.start")["max_bytes"] == 1_000_000
    with pytest.raises(CallError, match="larger than 1 MB.*Nothing was changed"):
        for part in range(0, 100):
            small.call("upload.part", {"token": token, "part": part,
                                       "text": text[part * 200_000:(part + 1) * 200_000]})
    assert small.store.one("SELECT count(*) AS n FROM upload WHERE token=?", (token,))["n"] == 0


def test_the_ceiling_follows_the_setting_and_starts_at_eight_megabytes(make_rig):
    assert make_rig().call("upload.start")["max_bytes"] == 8_000_000
    assert make_rig(settings={"import_mb": 20}).call("upload.start")["max_bytes"] == 20_000_000
    assert make_rig().call("overview")["import_bytes"] == 8_000_000
