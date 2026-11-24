"""The tools Articles offers anyone who holds no role: an agent with no work from Articles
and an outside client the owner let through the door. They only read, are bounded, never
name a site's address, sign-in or secret, never give sources or notes, and count against
no piece's budget. Sample data is invented and every address is on example.com."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from articles import outside, states
from articles_rig import START

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
CLIENT = "client:test"
LONELY = "agent:nobody"  # a real agent that holds no role of Articles
HOUR = 3_600_000
HIDDEN = "pw-test-0000-secret-value"
BODY = ("<h2>Why plots help</h2><p>Plots cut food bills by a third [S1:2].</p>"
        "<ul><li>Start small [S2:1]</li></ul><p>Share the harvest.</p>")


@pytest.fixture
def articles(make_rig):
    rig = make_rig(secrets={"wordpress_password": HIDDEN, "wordpress_user": "writer-77"})
    rig.garden = rig.site(publisher="practice")
    rig.store.update("site", rig.garden["id"], {"wordpress_url": "https://blog.example.com",
                                                "wordpress_user": "writer-77"})
    rig.shop = rig.site(name="Shop notes", audience="Shoppers", register="Brisk and direct.")
    return rig


def put(rig, site, state, topic, *, title=None, body=BODY, at=0, **more):
    piece = rig.store.insert("piece", {
        "site_id": site["id"], "way": "topic", "topic": topic, "state": state,
        "notes": "private owner note", "owner_note": "private send-back note",
        "revision": 1 if title else 0, "created": START, "updated": START + at, **more})
    if title:
        rig.store.insert("revision", {"piece_id": piece, "n": 1, "kind": "draft",
                                      "title": title, "body": body, "created": START})
    rig.store.insert("source", {"piece_id": piece, "n": 1, "url": "https://src.example.com/a",
                                "title": "A source", "text": "SOURCE TEXT", "kept": 1,
                                "why": "why", "created": START})
    return piece


def call(rig, name, args=None, agent=CLIENT, role=None):
    return rig.core.tool(name, args or {}, agent=agent, role=role)


def dump(said) -> str:
    return json.dumps(said, ensure_ascii=False)


def test_sites_gives_each_sites_name_audience_and_voice_and_nothing_about_publishing(articles):
    said = call(articles, "sites")
    shown = said["shown"]
    assert said["ok"] and said["result"] == "2 sites."
    assert "Garden notes" in shown and "Writes for: People new to growing food" in shown
    assert "Sounds like: Warm and plain." in shown and "Shop notes" in shown
    for forbidden in ("blog.example.com", "writer-77", HIDDEN, "wordpress"):
        assert forbidden not in dump(said).lower()


def test_articles_lists_title_site_state_in_words_and_when_newest_first(articles):
    put(articles, articles.garden, "approved", "Plots and bills", title="Why plots help",
        at=HOUR)
    put(articles, articles.shop, "sent", "Spades on sale", title="Spades on sale",
        at=2 * HOUR)
    said = call(articles, "articles")
    shown = said["shown"]
    assert said["ok"] and said["result"] == "2 articles."
    assert shown.index("Spades on sale") < shown.index("Why plots help")
    assert "Site: Garden notes. approved. Updated 2027-01-15 09:00 UTC" in shown
    assert "Site: Shop notes. sent. Updated 2027-01-15 10:00 UTC" in shown


def test_only_articles_the_owner_approved_or_that_went_further_are_listed_or_given(articles):
    given = {state: put(articles, articles.garden, state, f"Topic {state}",
                        title=f"Title {state}") for state in sorted(states.APPROVED)}
    held = {state: put(articles, articles.garden, state, f"Topic {state}",
                       title=f"Title {state}")
            for state in sorted(set(states.PIECE) - states.APPROVED)}
    assert {"ready", "drafting", "reviewing", "revising", "declined", "dropped"} <= set(held)
    shown = call(articles, "articles", {"limit": 25})["shown"]
    for state, number in given.items():
        assert f"Title {state}" in shown, state
        assert call(articles, "article", {"article": number})["ok"], state
    missing = call(articles, "article", {"article": 9999})["shown"].replace("9999", "N")
    for state, number in held.items():
        assert f"Topic {state}" not in shown and f"Title {state}" not in shown, state
        said = call(articles, "article", {"article": number})
        assert said["ok"] is False, state
        assert said["shown"].replace(str(number), "N") == missing, state


def test_articles_leaves_out_dropped_ones_and_can_be_narrowed_to_one_site(articles):
    put(articles, articles.garden, "dropped", "Thrown away", title="Thrown away title")
    put(articles, articles.garden, "published", "Out", title="Garden one")
    put(articles, articles.shop, "approved", "Shop topic", title="Shop one")
    shown = call(articles, "articles")["shown"]
    assert "Thrown away" not in shown and "Garden one" in shown and "Shop one" in shown
    only = call(articles, "articles", {"site": articles.shop["id"]})
    assert "Shop one" in only["shown"] and "Garden one" not in only["shown"]
    assert only["result"] == "1 articles."


def test_article_gives_the_current_text_as_numbered_lines_without_citation_marks(articles):
    number = put(articles, articles.garden, "approved", "Plots and bills", title="Why plots help")
    said = call(articles, "article", {"article": number})
    shown = said["shown"]
    assert said["ok"]
    assert "1. Title: Why plots help" in shown and "2. ## Why plots help" in shown
    assert "3. Plots cut food bills by a third." in shown and "4. - Start small" in shown
    assert "[S1" not in shown and "the end" in shown and "Written by a model" in shown


def test_article_gives_the_current_revision_not_an_older_one(articles):
    number = put(articles, articles.garden, "approved", "Topic", title="First title",
                 body="<p>Old words.</p>")
    articles.store.insert("revision", {"piece_id": number, "n": 2, "kind": "edit",
                                       "title": "Second title", "body": "<p>New words.</p>",
                                       "created": START})
    articles.store.update("piece", number, {"revision": 2})
    shown = call(articles, "article", {"article": number})["shown"]
    assert "New words." in shown and "Old words." not in shown


def test_article_is_cut_to_a_page_and_says_how_to_read_on(articles):
    body = "".join(f"<p>Paragraph {n} " + "word " * 120 + "</p>" for n in range(1, 40))
    number = put(articles, articles.garden, "approved", "Long", title="Long one", body=body)
    said = call(articles, "article", {"article": number})
    assert len(said["shown"]) <= int(articles.ctx.cfg().tool_page_chars)
    assert "ask again with start=" in said["shown"]
    start = int(said["shown"].rsplit("start=", 1)[1].split(" ")[0])
    assert f"{start}. Paragraph {start - 1} " in call(articles, "article", {
        "article": number, "start": start})["shown"]


def test_article_with_nothing_written_says_so_and_a_dropped_or_missing_one_is_refused(
        articles):
    unwritten = put(articles, articles.garden, "approved", "Not yet")
    said = call(articles, "article", {"article": unwritten})
    assert said["ok"] and "no text yet" in said["result"]
    dropped = put(articles, articles.garden, "dropped", "Gone", title="Gone title")
    for number in (dropped, 9999):
        said = call(articles, "article", {"article": number})
        assert said["ok"] is False and "Nothing was read" in said["result"]


def test_with_nothing_yet_each_tool_says_so_in_words(make_rig):
    rig = make_rig()
    assert "no sites yet" in call(rig, "sites")["shown"]
    assert "no approved articles yet" in call(rig, "articles")["shown"]


def test_a_list_holds_at_most_the_limit_and_says_how_many_were_left_out(articles):
    for n in range(8):
        put(articles, articles.garden, "approved", f"Topic {n}", title=f"Title {n}", at=n)
    said = call(articles, "articles", {"limit": 3})
    assert said["shown"].count("- Article ") == 3
    assert "5 more not shown here: 3 of 8" in said["shown"]
    assert call(articles, "articles")["shown"].count("- Article ") == 8


def test_a_long_list_is_cut_to_what_one_answer_may_hold_and_says_so(articles):
    for n in range(outside.MOST + 5):
        put(articles, articles.garden, "approved", "t", title=f"{n} " + "title " * 40, at=n)
    said = call(articles, "articles", {"limit": outside.MOST})
    assert len(said["shown"]) <= int(articles.ctx.cfg().tool_page_chars)
    assert "more not shown here" in said["shown"]


@pytest.mark.parametrize("name, args", [
    ("sites", {"limit": 0}), ("sites", {"limit": 26}), ("articles", {"limit": "ten"}),
    ("articles", {"limit": True}), ("articles", {"site": "x"}), ("articles", {"site": 999}),
    ("article", {}), ("article", {"article": "x"}), ("article", {"article": 1, "start": 0}),
])
def test_an_argument_that_does_not_fit_is_refused_plainly_and_nothing_is_read(
        articles, name, args):
    put(articles, articles.garden, "approved", "t", title="A title")
    said = call(articles, name, args)
    assert said["ok"] is False and "Nothing was read" in said["result"]


def test_no_address_sign_in_source_note_or_internal_field_appears_in_anything_said(articles):
    number = put(articles, articles.garden, "approved", "Plots and bills", title="Why plots help",
                 card_id="card-secret-id")
    articles.store.insert("agent_review", {"piece_id": number, "revision_id": 1,
                                           "agent": "agent:checker-9", "verdict": "ship",
                                           "created": START})
    text = " ".join(dump(call(articles, name, args)) for name, args in (
        ("sites", {"limit": 25}), ("articles", {"limit": 25}), ("article", {"article": number})))
    for forbidden in (HIDDEN, "writer-77", "blog.example.com", "wordpress",
                      "private owner note", "private send-back note", "SOURCE TEXT",
                      "src.example.com", "card-secret-id", "checker-9"):
        assert forbidden not in text, forbidden


def test_the_tools_are_the_same_for_a_client_and_an_agent_with_no_role(articles):
    number = put(articles, articles.garden, "approved", "t", title="A title")
    for name, args in (("sites", {}), ("articles", {}), ("article", {"article": number})):
        assert call(articles, name, args, agent=CLIENT) == call(articles, name, args,
                                                                agent=LONELY)


def test_calling_them_counts_no_step_credits_nobody_and_writes_nothing(articles):
    number = put(articles, articles.garden, "approved", "t", title="A title")
    tables = ("agent_step", "agent_work", "credit", "turn", "piece", "revision", "source",
              "meta", "site")
    before = {t: articles.store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in tables}
    changes = articles.store.conn.total_changes
    for agent in (CLIENT, LONELY):
        for name, args in (("sites", {}), ("articles", {}), ("article", {"article": number})):
            assert call(articles, name, args, agent=agent)["ok"]
    after = {t: articles.store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in tables}
    assert after == before and articles.store.conn.total_changes == changes
    assert articles.core.work == {}


def test_they_reach_nothing_outside_articles_no_search_no_fetch_no_model(articles):
    number = put(articles, articles.garden, "approved", "t", title="A title")
    articles.core.replies.clear()
    articles.core.pages.clear()
    articles.core.search_results.clear()
    for name, args in (("sites", {}), ("articles", {}), ("article", {"article": number})):
        assert call(articles, name, args)["ok"]
    assert articles.core.replies == [] and articles.core.pages == {}


def test_none_of_them_belongs_to_a_role_and_a_role_tool_still_asks_for_its_role(articles):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    in_roles = {t for r in manifest["roles"] for t in r["tools"]}
    declared = {t["name"]: t["effect"] for t in manifest["tools"]}
    for name in outside.NAMES:
        assert name not in in_roles and declared[name] == "read"
    said = call(articles, "brief", {"piece": 1}, agent="agent:wren", role="checker")
    assert said["ok"] is False and "belongs to the writer" in said["shown"]
