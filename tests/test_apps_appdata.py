from __future__ import annotations

import sqlite3

import pytest

from jimmyhq.apps import appdata
from jimmyhq.apps.appdata import AppData, AppDataError, Limits
from jimmyhq.apps.described import Described, RecordError
from test_apps_bridge_fakes import BOOKS

TABLES = BOOKS["tables"]


@pytest.fixture
def data(tmp_path):
    store = AppData(tmp_path / "data" / "app.db", TABLES)
    yield store
    store.close()


def refused(fn, *args, **kwargs) -> AppDataError | RecordError:
    with pytest.raises((AppDataError, RecordError)) as caught:
        fn(*args, **kwargs)
    return caught.value


def records(data: AppData, limits: Limits = appdata.UNLIMITED,
            tables: dict | None = None) -> Described:
    """The app's records as the bridge reads and writes them: the rules of ``described``
    over this storage."""
    return Described(tables or data.tables, [], data.records(limits))


# --- values by key ---------------------------------------------------------------------------


def test_a_value_comes_back_as_it_was_stored_and_a_missing_key_is_nothing(data):
    for value in (3, 1.5, "text", True, [1, {"a": None}], {"nested": {"deep": [1, 2]}}):
        data.set("k", value)
        assert data.get("k") == value
    assert data.get("never set") is None


def test_deleting_a_key_says_whether_it_was_there(data):
    data.set("k", 1)
    assert data.delete("k") is True
    assert data.delete("k") is False
    assert data.get("k") is None


def test_keys_are_listed_in_order_and_by_prefix(data):
    for key in ("day:2", "day:1", "goal", "day%", "DAY:3"):
        data.set(key, 1)
    assert data.keys() == ["DAY:3", "day%", "day:1", "day:2", "goal"]
    assert data.keys("day:") == ["day:1", "day:2"]
    assert data.keys("day%") == ["day%"]  # a percent sign is a character, not a wildcard
    assert data.keys("day:", 1) == ["day:1"]


@pytest.mark.parametrize("key", ["", None, 7, "a\x00b", "line\nbreak", "x" * 201, "\ud800"])
def test_a_key_that_is_not_ordinary_text_is_refused_and_nothing_is_stored(data, key):
    error = refused(data.set, key, 1)
    assert error.code == "invalid" and "Nothing was" in error.message
    assert data.usage()["keys"] == 0


@pytest.mark.parametrize("key", [
    "../other/data/app.db", "..\\..\\other", "/etc/passwd", "C:\\Windows\\system.ini",
    "'; DROP TABLE kv; --", "\" OR 1=1 --", "kv); DELETE FROM records; --", "%", "_",
    "app:other", "file:///tmp/x", "\\\\server\\share\\x",
])
def test_a_key_shaped_like_a_path_or_a_query_is_only_ever_a_key(data, tmp_path, key):
    data.set("mine", "kept")
    data.set(key, "odd")
    assert data.get(key) == "odd" and data.get("mine") == "kept"
    assert data.usage()["keys"] == 2
    assert data.delete(key) is True and data.get("mine") == "kept"
    made = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    assert all(name.startswith("app.db") for name in made), made


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1, 2}, object(), b"bytes"])
def test_a_value_json_cannot_hold_is_refused(data, value):
    assert refused(data.set, "k", value).code == "invalid"
    assert data.get("k") is None


def test_a_value_over_the_size_limit_is_refused_with_both_numbers(data):
    limits = Limits(storage_bytes=10**9, value_bytes=2048, records=10)
    error = refused(data.set, "k", "x" * 5000, limits)
    assert error.code == "too_large"
    assert "2.0 KB" in error.message and "4.9 KB" in error.message
    assert data.get("k") is None


def test_storage_over_the_total_limit_is_refused_and_replacing_with_less_is_allowed(data):
    limits = Limits(storage_bytes=3000, value_bytes=2500, records=10)
    data.set("a", "x" * 2000, limits)
    error = refused(data.set, "b", "y" * 2000, limits)
    assert error.code == "quota" and "at most 0 MB" in error.message
    assert "Nothing was saved" in error.message
    assert data.get("b") is None
    data.set("a", "z" * 1000, limits)  # smaller than what it replaces
    data.set("b", "y" * 1500, limits)
    assert data.usage()["bytes"] < 3000


def test_what_is_held_is_counted_in_bytes_keys_and_records(data):
    assert data.usage() == {"bytes": 0, "keys": 0, "records": 0}
    data.set("k", "abc")
    records(data).create("writer", {"name": "A"})
    used = data.usage()
    assert used["keys"] == 1 and used["records"] == 1
    assert used["bytes"] == len("k") + len('"abc"') + len('{"name":"A"}')
    assert data.records().count("writer") == 1 and data.records().count("book") == 0


def test_data_survives_closing_and_opening_again(tmp_path):
    path = tmp_path / "app.db"
    first = AppData(path, TABLES)
    first.set("k", [1, 2])
    made = first.records().insert("writer", {"name": "A"}, 1000)
    first.close()
    second = AppData(path, TABLES)
    assert second.get("k") == [1, 2]
    assert second.records().get("writer", made.id) == made
    second.close()


def test_a_deleted_value_is_gone_from_the_files_on_disk(tmp_path):
    store = AppData(tmp_path / "app.db", TABLES)
    secret, kept = "PLUM-" * 40, "KEPT-" * 40
    store.set("gone", secret)
    store.set("stays", kept)
    made = store.records().insert("writer", {"name": secret}, 1000)
    store.delete("gone")
    assert store.records().delete("writer", made.id) is True
    store.close()
    on_disk = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert kept.encode() in on_disk
    assert secret.encode() not in on_disk and b"PLUM-PLUM" not in on_disk


def test_the_database_has_secure_delete_on(data):
    assert data._db.execute("PRAGMA secure_delete").fetchone()[0] == 1


# --- records ---------------------------------------------------------------------------------
# What a record may hold and how a list is read are ``described``'s rules, tested in
# test_apps_described.py over this same storage. Here: what the storage itself must hold to.


def test_a_record_is_kept_read_replaced_and_deleted(data):
    rows = data.records()
    made = rows.insert("book", {"title": "Dune", "stars": 5}, 1000)
    assert (made.data, made.created, made.updated) == ({"title": "Dune", "stars": 5}, 1000,
                                                       1000)
    assert rows.get("book", made.id) == made and rows.all("book") == [made]
    changed = rows.replace("book", made.id, {"title": "Dune", "stars": 4}, 2000)
    assert (changed.data["stars"], changed.created, changed.updated) == (4, 1000, 2000)
    assert rows.get("book", made.id) == changed
    assert rows.delete("book", made.id) is True and rows.delete("book", made.id) is False
    assert rows.get("book", made.id) is None and rows.replace("book", made.id, {}, 3000) is None


def test_an_id_is_a_whole_number_counted_across_tables_and_never_used_twice(data):
    rows = data.records()
    first = rows.insert("book", {"title": "a"}, 1)
    second = rows.insert("writer", {"name": "b"}, 2)
    assert (first.id, second.id) == (1, 2)
    rows.delete("writer", second.id)
    assert rows.insert("writer", {"name": "c"}, 3).id == 3


@pytest.mark.parametrize("table", ["kv", "records", "meta", "sqlite_master", "Book", "",
                                   "book; DROP TABLE records", "../book", None, 3,
                                   "book\x00", "writer' OR '1'='1"])
def test_a_table_the_manifest_does_not_declare_cannot_be_reached(data, table):
    app = records(data)
    made = app.create("book", {"title": "Dune"})["record"]
    for attempt in (lambda: app.list(table), lambda: app.create(table, {"title": "x"}),
                    lambda: app.get(table, made["id"]), lambda: app.delete(table, made["id"])):
        error = refused(attempt)
        assert error.code == "not_found" and "book, writer" in error.message
    assert data.usage()["records"] == 1
    # And the storage, asked directly, only ever treats the name as a value.
    assert data.records().all(table if isinstance(table, str) else "x") == []
    assert data.records().get(table, made["id"]) is None


def test_a_record_cannot_be_reached_through_another_table(data):
    app = records(data)
    writer = app.create("writer", {"name": "A"})["record"]["id"]
    assert refused(app.get, "book", writer).code == "not_found"
    assert refused(app.update, "book", writer, {"title": "x"}).code == "not_found"
    assert refused(app.delete, "book", writer).code == "not_found"
    assert data.records().delete("book", writer) is False
    assert app.get("writer", writer)["record"]["values"]["name"] == "A"


@pytest.mark.parametrize("record_id", ["", "1", "' OR 1=1 --", "../x", None, 1.0, True, -1, 0,
                                       2**70, "%" * 26, [1], {"id": 1}])
def test_an_id_that_is_not_a_record_id_finds_nothing(data, record_id):
    app = records(data)
    assert app.create("book", {"title": "Dune"})["record"]["id"] == 1
    assert refused(app.get, "book", record_id).code == "not_found"
    assert refused(app.delete, "book", record_id).code == "not_found"
    assert data.records().get("book", record_id) is None
    assert data.records().delete("book", record_id) is False
    assert data.records().replace("book", record_id, {"title": "x"}, 5) is None
    assert data.usage()["records"] == 1 and app.get("book", 1)["record"]["label"] == "Dune"


def test_the_record_limit_is_refused_with_the_number(data):
    app = records(data, Limits(storage_bytes=10**9, value_bytes=10**6, records=2))
    app.create("book", {"title": "1"})
    app.create("writer", {"name": "2"})
    error = refused(app.create, "book", {"title": "3"})
    assert error.code == "quota" and "at most 2 records" in error.message
    assert data.usage()["records"] == 2


def test_a_record_over_the_size_limit_is_refused(data):
    app = records(data, Limits(storage_bytes=10**9, value_bytes=1024, records=10))
    assert refused(app.create, "book", {"title": "x" * 1500}).code == "too_large"
    made = app.create("book", {"title": "ok"})["record"]["id"]
    assert refused(app.update, "book", made, {"title": "x" * 1500}).code == "too_large"
    assert app.get("book", made)["record"]["values"]["title"] == "ok"


def test_records_count_toward_the_apps_total_storage(data):
    limits = Limits(storage_bytes=300, value_bytes=10**6, records=100)
    app = records(data, limits)
    data.set("k", "x" * 200, limits)
    error = refused(app.create, "book", {"title": "y" * 200})
    assert error.code == "quota" and "Nothing was saved" in error.message
    assert data.usage()["records"] == 0
    assert app.create("book", {"title": "small"})["record"]["label"] == "small"


def test_text_json_cannot_hold_is_refused_and_nothing_is_saved(data):
    error = refused(data.records().insert, "book", {"title": "half \ud800"}, 1)
    assert error.code == "invalid" and data.usage()["records"] == 0


# --- renames ---------------------------------------------------------------------------------


def test_rows_follow_a_table_and_a_field_that_were_renamed(tmp_path):
    path = tmp_path / "app.db"
    old_tables = {"book": {"fields": {"name": {"type": "text"}, "stars": {"type": "int"}}},
                  "loan": {"fields": {"of": {"type": "link", "table": "book"}}}}
    old = AppData(path, old_tables)
    app = records(old)
    made = app.create("book", {"name": "Dune", "stars": 5})["record"]["id"]
    loan = app.create("loan", {"of": made})["record"]["id"]
    old.close()
    new_tables = {
        "title": {"was": ["book"], "fields": {
            "title": {"type": "text", "was": ["name"]}, "stars": {"type": "int"}}},
        "loan": {"fields": {"of": {"type": "link", "table": "title"}}}}
    new = AppData(path, new_tables)
    app = records(new)
    assert app.get("title", made)["record"]["values"] == {"title": "Dune", "stars": 5}
    assert new.records().get("title", made).data == {"title": "Dune", "stars": 5}
    # The link still points at the same record: its id did not change with the table's name.
    assert app.get("loan", loan)["labels"] == {"of": {str(made): "Dune"}}
    assert new.usage()["records"] == 2 and new.records().all("book") == []
    new.close()


# --- where the database lives ----------------------------------------------------------------


def test_each_app_gets_its_own_database_under_the_data_folder(core):
    water = appdata.open_data(core, "water")
    other = appdata.open_data(core, "other")
    water.set("k", "water's")
    assert other.get("k") is None
    assert water.path == core.config.data / "apps" / "water" / "data" / "app.db"
    assert other.path == core.config.data / "apps" / "other" / "data" / "app.db"
    assert appdata.open_data(core, "water") is water


@pytest.mark.parametrize("app_id", [
    "..", "../water", "water/../other", "water/data", "/abs", "\\\\server\\share", "C:",
    "c:\\other", "Water", "water ", "wa", "w" * 40, "water\x00", "water%2f..", "con.txt",
    "", None, 7, "a.b", "~", "water\n",
][:-1] + ["water\n"])
def test_an_app_id_that_is_not_an_id_never_becomes_a_path(core, app_id):
    if app_id == "wa":  # two characters is a real id
        assert appdata.data_dir(core, app_id).name == "data"
        return
    before = sorted(p for p in core.config.data.rglob("*"))
    with pytest.raises(AppDataError) as caught:
        appdata.open_data(core, app_id)
    assert caught.value.code == "not_found"
    assert sorted(p for p in core.config.data.rglob("*")) == before


def test_a_data_folder_that_is_a_link_is_not_followed(core, tmp_path):
    victim = appdata.open_data(core, "victim")
    victim.set("secret", "theirs")
    apps = core.config.data / "apps"
    (apps / "sneak").mkdir()
    try:
        (apps / "sneak" / "data").symlink_to(apps / "victim" / "data",
                                             target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let the test make a link")
    with pytest.raises(AppDataError) as caught:
        appdata.open_data(core, "sneak")
    assert caught.value.code == "unavailable"


def test_changed_tables_reopen_the_database_and_drop_closes_it(core):
    first = appdata.open_data(core, "books_read", TABLES)
    made = first.records().insert("writer", {"name": "A"}, 1)
    second = appdata.open_data(core, "books_read", {"writer": TABLES["writer"]})
    assert second is not first and second.records().get("writer", made.id).data == {"name": "A"}
    appdata.stores(core).drop("books_read")
    with pytest.raises(sqlite3.ProgrammingError):
        second.usage()
