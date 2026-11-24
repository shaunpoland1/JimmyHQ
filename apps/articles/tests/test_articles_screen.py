"""The Articles screen: the calls it draws from, the evidence it lights up, and the calls
its page makes."""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from articles.program import READS, calls
from articles.store import Store
from articles_rig import APP_ROOT, GOOD_BODY, finding

WEB = APP_ROOT / "web"
PASTE = ("I would love to read about saving money with a small plot and the pear tree "
         "squash.\n---\nPlease write about compost for clay soil, it never works for me.")


def sentence_with(view: dict, words: str) -> dict:
    for block in view["blocks"]:
        for sentence in block["sentences"]:
            if words in sentence["text"]:
                return sentence
    raise AssertionError(f"no sentence says {words!r}")


def check_of(view: dict, check_id: int) -> dict:
    return next(c for c in view["checks"] if c["id"] == check_id)


def marked(sentence: dict, kind: str) -> list[str]:
    return [sentence["text"][m["start"]:m["end"]] for m in sentence["marks"] if m["kind"] == kind]


# --- the overview ---------------------------------------------------------------------------


def test_a_first_run_with_no_sites_says_how_to_add_one(rig):
    seen = rig.core.call("overview", {})
    assert seen["sites"] == [] and seen["pieces"] == []
    assert "Add one under Sites" in seen["hint"]
    assert {p["id"] for p in seen["publishers"]} == {"practice", "by_hand", "wordpress"}


def test_the_overview_counts_an_articles_open_questions_and_warnings(make_rig):
    rig = make_rig(settings={"repair_passes": 0})
    piece = rig.to_card(body=GOOD_BODY + "<p>Plots saved families 77% of their food bill "
                        "[S1:1].</p>")
    listed = next(p for p in rig.core.call("overview", {})["pieces"] if p["id"] == piece["id"])
    checks = rig.store.checks(rig.store.revision(piece["id"])["id"])
    assert listed["questions"] >= 1  # 77% is in no source
    assert listed["questions"] == sum(1 for c in checks if c["level"] == "ask" and not c["answer"])
    assert listed["warnings"] == sum(1 for c in checks if c["level"] == "warn")
    assert listed["title"] == "What a garden plot really saves" and listed["sources"] == 4
    assert listed["words"] == "waiting for you"
    ask = next(c for c in checks if c["level"] == "ask")
    rig.core.call("check.answer", {"id": ask["id"], "answer": "yes"})
    again = next(p for p in rig.core.call("overview", {})["pieces"] if p["id"] == piece["id"])
    assert again["questions"] == listed["questions"] - 1


def test_an_article_held_as_close_to_an_earlier_one_says_how_close_in_words(rig):
    first = rig.to_card()
    again = rig.topic(rig.store.site(first["site_id"]))
    rig.advance(1)
    held = next(p for p in rig.core.call("overview", {})["pieces"] if p["id"] == again["id"])
    assert held["state"] == "held_coverage" and held["close"]["close"] is True
    assert "like \"What a garden plot really saves\"" in held["close"]["said"]
    assert "coverage" not in held


def test_the_overview_says_where_each_site_publishes_and_whether_it_sends_out(rig):
    rig.site()
    rig.site(name="Plot news", publisher="wordpress", wordpress_url="https://blog.example.com")
    found = {s["name"]: s for s in rig.core.call("overview", {})["sites"]}
    assert found["Garden notes"]["publisher_label"] == "Practice"
    assert found["Garden notes"]["sends_out"] is False
    assert found["Plot news"]["sends_out"] is True


def test_readers_replies_reach_the_overview_only_as_counts(rig):
    rig.core.call("replies.paste", {"text": PASTE, "audience": "parents"})
    seen = rig.core.call("overview", {})
    assert seen["replies"]["replies"] == 2 and seen["replies"]["unread"] == 2
    assert "does not take out names" in seen["replies"]["hint"].replace("It does", "does")
    said = json.dumps(seen)
    assert "squash" not in said and "clay soil" not in said
    assert seen["ideas_hosted"] is False


def test_the_overview_gives_the_decline_reasons_from_the_settings(make_rig):
    rig = make_rig(settings={"decline_reasons": ["Off topic", " ", "Other"]})
    assert rig.core.call("overview", {})["decline_reasons"] == ["Off topic", "Other"]


def test_the_overview_says_which_addresses_the_owner_gave_and_drops_one_he_removed(rig):
    assert rig.core.call("overview", {})["hosts"] == []
    rig.core.grant_host("blog.example.com")
    assert rig.core.call("overview", {})["hosts"] == ["blog.example.com"]
    assert rig.core.call("sites.hosts", {}) == {"hosts": ["blog.example.com"]}
    rig.core.remove_host("blog.example.com")
    assert rig.core.call("overview", {})["hosts"] == []


def test_the_overview_says_header_images_are_off_until_the_owner_switches_them_on(make_rig):
    assert make_rig().core.call("overview", {})["header_images"] is False
    assert make_rig(settings={"header_images": True}).core.call("overview", {})[
        "header_images"] is True


# --- one article and its evidence -----------------------------------------------------------


def test_a_number_in_the_article_points_at_the_source_sentence_it_was_found_in(rig):
    piece = rig.to_card()
    seen = rig.core.call("article.view", {"id": piece["id"]})
    sentence = sentence_with(seen, "18% less")
    assert sentence["cites"] == [[1, 1]]
    assert marked(sentence, "number") == ["18%", "1,200"]
    for mark in sentence["marks"]:
        result = check_of(seen, mark["check"])
        assert result["name"] == "numbers" and result["level"] == "ok"
        assert result["evidence"] == ["S1:1"]


def test_marks_are_found_across_several_sources_in_one_paragraph(rig):
    piece = rig.to_card()
    seen = rig.core.call("article.view", {"id": piece["id"]})
    compost = sentence_with(seen, "yields rise by 9%")
    assert compost["cites"] == [[2, 2]]
    evidence = {e for m in compost["marks"] for e in check_of(seen, m["check"])["evidence"]}
    assert evidence == {"S2:2"}
    plots = sentence_with(seen, "41 kilograms")
    assert plots["cites"] == [[1, 4]]


def test_a_number_no_source_has_is_marked_with_its_question(rig):
    piece = rig.to_card(body=GOOD_BODY + "<p>Plots saved families 77% of their food bill "
                        "[S1:1].</p>")
    seen = rig.core.call("article.view", {"id": piece["id"]})
    sentence = sentence_with(seen, "77%")
    [mark] = [m for m in sentence["marks"] if sentence["text"][m["start"]:m["end"]] == "77%"]
    result = check_of(seen, mark["check"])
    assert result["level"] == "ask" and "It is in no sentence of any source" in result["detail"]


def test_a_quotation_is_marked_with_the_check_that_found_it_word_for_word(rig):
    body = ("<p>Dr. Rivera said: “Most plots paid for their seeds within six weeks.” "
            "[S1:3]</p>")
    piece = rig.to_card(body=body)
    seen = rig.core.call("article.view", {"id": piece["id"]})
    sentence = sentence_with(seen, "Most plots paid")
    assert len(marked(sentence, "quote")) == 1
    assert marked(sentence, "number") == []  # "six" is a word; nothing inside is marked twice
    [mark] = [m for m in sentence["marks"] if m["kind"] == "quote"]
    assert check_of(seen, mark["check"])["name"] == "quotes"
    assert check_of(seen, mark["check"])["level"] == "ok"


def test_a_sentence_that_states_a_fact_and_cites_nothing_carries_its_question(rig):
    piece = rig.to_card(body=GOOD_BODY + "<p>Researchers found that plots double "
                        "happiness.</p>")
    seen = rig.core.call("article.view", {"id": piece["id"]})
    sentence = sentence_with(seen, "double happiness")
    assert sentence["cites"] == []
    names = [check_of(seen, c)["name"] for c in sentence["checks"]]
    assert "uncited" in names


def test_a_review_finding_sits_on_the_sentence_it_quotes(rig):
    piece = rig.to_card(review={"findings": [finding("Compost helped too")]})
    rig.store.move(piece["id"], "ready")  # as the repair passes would leave it
    seen = rig.core.call("article.view", {"id": piece["id"]})
    sentence = sentence_with(seen, "Compost helped too")
    assert [check_of(seen, c)["name"] for c in sentence["checks"]] == ["review"]


def test_the_view_shows_the_research_the_outline_and_every_step(rig):
    piece = rig.to_card()
    seen = rig.core.call("article.view", {"id": piece["id"]})
    assert [s["n"] for s in seen["sources"]] == [1, 2, 3, 4]
    assert all(s["why"] for s in seen["sources"]) and seen["sources"][3]["weak"] is True
    assert any(d["host"] == "news.example.com" and d["why"] for d in seen["left_out"])
    assert seen["summary"][0]["cites"] == [[1, 1]]
    assert seen["outline"]["sections"][0]["heading"] == "The savings"
    assert [t["step"] for t in seen["turns"]][:2] == ["started", "coverage_check"]
    assert seen["revision"]["n"] == 1 and seen["revision"]["words"] > 20
    assert seen["blocks"][0]["tag"] == "h1"
    assert seen["said"].endswith(".")


def test_text_from_a_topic_comes_back_as_it_was_never_changed_into_markup(rig):
    site = rig.site()
    hostile = '<img src=x onerror="alert(1)"> plots & "quotes"'
    piece = rig.topic(site, topic=hostile)
    seen = rig.core.call("article.view", {"id": piece["id"]})
    assert seen["piece"]["topic"] == " ".join(hostile.split())
    assert seen["blocks"] == [] and seen["revision"] is None


def test_an_article_that_is_not_there_is_refused_with_a_sentence(rig):
    with pytest.raises(Exception, match="There is no such article"):
        rig.core.call("article.view", {"id": 999})


EVIDENCE_BODY = (GOOD_BODY + "<p>Plots saved families 77% of their food bill [S1:1].</p>"
                 "<ul><li>Researchers found that plots double happiness.</li>"
                 "<li>Families ate vegetables on five days a week [S3:3].</li></ul>")
FIXTURE = APP_ROOT / "tests" / "fixtures" / "evidence.json"


def fixture_of(view: dict) -> dict:
    """What the page's own tests read: the evidence blocks and the checks they point at."""
    keep = ("id", "name", "level", "detail", "evidence", "answer")
    return {"blocks": view["blocks"], "checks": [{k: c[k] for k in keep} for c in view["checks"]]}


def test_the_evidence_the_pages_tests_read_is_what_the_program_sends(make_rig):
    # web/tests/articles.evidence.test.ts reads the same file, so the two cannot drift.
    rig = make_rig(settings={"repair_passes": 0})
    piece = rig.to_card(body=EVIDENCE_BODY)
    seen = fixture_of(rig.core.call("article.view", {"id": piece["id"]}))
    assert seen == json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_source_sentences_come_in_parts_no_larger_than_the_setting(make_rig):
    rig = make_rig(settings={"screen_answer_kb": 64})
    piece = rig.to_card()
    long = [f"Sentence {i} of a long invented page about plots and their yields." for i in
            range(1500)]
    first = rig.store.sources(piece["id"])[0]
    rig.store.update("source", first["id"], {"sentences": long})
    parts, after = [], 0
    while after is not None:
        got = rig.core.call("article.sources", {"id": piece["id"], "after": after})
        assert len(json.dumps(got).encode("utf-8")) <= 64 * 1024 + 512
        parts += got["sources"]
        after = got["next"]
    assert [p["n"] for p in parts] == [1, 2, 3, 4]
    assert parts[0]["cut"] is True and 0 < len(parts[0]["sentences"]) < 1500
    assert parts[1]["sentences"] == rig.store.sources(piece["id"])[1]["sentences"]


# --- announcing changes ---------------------------------------------------------------------


def test_every_call_that_changes_a_record_tells_the_open_pages(rig):
    site = rig.site()
    before = len(rig.core.emitted)
    rig.core.call("sites.save", {**site, "register": "Plain."})
    assert len(rig.core.emitted) == before + 1
    assert rig.core.emitted[-1]["event"] == "articles.changed"
    piece = rig.to_card(rig.store.site(site["id"]))
    before = len(rig.core.emitted)
    rig.core.call("article.approve", {"id": piece["id"]})
    assert len(rig.core.emitted) > before


def test_a_call_that_only_reads_or_is_refused_tells_nobody(rig):
    rig.site()
    before = len(rig.core.emitted)
    for name in ("overview", "sites.list", "ideas.list", "replies.status"):
        rig.core.call(name, {})
    with pytest.raises(Exception, match="Give the site a name"):
        rig.core.call("sites.save", {"name": ""})
    assert len(rig.core.emitted) == before


def test_forgetting_the_replies_tells_the_open_pages(rig):
    rig.core.call("replies.paste", {"text": PASTE})
    before = len(rig.core.emitted)
    rig.core.call("replies.forget", {})
    assert len(rig.core.emitted) == before + 1


def test_the_calls_left_unannounced_are_exactly_the_ones_that_only_read(rig):
    known = set(calls(rig.ctx))
    assert READS <= known
    assert {"sites.save", "article.edit", "check.answer", "replies.forget",
            "article.clear"}.isdisjoint(READS)


# --- sites ----------------------------------------------------------------------------------


def test_a_site_keeps_its_time_zone_and_an_unknown_one_is_refused(rig):
    site = rig.site(timezone="Europe/Lisbon")
    assert site["timezone"] == "Europe/Lisbon"
    assert rig.site(name="Second", timezone="")["timezone"] == ""
    with pytest.raises(Exception, match="does not know the time zone"):
        rig.site(name="Third", timezone="Mars/Olympus")


def test_a_database_made_before_sites_had_a_time_zone_gains_one(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE site (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                 "created INTEGER NOT NULL, updated INTEGER NOT NULL)")
    conn.execute("INSERT INTO site (name, created, updated) VALUES ('Old', 1, 1)")
    conn.commit()
    store = Store(conn, lambda: 2)
    try:
        assert store.sites()[0]["timezone"] == ""
    finally:
        store.close()


# --- the page -------------------------------------------------------------------------------


def test_every_call_the_page_makes_is_one_the_program_answers(rig):
    asked = set()
    for path in WEB.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        asked |= set(re.findall(r"""call\(\s*["']([a-z_.]+)["']""", text))
    assert {"overview", "article.view", "article.sources", "article.edit"} <= asked
    assert asked - set(calls(rig.ctx)) == set()


def test_the_page_never_asks_for_or_sends_a_wordpress_user_name_or_password():
    for path in WEB.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for banned in ("wordpress_user", "wordpress_password", "type: \"password\""):
            assert banned not in text, f"{path.name} has {banned}"


def test_the_page_uses_the_publishing_calls_for_hosts_sent_edits_and_header_images(rig):
    asked = set()
    for path in WEB.rglob("*.js"):
        asked |= set(re.findall(r"""call\(\s*["']([a-z_.]+)["']""",
                                path.read_text(encoding="utf-8")))
    assert {"sites.hosts", "article.edit_sent", "header.make", "header.get",
            "header.style.get", "header.style.set"} <= asked
    assert {"sites.hosts", "header.get", "header.style.get"} <= READS


def test_the_page_never_writes_html_from_text_and_never_uses_the_browsers_dialogs():
    for path in WEB.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                       "alert(", "confirm(", "prompt(", "eval(", "new Function",
                       "DOMParser", "fetch(", "XMLHttpRequest"):
            assert banned not in text, f"{path.name} uses {banned}"


# --- what the page's words promise --------------------------------------------------------------

# Every button a sentence may tell the owner to press, as the page words it.
BUTTONS = ("Approve", "Send back", "Decline", "Drop", "Retry", "Write anyway", "Publish",
           "Published", "It is there: Published", "I checked, it is not there",
           "Keep these replies", "Find ideas", "Delete every reply", "Start", "Save",
           "Fill in from the website")
VIEWS = ("Today", "Articles", "Publish", "Ideas from readers", "New", "Sites")


def page_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(WEB.rglob("*.js")))


def program_text() -> str:
    return "\n".join(re.sub(r'"\s*\n\s*f?"', "", p.read_text(encoding="utf-8"))
                     for p in sorted((APP_ROOT / "articles").rglob("*.py")))


def test_every_button_a_sentence_can_name_is_a_button_of_the_page():
    text = page_text()
    for label in BUTTONS:
        assert re.search(r'[,(?:]\s*"' + re.escape(label) + r'"\s*[),:]', text) \
            or f"`{label} " in text, f"the page has no {label} button"
    listed = re.findall(r'\["[a-z]+", "([^"]+)", \w+\.render\]', text)
    assert tuple(listed) == VIEWS


def test_every_way_out_names_only_buttons_the_page_has():
    from articles import states  # noqa: PLC0415

    for state, words in states.WAYS_OUT.items():
        left = words
        for label in sorted(BUTTONS, key=len, reverse=True):
            left = left.replace(label, "")
        named = re.findall(r"\b[A-Z][a-z]+", left)
        assert not named, f"{state}: {words!r} names {named}, which is not a button"


def test_every_sentence_that_says_press_names_a_button_the_page_has():
    for text in (program_text(), page_text()):
        for after in re.findall(r'\b[Pp]ress(?:ed)? \\?"?([A-Z][^.;\n]{0,40})', text):
            if after.startswith(("Ctrl", "Cmd")):
                continue
            assert after.startswith(BUTTONS), f"“press {after}” names no button of the page"


def test_every_place_a_sentence_sends_the_owner_to_is_a_view_of_the_page():
    views = {"today", "articles", "publish", "ideas", "new", "sites"}
    text = page_text()
    assert set(re.findall(r'go\("([a-z]+)"', text)) <= views
    assert set(re.findall(r'view: "([a-z]+)"', text)) - {"today"} <= views
    for said in re.findall(r"\b(?:under|on) (the site's page|Sites|New|Publish|Today)\b",
                           program_text() + text):
        assert said in VIEWS, f"a sentence sends the owner to {said!r}, which is not a view"


def test_the_page_offers_every_call_an_owner_needs_to_finish_or_undo_something():
    asked = set(re.findall(r"""["']([a-z_]+\.[a-z_.]+)["']""", page_text()))
    assert {"sites.save", "piece.retry", "piece.drop", "piece.write_anyway", "check.answer",
            "article.publish", "article.clear", "article.published", "article.export",
            "replies.forget", "idea.start", "idea.dismiss"} <= asked


def test_the_replies_hint_says_they_can_be_deleted_and_that_ideas_stay(rig):
    hint = rig.core.call("replies.status")["hint"]
    assert "Delete every reply" in hint and "for good" in hint
    assert "ideas already found stay" in hint


def test_deleting_the_replies_keeps_the_ideas_already_found(rig):
    rig.core.call("replies.paste", {"text": PASTE})
    db = rig.ctx.replies.db
    db.execute("INSERT INTO idea (created, title, reply_ids) VALUES (1, 'Compost for clay', "
               "'[1]')")
    db.commit()
    gone = rig.core.call("replies.forget")
    assert gone["deleted"] == 2 and gone["replies"] == 0
    assert [i["title"] for i in rig.core.call("ideas.list")["ideas"]] == ["Compost for clay"]


def test_single_letter_keys_are_on_until_the_owner_switches_them_off(make_rig):
    assert make_rig().core.call("overview", {})["single_keys"] is True
    off = make_rig(settings={"single_keys": False})
    assert off.core.call("overview", {})["single_keys"] is False


def test_the_keys_setting_names_the_letters_the_page_uses():
    manifest = json.loads((APP_ROOT / "jimmyhq.app.json").read_text(encoding="utf-8"))
    setting = next(s for s in manifest["settings"] if s["key"] == "single_keys")
    assert setting["type"] == "bool" and setting["default"] is True
    keys = (WEB / "lib" / "keys.js").read_text(encoding="utf-8")
    line = keys.split("const LETTERS")[1].split("\n")[0]
    letters = dict(re.findall(r'\b([a-z]): "([a-z_]+)"', line))
    assert letters == {"j": "next", "k": "previous", "a": "approve", "e": "edit",
                       "s": "send_back", "d": "decline"}
    assert "Single-letter keys" in keys and setting["label"] == "Single-letter keys"
