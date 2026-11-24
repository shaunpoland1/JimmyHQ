"""The one publishing path: the attempt is recorded first, an attempt with no answer
blocks another, and nothing retries by itself. Practice, By hand and WordPress."""

from __future__ import annotations

import pytest
from articles import publish
from articles.publishers import Outcome
from articles.publishers.wordpress import answer

HOST = "blog.example.com"
KEY = "sk-test-SECRET-0123456789"


class Silent:
    """A publisher that sends out and never answers."""

    id, label, sends_out = "silent", "Silent", True

    def __init__(self, seen: list):
        self.seen = seen

    def needs(self, site):
        return None

    def publish(self, item):
        self.seen.append(("asked", item.piece_id))
        raise TimeoutError("no answer in 30 seconds")


def approved(rig, **site) -> dict:
    piece = rig.to_card(rig.site(**site))
    [card] = rig.cards_of("article")
    rig.core.decide(card["card_id"], "approve")
    return rig.store.piece(piece["id"])


def test_publishing_records_the_attempt_before_anything_is_sent_and_a_second_try_is_refused_while_the_first_got_no_answer(make_rig):  # noqa: E501
    seen: list = []
    rig = make_rig(publishers={"practice": Silent(seen)})

    class Watch(Silent):
        def publish(self, item):
            attempts = rig.store.attempts(item.piece_id)
            kinds = [e["kind"] for e in rig.core.events]
            seen.append((attempts[-1]["outcome"], kinds[-1]))
            raise TimeoutError("no answer")
    rig.ctx.publishers["practice"] = Watch(seen)
    rig.core.ladder[("publish", "send")] = "alone"  # the owner lets it publish alone
    piece = approved(rig)
    assert seen == [("pending", "app.articles.publish.attempted")]
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "unsure" and "no answer" in piece["error"]
    [attempt] = rig.store.attempts(piece["id"])
    assert attempt["outcome"] == "unsure"
    rig.store.update("piece", piece["id"], {"state": "approved"})  # even if it were approved
    with pytest.raises(Exception, match="got no answer, so it may be on the site already"):
        rig.core.call("article.publish", {"id": piece["id"]})
    assert len(seen) == 1 and len(rig.store.attempts(piece["id"])) == 1
    rig.store.update("piece", piece["id"], {"state": "unsure"})
    rig.advance(3)
    assert len(seen) == 1  # nothing retries by itself


def test_after_the_owner_has_looked_an_unsure_article_may_be_tried_again(rig):
    seen: list = []
    rig.ctx.publishers["practice"] = Silent(seen)
    rig.core.ladder[("publish", "send")] = "alone"  # the owner lets it publish alone
    piece = approved(rig)
    rig.core.call("article.clear", {"id": piece["id"]})
    assert rig.store.piece(piece["id"])["state"] == "approved"
    assert rig.store.attempts(piece["id"])[0]["outcome"] == "cleared"
    again = rig.core.call("article.publish", {"id": piece["id"]})  # asked once more
    assert again["state"] == "unsure" and len(seen) == 2
    assert [a["outcome"] for a in rig.store.attempts(piece["id"])] == ["cleared", "unsure"]


def test_a_publisher_that_sends_out_waits_for_the_owners_publish_click(rig):
    seen: list = []
    rig.ctx.publishers["practice"] = Silent(seen)
    piece = approved(rig)
    assert piece["state"] == "approved" and seen == []
    assert rig.store.attempts(piece["id"]) == []


def test_practice_records_what_it_would_send_and_is_the_default(rig):
    piece = approved(rig)
    assert rig.store.site(piece["site_id"])["publisher"] == "practice"
    assert piece["state"] == "sent"
    [attempt] = rig.store.attempts(piece["id"])
    assert attempt["outcome"] == "sent" and attempt["publisher"] == "practice"
    assert [e["kind"] for e in rig.core.events][-2:] == ["app.articles.publish.attempted",
                                                         "app.articles.publish.result"]


def test_by_hand_gives_the_article_as_clean_html_and_markdown_and_waits_for_published(rig):
    piece = approved(rig, publisher="by_hand")
    assert piece["state"] == "sent"
    page = rig.core.call("article.export", {"id": piece["id"], "form": "html"})
    assert page["text"].startswith("<h1>") and "<h2>Sources</h2>" in page["text"]
    assert "[S1" not in page["text"] and page["name"].endswith(".html")
    md = rig.core.call("article.export", {"id": piece["id"], "form": "markdown"})
    assert md["text"].startswith("# ") and "## Sources" in md["text"]
    rig.core.call("article.published", {"id": piece["id"]})
    assert rig.store.piece(piece["id"])["state"] == "published"


def test_wordpress_says_plainly_it_cannot_reach_the_owners_site_until_he_gives_it(rig):
    piece = approved(rig, publisher="wordpress", wordpress_url=f"https://{HOST}")
    assert piece["state"] == "approved"
    with pytest.raises(Exception, match=f"has not been given {HOST}") as said:
        rig.core.call("article.publish", {"id": piece["id"]})
    assert "Nothing was sent" in str(said.value) and "By hand" in str(said.value)
    assert rig.core.fetched == [] and rig.store.attempts(piece["id"]) == []


def test_wordpress_with_its_host_given_makes_a_draft_and_only_a_draft(rig):
    rig.core.grant_host(HOST, secrets={"wordpress_user": "ed", "wordpress_password": KEY})
    url = f"https://{HOST}/wp-json/wp/v2/posts"
    rig.core.responses[url] = {"status": 201, "json": {"id": 77, "link": f"https://{HOST}/?p=77"}}
    piece = approved(rig, publisher="wordpress", wordpress_url=f"https://{HOST}")
    rig.core.call("article.publish", {"id": piece["id"]})
    [sent] = rig.core.fetched
    assert sent["url"] == url and sent["method"] == "POST"
    assert sent["json"]["status"] == "draft" and "<h1>" not in sent["json"]["content"]
    assert sent["headers"]["Authorization"].startswith("Basic ")
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "sent"
    assert rig.store.attempts(piece["id"])[0]["external"] == {
        "id": 77, "link": f"https://{HOST}/?p=77", "host": HOST}
    assert KEY not in str(rig.core.events) and KEY not in str(rig.store.attempts(piece["id"]))


def test_a_definite_no_from_wordpress_puts_the_article_back_to_approved(rig):
    rig.core.grant_host(HOST, secrets={"wordpress_user": "ed", "wordpress_password": KEY})
    rig.core.responses[f"https://{HOST}/wp-json/wp/v2/posts"] = {
        "status": 401, "json": {"code": "incorrect_password",
                                "message": "Sorry, you are not allowed to do that."}}
    piece = approved(rig, publisher="wordpress", wordpress_url=f"https://{HOST}")
    done = rig.core.call("article.publish", {"id": piece["id"]})
    assert done["state"] == "approved" and "did not accept the user name" in done["said"]
    assert rig.store.attempts(piece["id"])[0]["outcome"] == "refused"


@pytest.mark.parametrize("reply, kind", [
    ({"status": 201, "json": {"id": 5}}, "sent"), ({"status": 200, "text": '{"id": 6}'}, "sent"),
    ({"status": 201, "json": {}}, "unsure"), ({"status": 400, "json": {}}, "refused"),
    ({"status": 408}, "unsure"), ({"status": 503}, "unsure"), (None, "unsure"),
])
def test_only_a_definite_answer_from_wordpress_counts(reply, kind):
    assert answer(reply).kind == kind


def test_only_an_approved_article_can_be_published(rig):
    piece = rig.to_card()
    with pytest.raises(Exception, match="only an approved article can be published"):
        rig.core.call("article.publish", {"id": piece["id"]})
    assert publish.must_be_approved({"state": "ready"}) is not None
    assert publish.no_open_attempt([{"outcome": "pending"}]) is not None
    assert publish.no_open_attempt([{"outcome": "refused"}, {"outcome": "cleared"}]) is None


def test_a_publisher_that_answers_nonsense_is_unsure():
    class Odd:
        def publish(self, item):
            return "done!"
    assert publish._ask(Odd(), None).kind == "unsure"  # noqa: SLF001
    assert isinstance(publish._ask(Odd(), None), Outcome)  # noqa: SLF001


def test_an_attempt_left_waiting_when_the_program_stopped_becomes_unsure_and_is_not_sent_again(rig):  # noqa: E501
    piece = rig.to_card()
    rig.store.move(piece["id"], "approved")
    rig.store.move(piece["id"], "publishing")
    rig.store.insert("attempt", {"piece_id": piece["id"], "publisher": "practice",
                                 "started": rig.clock.now, "outcome": "pending"})
    rig.clock.advance(60 * 60 * 1000)
    rig.advance(1)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "unsure" and "no answer" in piece["error"]
    assert rig.store.attempts(piece["id"])[0]["outcome"] == "unsure"
    assert rig.store.all("SELECT * FROM practice_log") == []
