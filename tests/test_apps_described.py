"""A described app keeps and shows records, and nothing else: every value is checked with a
sentence that names its field, links cannot dangle, and a computed field is only arithmetic."""

from __future__ import annotations

from pathlib import Path

import pytest

from jimmyhq.apps import load
from jimmyhq.apps import manifest as mf
from jimmyhq.apps.appdata import AppData
from jimmyhq.apps.described import (
    CALLS,
    MAX_LIMIT,
    MAX_RECORDS,
    MAX_TEXT,
    Described,
    DescribedError,
    Formula,
    MemoryStore,
    RecordError,
    check,
    matches,
    total,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "apps" / "examples"


TABLES = {
    "author": {"label": "Authors", "fields": {"name": {"type": "text", "required": True}}},
    "book": {"label": "Books", "fields": {
        "title": {"type": "text", "label": "Title", "required": True},
        "author": {"type": "link", "table": "author"},
        "stars": {"type": "int", "label": "Stars", "min": 1, "max": 5},
        "weight": {"type": "number", "min": 0, "max": 10},
        "read": {"type": "bool"},
        "finished": {"type": "date"},
        "status": {"type": "choice", "options": ["to read", "reading", "finished"]},
        "paid": {"type": "money", "currency": "USD", "min": 0},
        "pages": {"type": "int", "min": 1},
        "per_page": {"type": "computed", "formula": "paid / pages"},
    }},
}
VIEWS = [
    {"id": "all", "label": "All", "table": "book", "sort": ["-finished"],
     "totals": {"stars": "avg", "paid": "sum"}},
    {"id": "top", "label": "Top", "table": "book", "group": "status",
     "filter": [{"field": "stars", "op": "ge", "value": 4}], "totals": {"pages": "sum"}},
]


@pytest.fixture(params=["memory", "appdata"])
def app(request, tmp_path):
    """Every rule is tested twice: over a dict, and over the app's real database."""
    if request.param == "memory":
        store = MemoryStore()
    else:
        data = AppData(tmp_path / "app.db", TABLES)
        request.addfinalizer(data.close)
        store = data.records()
    clock = iter(range(1000, 10**6, 10))
    return Described(TABLES, VIEWS, store, now=lambda: next(clock))


def refused(call, *args, code="invalid", **kw) -> str:
    with pytest.raises(RecordError) as caught:
        call(*args, **kw)
    assert caught.value.code == code
    return caught.value.message


def values(result):
    return result["record"]["values"]


# --- each field type -------------------------------------------------------------------------


def test_a_text_value_must_be_text_within_its_length_without_control_characters(app):
    assert "\"Title\" must be text" in refused(app.create, "book", {"title": 5})
    assert "characters long" in refused(app.create, "book", {"title": "x" * (MAX_TEXT + 1)})
    assert "invisible" in refused(app.create, "book", {"title": "a\x00b"})
    assert "invisible" in refused(app.create, "book", {"title": "a‮b"})
    ok = app.create("book", {"title": "Line one\nLine two"})
    assert values(ok)["title"] == "Line one\nLine two"


def test_a_whole_number_field_refuses_fractions_text_and_values_out_of_range(app):
    assert "whole number from 1 to 5" in refused(app.create, "book", {"title": "a", "stars": 2.5})
    assert "\"Stars\"" in refused(app.create, "book", {"title": "a", "stars": "4"})
    assert "not 9" in refused(app.create, "book", {"title": "a", "stars": 9})
    assert "not 0" in refused(app.create, "book", {"title": "a", "stars": 0})
    assert "not true" in refused(app.create, "book", {"title": "a", "stars": True})
    assert "too large" in refused(app.create, "book", {"title": "a", "pages": 2**60})
    assert values(app.create("book", {"title": "a", "stars": 5.0}))["stars"] == 5


def test_a_number_field_takes_fractions_inside_its_bounds_and_nothing_else(app):
    assert values(app.create("book", {"title": "a", "weight": 2.5}))["weight"] == 2.5
    assert "\"Weight\" must be a number from 0 to 10" in refused(
        app.create, "book", {"title": "a", "weight": 11})
    assert "\"Weight\"" in refused(app.create, "book", {"title": "a", "weight": "heavy"})
    assert "\"Weight\"" in refused(app.create, "book", {"title": "a", "weight": float("nan")})


def test_a_yes_no_field_takes_only_true_or_false(app):
    assert values(app.create("book", {"title": "a", "read": False}))["read"] is False
    assert "yes or no" in refused(app.create, "book", {"title": "a", "read": 1})
    assert "yes or no" in refused(app.create, "book", {"title": "a", "read": "yes"})


def test_a_date_field_takes_only_a_real_date_written_year_month_day(app):
    assert values(app.create("book", {"title": "a", "finished": "2030-02-28"}))["finished"]
    for bad in ("2030-02-30", "2030-2-3", "31/01/2030", "2030-01-01T00:00", 20300101, "٢٠٣٠-٠١-٠١"):
        assert "\"Finished\" must be a real date" in refused(
            app.create, "book", {"title": "a", "finished": bad})


def test_a_choice_field_takes_only_one_of_its_options(app):
    assert values(app.create("book", {"title": "a", "status": "reading"}))["status"] == "reading"
    message = refused(app.create, "book", {"title": "a", "status": "lost"})
    assert "\"Status\" must be one of: to read, reading, finished" in message


def test_a_money_field_is_a_whole_count_of_the_smallest_unit(app):
    assert values(app.create("book", {"title": "a", "paid": 1250}))["paid"] == 1250
    assert "whole number of its smallest unit" in refused(
        app.create, "book", {"title": "a", "paid": 12.5})
    assert "\"Paid\"" in refused(app.create, "book", {"title": "a", "paid": -1})


def test_a_link_field_takes_the_id_of_a_record_that_exists(app):
    author = app.create("author", {"name": "Avery Example"})["record"]["id"]
    book = app.create("book", {"title": "a", "author": author})
    assert values(book)["author"] == author
    assert book["labels"]["author"] == {str(author): "Avery Example"}
    assert "id of a record in \"author\"" in refused(
        app.create, "book", {"title": "a", "author": "Avery Example"})
    assert "id of a record" in refused(app.create, "book", {"title": "a", "author": True})


def test_a_record_that_refers_to_a_missing_record_is_refused_and_nothing_is_kept(app):
    message = refused(app.create, "book", {"title": "a", "author": 99})
    assert "\"Author\" points at author 99, which is not there" in message
    assert "Nothing was saved" in message
    assert app.list("book")["total"] == 0


def test_a_required_field_must_be_filled_when_creating_and_cannot_be_cleared_later(app):
    assert "\"Title\" is needed" in refused(app.create, "book", {})
    assert "\"Title\" is needed" in refused(app.create, "book", {"title": ""})
    rec = app.create("book", {"title": "a"})["record"]["id"]
    assert "\"Title\" is needed" in refused(app.update, "book", rec, {"title": None})


def test_unknown_fields_ids_and_computed_values_cannot_be_written(app):
    assert "no field called \"colour\"" in refused(app.create, "book", {"title": "a", "colour": 1})
    assert "\"id\"" in refused(app.create, "book", {"title": "a", "id": 5})
    assert "worked out" in refused(app.create, "book", {"title": "a", "per_page": 3})
    assert "object" in refused(app.create, "book", ["title"])
    assert "no table called \"movie\"" in refused(app.create, "movie", {}, code="not_found")


def test_a_record_too_big_to_keep_is_refused_with_too_large(app):
    big = {"title": "a", "status": "reading"}
    names = [f"f{n}" for n in range(40)]
    notes = Described({"note": {"fields": {n: {"type": "text"} for n in names}}}, [],
                      MemoryStore())
    message = refused(notes.create, "note", {n: "x" * MAX_TEXT for n in names},
                      code="too_large")
    assert "64 KB" in message and big


def test_a_table_cannot_hold_more_records_than_the_cap(app):
    app.store.count = lambda table: MAX_RECORDS  # type: ignore[method-assign]
    assert "Delete some first" in refused(app.create, "book", {"title": "a"}, code="quota")


# --- operations ------------------------------------------------------------------------------


def test_creating_a_record_returns_it_with_its_id_label_and_times(app):
    made = app.create("book", {"title": "Dune", "stars": 5})["record"]
    assert made["id"] == 1 and made["label"] == "Dune"
    assert made["created"] == made["updated"]
    assert made["values"]["finished"] is None  # every field is present, empty ones are None


def test_reading_a_record_that_is_not_there_says_so(app):
    assert "no book with the id 7" in refused(app.get, "book", 7, code="not_found")
    assert "no book" in refused(app.get, "book", "x", code="not_found")


def test_updating_changes_only_the_fields_given_and_moves_the_updated_time(app):
    first = app.create("book", {"title": "Dune", "stars": 4})["record"]
    changed = app.update("book", first["id"], {"stars": 5})["record"]
    assert changed["values"]["title"] == "Dune" and changed["values"]["stars"] == 5
    assert changed["created"] == first["created"] and changed["updated"] > first["updated"]


def test_an_empty_value_clears_an_optional_field(app):
    rec = app.create("book", {"title": "a", "stars": 3})["record"]["id"]
    assert values(app.update("book", rec, {"stars": None}))["stars"] is None
    assert values(app.update("book", rec, {"finished": ""}))["finished"] is None


def test_updating_with_a_bad_value_leaves_the_record_as_it_was(app):
    rec = app.create("book", {"title": "a", "stars": 3})["record"]["id"]
    refused(app.update, "book", rec, {"title": "b", "stars": 99})
    assert values(app.get("book", rec))["title"] == "a"
    assert "no book with the id 40" in refused(app.update, "book", 40, {}, code="not_found")


def test_an_unchanged_link_does_not_block_editing_other_fields(app):
    author = app.create("author", {"name": "A"})["record"]["id"]
    rec = app.create("book", {"title": "a", "author": author})["record"]["id"]
    assert values(app.update("book", rec, {"author": author, "stars": 2}))["stars"] == 2


def test_deleting_removes_the_record(app):
    rec = app.create("book", {"title": "a"})["record"]["id"]
    assert app.delete("book", rec) == {"deleted": rec, "unlinked": 0}
    assert "no book" in refused(app.get, "book", rec, code="not_found")
    assert "no book" in refused(app.delete, "book", rec, code="not_found")


def test_a_record_that_others_link_to_is_not_deleted_unless_the_links_are_cleared(app):
    author = app.create("author", {"name": "A"})["record"]["id"]
    book = app.create("book", {"title": "a", "author": author})["record"]["id"]
    message = refused(app.delete, "author", author)
    assert "1 record in book refers to it" in message and "Nothing was deleted" in message
    assert app.get("author", author)
    assert app.delete("author", author, unlink=True)["unlinked"] == 1
    assert values(app.get("book", book))["author"] is None


def test_a_required_link_is_never_cleared_to_make_a_delete_possible():
    tables = {"room": {"fields": {"name": {"type": "text"}}},
              "job": {"fields": {"room": {"type": "link", "table": "room", "required": True}}}}
    app = Described(tables, [], MemoryStore())
    room = app.create("room", {"name": "r"})["record"]["id"]
    app.create("job", {"room": room})
    message = refused(app.delete, "room", room, unlink=True)
    assert "cannot be cleared" in message and app.get("room", room)


def test_a_field_renamed_in_an_update_is_read_through_its_was_list():
    store = MemoryStore()
    old = {"book": {"fields": {"name": {"type": "text"}}}}
    Described(old, [], store).create("book", {"name": "Dune"})
    new = {"book": {"fields": {"title": {"type": "text", "was": ["name"]}}}}
    app = Described(new, [], store)
    assert app.list("book")["records"][0]["values"]["title"] == "Dune"
    app.update("book", 1, {"title": "Dune 2"})
    assert store.get("book", 1).data == {"title": "Dune 2"}


def test_a_table_renamed_in_an_update_is_moved_under_its_new_name_by_migrate():
    store = MemoryStore()
    Described({"entry": {"fields": {"t": {"type": "text"}}}}, [], store).create("entry",
                                                                               {"t": "x"})
    app = Described({"post": {"was": ["entry"], "fields": {"t": {"type": "text"}}}}, [], store)
    app = Described(app.tables, [], store)
    assert app.migrate() == 1
    assert app.list("post")["total"] == 1


# --- list: filter, sort, paging --------------------------------------------------------------


def seed(app) -> None:
    for title, stars, finished, status, paid, pages in [
        ("Dune", 5, "2030-01-10", "finished", 1000, 400),
        ("Emma", 3, "2030-03-01", "finished", 500, 250),
        ("Ulysses", None, None, "reading", None, None),
        ("Beloved", 4, "2030-02-01", "to read", 700, 350),
        ("anna", 4, None, None, 300, 150),
    ]:
        app.create("book", {k: v for k, v in dict(
            title=title, stars=stars, finished=finished, status=status, paid=paid,
            pages=pages).items() if v is not None})


def titles(result) -> list[str]:
    return [r["values"]["title"] for r in result["records"]]


def test_listing_without_options_returns_records_in_the_order_they_were_made(app):
    seed(app)
    result = app.list("book")
    assert titles(result) == ["Dune", "Emma", "Ulysses", "Beloved", "anna"]
    assert result["total"] == 5 and result["next"] is None


def test_sorting_runs_forwards_or_backwards_and_puts_empty_values_last_either_way(app):
    seed(app)
    assert titles(app.list("book", sort=["finished"]))[:3] == ["Dune", "Beloved", "Emma"]
    assert titles(app.list("book", sort=["-finished"]))[:3] == ["Emma", "Beloved", "Dune"]
    assert titles(app.list("book", sort=["-finished"]))[3:] == ["Ulysses", "anna"]


def test_sorting_text_ignores_case_and_a_second_key_breaks_ties(app):
    seed(app)
    assert titles(app.list("book", sort="title")) == ["anna", "Beloved", "Dune", "Emma", "Ulysses"]
    assert titles(app.list("book", sort=["-stars", "title"]))[:3] == ["Dune", "anna", "Beloved"]


def test_sorting_a_choice_follows_its_options_and_a_computed_field_its_value(app):
    seed(app)
    assert titles(app.list("book", sort=["status", "title"]))[:3] == ["Beloved", "Ulysses", "Dune"]
    assert titles(app.list("book", sort=["-per_page"]))[:2] == ["Dune", "Emma"]


def test_sorting_by_the_time_made_or_changed_is_allowed(app):
    seed(app)
    assert titles(app.list("book", sort=["-created"]))[0] == "anna"
    app.update("book", 1, {"stars": 1})
    assert titles(app.list("book", sort=["-updated"]))[0] == "Dune"


def test_a_sort_that_names_no_field_is_refused_with_the_fields_to_use(app):
    assert "not a field" in refused(app.list, "book", sort=["colour"])
    assert "at most 5" in refused(app.list, "book", sort=["title"] * 6)


@pytest.mark.parametrize(("rule", "expected"), [
    ({"field": "stars", "op": "ge", "value": 4}, {"Dune", "Beloved", "anna"}),
    ({"field": "stars", "op": "lt", "value": 4}, {"Emma"}),
    ({"field": "stars", "op": "eq", "value": 4}, {"Beloved", "anna"}),
    ({"field": "stars", "op": "ne", "value": 4}, {"Dune", "Emma", "Ulysses"}),
    ({"field": "stars", "op": "empty"}, {"Ulysses"}),
    ({"field": "finished", "op": "not_empty"}, {"Dune", "Emma", "Beloved"}),
    ({"field": "title", "op": "contains", "value": "AN"}, {"anna"}),
    ({"field": "title", "op": "contains", "value": "e"}, {"Dune", "Emma", "Ulysses", "Beloved"}),
    ({"field": "finished", "op": "gt", "value": "2030-01-15"}, {"Emma", "Beloved"}),
    ({"field": "status", "op": "eq", "value": "reading"}, {"Ulysses"}),
    ({"field": "per_page", "op": "gt", "value": 2.2}, {"Dune"}),
])
def test_each_filter_operation_keeps_exactly_the_records_that_pass(app, rule, expected):
    seed(app)
    assert set(titles(app.list("book", filter=[rule]))) == expected


def test_a_filter_with_a_value_of_the_wrong_kind_matches_nothing_and_never_raises():
    spec = {"type": "int"}
    assert not matches({"op": "gt", "value": "abc"}, spec, 3)
    assert not matches({"op": "eq", "value": "3"}, spec, 3)
    assert not matches({"op": "contains", "value": "a"}, spec, 3)
    assert matches({"op": "ne", "value": "3"}, spec, 3)


def test_filters_are_added_together_and_a_views_own_filter_always_applies(app):
    seed(app)
    both = app.list("book", view="top", filter=[{"field": "status", "op": "eq",
                                                "value": "to read"}])
    assert titles(both) == ["Beloved"]


def test_a_bad_filter_is_refused_with_a_sentence_naming_the_rule(app):
    assert "filter[0].field" in refused(app.list, "book", filter=[{"field": "x", "op": "eq"}])
    assert "filter[0].op" in refused(app.list, "book", filter=[{"field": "title", "op": "like",
                                                               "value": "a"}])
    assert "filter[0].value\" is missing" in refused(
        app.list, "book", filter=[{"field": "title", "op": "eq"}])
    assert "list of at most" in refused(app.list, "book", filter="stars")
    assert "must be a rule" in refused(app.list, "book", filter=[{"field": "title", "op": "eq",
                                                                "value": 1, "x": 2}])


def test_a_search_finds_text_in_any_text_or_choice_field_ignoring_case(app):
    seed(app)
    assert titles(app.list("book", search="ULY")) == ["Ulysses"]
    assert set(titles(app.list("book", search="read"))) == {"Ulysses", "Beloved"}
    assert "search" in refused(app.list, "book", search=5)


def test_paging_walks_every_record_once_in_order_even_when_sorted_on_a_tied_field(app):
    seed(app)
    seen, after = [], None
    while True:
        page = app.list("book", sort=["-stars"], limit=2, after=after)
        seen += titles(page)
        assert page["total"] == 5
        after = page["next"]
        if after is None:
            break
    assert seen == titles(app.list("book", sort=["-stars"]))
    assert len(seen) == len(set(seen)) == 5


def test_paging_survives_the_last_record_of_a_page_being_deleted(app):
    seed(app)
    page = app.list("book", limit=2)
    app.delete("book", page["records"][-1]["id"])
    rest = app.list("book", limit=2, after=page["next"])
    assert titles(rest) == ["Ulysses", "Beloved"]


def test_a_page_size_must_be_between_one_and_the_maximum(app):
    assert str(MAX_LIMIT) in refused(app.list, "book", limit=MAX_LIMIT + 1)
    assert "limit" in refused(app.list, "book", limit=0)
    assert "limit" in refused(app.list, "book", limit="5")


@pytest.mark.parametrize("cursor", ["", "!!!", "e30", "eyJpZCI6MX0", 5,
                                    "eyJpZCI6ICJ4IiwgImsiOiBbXX0",
                                    "eyJpZCI6IDEsICJrIjogW3t9XX0"])
def test_a_forged_page_marker_is_refused_not_trusted(app, cursor):
    seed(app)
    assert "not a place this list gave you" in refused(app.list, "book", after=cursor)


# --- views and totals ------------------------------------------------------------------------


def test_a_view_brings_its_sort_filter_and_totals_over_all_matches_not_just_the_page(app):
    seed(app)
    page = app.list("book", view="all", limit=2)
    assert titles(page) == ["Emma", "Beloved"]
    assert page["totals"] == {"stars": 4.0, "paid": 2500}
    top = app.list("book", view="top")
    assert top["total"] == 3 and top["totals"] == {"pages": 900}


def test_a_view_that_does_not_exist_or_belongs_to_another_table_is_refused(app):
    assert "no view called \"nope\"" in refused(app.list, "book", view="nope")
    assert "no view" in refused(app.list, "author", view="all")


def test_totals_work_over_filled_values_only_and_say_nothing_where_they_cannot():
    assert total("sum", {"type": "int"}, [1, 2, None]) == 3
    assert total("avg", {"type": "int"}, [1, 2, None]) == 1.5
    assert total("avg", {"type": "money"}, [1, 2]) == 2
    assert total("sum", {"type": "number"}, [0.1, 0.2]) == 0.3
    assert total("min", {"type": "date"}, ["2030-02-01", "2030-01-01"]) == "2030-01-01"
    assert total("count", {"type": "text"}, ["a", None, "b"]) == 2
    assert total("sum", {"type": "text"}, ["a"]) is None
    assert total("max", {"type": "int"}, []) is None


def test_a_grouped_view_reports_each_group_in_option_order_with_empty_ones_and_none_last(app):
    seed(app)
    groups = app.list("book", view="top")["groups"]
    assert [(g["value"], g["count"]) for g in groups] == [
        ("to read", 1), ("reading", 0), ("finished", 1), (None, 1)]
    assert groups[0]["totals"] == {"pages": 350}


# --- computed fields -------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "values", "result"), [
    ("price * count", {"price": 3, "count": 4}, 12),
    ("a + b * c", {"a": 1, "b": 2, "c": 3}, 7),
    ("(a + b) * c", {"a": 1, "b": 2, "c": 3}, 9),
    ("-a + 10", {"a": 4}, 6),
    ("a / b", {"a": 1, "b": 4}, 0.25),
    ("a / b", {"a": 1, "b": 0}, None),
    ("a * 2", {"a": None}, None),
    ("a - 1.5", {"a": 4}, 2.5),
])
def test_a_formula_is_arithmetic_over_the_records_numbers(text, values, result):
    assert Formula(text).evaluate(values) == result


@pytest.mark.parametrize("text", [
    "", "a +", "(a", "a b", "a ** 2", "__import__('os')", "a.b", "len(a)", "a; b", "1e5",
    "(" * 30 + "a" + ")" * 30, "a % 2", "a == b",
])
def test_a_formula_that_is_more_than_arithmetic_is_not_run(text):
    with pytest.raises(ValueError):
        Formula(text)


def test_a_formula_naming_a_field_that_is_not_a_number_is_reported_before_the_app_runs():
    tables = {"t": {"fields": {"n": {"type": "text"}, "k": {"type": "int"},
                               "c": {"type": "computed", "formula": "n * k"}}}}
    (problem,) = check(tables)
    assert "tables.t.fields.c.formula" in problem and "\"n\"" in problem
    with pytest.raises(DescribedError):
        Described(tables, [], MemoryStore())


def test_a_computed_field_is_worked_out_on_every_read_and_never_stored(app):
    rec = app.create("book", {"title": "a", "paid": 1000, "pages": 400})
    assert values(rec)["per_page"] == 2.5
    assert "per_page" not in app.store.get("book", 1).data
    assert values(app.update("book", 1, {"pages": 200}))["per_page"] == 5
    assert values(app.create("book", {"title": "b"}))["per_page"] is None


# --- the bridge's door -----------------------------------------------------------------------


def test_the_bridge_calls_reach_each_operation_and_nothing_else(app):
    made = app.handle("records.create", {"table": "book", "values": {"title": "a"}})
    rid = made["record"]["id"]
    assert app.handle("records.get", {"table": "book", "id": rid})["record"]["label"] == "a"
    app.handle("records.update", {"table": "book", "id": rid, "values": {"stars": 2}})
    assert app.handle("records.list", {"table": "book", "limit": 5})["total"] == 1
    assert app.handle("records.delete", {"table": "book", "id": rid})["deleted"] == rid
    assert "not a records call" in refused(app.handle, "storage.get", {})
    assert set(CALLS) == {"records.list", "records.get", "records.create", "records.update",
                          "records.delete"}
    assert "object" in refused(app.handle, "records.list", [])
    assert "unlink" in refused(app.handle, "records.delete", {"table": "book", "id": 1,
                                                              "unlink": "yes"})


def test_a_record_label_is_the_first_text_field_clipped_or_the_id_when_there_is_none(app):
    long = app.create("book", {"title": "word " * 100})["record"]
    assert len(long["label"]) <= 120 and long["label"].endswith("…")
    notitle = Described({"t": {"fields": {"n": {"type": "int"}}}}, [], MemoryStore())
    assert notitle.create("t", {})["record"]["label"] == "#1"
    assert notitle.create("t", {"n": 7})["record"]["label"] == "7"


def test_text_with_markup_is_kept_exactly_as_written_and_never_changed_or_run(app):
    spoof = "<img src=x onerror=alert(1)><script>1</script> & \"quoted\""
    rec = app.create("book", {"title": spoof})["record"]
    assert rec["values"]["title"] == spoof and rec["label"] == spoof


# --- the examples ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["books_read", "chores"])
def test_each_described_example_loads_through_the_parser_and_runs(name):
    manifest = load(EXAMPLES / name)
    assert manifest.described and manifest.tier == "sandboxed" and not manifest.permissions
    app = Described.for_manifest(manifest, MemoryStore())
    for view in manifest.views:
        result = app.list(view["table"], view=view["id"])
        assert result["total"] == 0 and result["next"] is None
    assert not check(manifest.tables)


def test_the_books_example_keeps_a_book_with_its_author_and_totals_it():
    app = Described.for_manifest(load(EXAMPLES / "books_read"), MemoryStore())
    author = app.create("author", {"name": "Avery Example", "born": 1929})["record"]["id"]
    app.create("book", {"title": "A", "author": author, "status": "finished", "stars": 5,
                        "pages": 200, "paid": 1000, "finished": "2030-01-01"})
    app.create("book", {"title": "B", "stars": 3})
    result = app.list("book", view="all")
    assert result["totals"] == {"stars": 4.0, "pages": 200, "paid": 1000}
    assert result["records"][0]["values"]["per_page"] == 5
    assert app.list("book", view="favourites")["totals"] == {"stars": 1}
    assert [g["value"] for g in app.list("book", view="shelf")["groups"]] == [
        "to read", "reading", "finished", None]


def test_the_chores_example_has_a_board_grouped_by_a_choice_field():
    manifest = load(EXAMPLES / "chores")
    board = next(v for v in manifest.views if v["id"] == "board")
    assert manifest.tables["chore"]["fields"][board["group"]]["type"] == "choice"
    app = Described.for_manifest(manifest, MemoryStore())
    app.create("chore", {"title": "Sweep", "state": "doing", "minutes": 20})
    app.create("chore", {"title": "Bins", "state": "done", "minutes": 5})
    result = app.list("chore", view="board")
    assert [(g["value"], g["count"]) for g in result["groups"]] == [
        ("to do", 0), ("doing", 1), ("done", 1)]
    assert app.list("chore", view="open")["total"] == 1
    assert "\"State\" is needed" in refused(app.create, "chore", {"title": "x"})


def test_the_examples_contain_nothing_personal_and_use_only_what_the_manifest_defines():
    for name in ("books_read", "chores"):
        text = (EXAMPLES / name / mf.MANIFEST_FILE).read_text(encoding="utf-8")
        assert "@" not in text and "/Users/" not in text
