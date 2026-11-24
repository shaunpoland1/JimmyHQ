"""WordPress for real: a draft on the owner's own site, an address he gave Articles
himself, signed in with that address's own user name and application password. Every
answer here is canned by the fake core; no request reaches any site."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest
from articles.publishers import Publication
from articles.publishers.wordpress import (
    looks_like_a_page,
    read_answer,
    redirect_sentence,
    refusal_sentence,
    scrub,
    site_message,
)
from jimmyhq_app import CallError

HOST = "blog.example.com"
OTHER = "notes.example.org"
KEY = "sk-test-SECRET-0123456789"
USER = "editor"
POSTS = f"https://{HOST}/wp-json/wp/v2/posts"
TOKEN = base64.b64encode(f"{USER}:{KEY}".encode()).decode("ascii")
MADE = {"status": 201, "headers": {"content-type": "application/json; charset=UTF-8"},
        "text": '{"id": 77, "link": "https://blog.example.com/?p=77", "status": "draft"}'}
PAGE = {"content-type": "text/html; charset=UTF-8"}


def given(rig, **secrets):
    rig.core.grant_host(HOST, secrets={"wordpress_user": USER, "wordpress_password": KEY}
                        | secrets)


def approved(rig, **site) -> dict:
    piece = rig.to_card(rig.site(publisher="wordpress", wordpress_host=HOST, **site))
    [card] = rig.cards_of("article")
    rig.core.decide(card["card_id"], "approve")
    return rig.store.piece(piece["id"])


def publish(rig, piece):
    return rig.core.call("article.publish", {"id": piece["id"]})


def every_byte(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


# --- the draft ------------------------------------------------------------------------------


def test_an_approved_article_becomes_a_draft_on_the_owners_own_site_and_only_a_draft(rig):
    given(rig)
    rig.core.responses[POSTS] = MADE
    piece = approved(rig)
    done = publish(rig, piece)
    [sent] = rig.core.fetched
    assert sent["url"] == POSTS and sent["method"] == "POST"
    assert sent["headers"] == {"Authorization": f"Basic {TOKEN}",
                               "Content-Type": "application/json"}
    revision = rig.store.revision(piece["id"])
    assert sent["json"]["title"] == revision["title"] and sent["json"]["status"] == "draft"
    content = sent["json"]["content"]
    assert "<h1>" not in content and "[S1:" not in content and "<script" not in content
    assert "Sources" in content and "survey.example.com" in content
    assert done["state"] == "sent" and "Publish it there" in done["said"]
    assert rig.store.attempts(piece["id"])[0]["external"]["id"] == 77


def test_the_attempt_is_recorded_before_the_draft_is_sent(rig):
    given(rig)
    seen = []

    def answer(args):
        seen.append([a["outcome"] for a in rig.store.attempts(piece["id"])])
        return MADE
    rig.core.responses[POSTS] = answer
    piece = approved(rig)
    publish(rig, piece)
    assert seen == [["pending"]]
    kinds = [e["kind"] for e in rig.core.events]
    assert kinds.index("app.articles.publish.attempted") < kinds.index(
        "app.articles.publish.result")


def test_a_site_names_which_of_the_owners_addresses_it_publishes_to(rig):
    rig.core.grant_host(HOST)
    rig.core.grant_host(OTHER)
    assert rig.core.call("sites.hosts") == {"hosts": [HOST, OTHER]}
    site = rig.site(publisher="wordpress", wordpress_host=OTHER)
    assert site["wordpress_url"] == f"https://{OTHER}"


def test_what_the_site_lacks_is_said_before_anything_is_sent(rig):
    piece = approved(rig)
    for step, words in ((lambda: None, f"has not been given {HOST}"),
                        (lambda: rig.core.grant_host(HOST), "user name"),
                        (lambda: given(rig, wordpress_password=""), "application password")):
        step()
        with pytest.raises(Exception, match=words) as said:
            publish(rig, piece)
        assert "Nothing was sent" in str(said.value)
    assert rig.core.fetched == [] and rig.store.attempts(piece["id"]) == []


def test_a_host_the_owner_removed_is_not_published_to(rig):
    given(rig)
    piece = approved(rig)
    rig.core.remove_host(HOST)
    with pytest.raises(Exception, match=f"has not been given {HOST}"):
        publish(rig, piece)
    assert rig.core.fetched == []


# --- the usual failures ---------------------------------------------------------------------


@pytest.mark.parametrize("reply, words", [
    ({"status": 401, "json": {"code": "incorrect_password", "message": "The password is wrong."}},
     "did not accept the user name and application password"),
    ({"status": 401, "json": {"code": "invalid_username", "message": "Unknown user."}},
     "did not accept the user name"),
    ({"status": 403, "json": {"code": "rest_cannot_create", "message": "Not allowed."}},
     "may not write posts"),
    ({"status": 404, "json": {"code": "rest_no_route", "message": "No route was found."}},
     "REST API of your site"),
    ({"status": 401, "json": {"code": "rest_disabled", "message": "The REST API is off."}},
     "REST API of your site"),
    ({"status": 403, "headers": PAGE, "text": "<!DOCTYPE html><title>Blocked</title>"},
     "security plugin"),
    ({"status": 302, "headers": {"location": f"https://{OTHER}/wp-json/wp/v2/posts"}},
     f"another address, {OTHER}"),
    ({"status": 301, "headers": {"location": f"https://{HOST}/blog/wp-json/wp/v2/posts"}},
     "answered with a redirect"),
    ({"status": 429, "json": {"message": "Slow down."}}, "asked Articles to wait"),
])
def test_a_definite_no_is_refused_with_the_sites_own_words_and_the_article_waits_approved(
        rig, reply, words):
    given(rig)
    rig.core.responses[POSTS] = reply
    piece = approved(rig)
    done = publish(rig, piece)
    assert done["state"] == "approved" and words in done["said"]
    said = reply.get("json", {}).get("message")
    if said:
        assert said in done["said"]
    assert len(rig.core.fetched) == 1  # a redirect is never followed
    assert rig.store.attempts(piece["id"])[0]["outcome"] == "refused"


@pytest.mark.parametrize("reply, words", [
    ({"status": 200, "headers": PAGE, "text": "<html><body>Checking your browser</body></html>"},
     "web page instead"),
    ({"status": 201, "json": {"status": "draft"}}, "did not say which draft"),
    ({"status": 500, "text": "oops"}, "no clear answer"),
    ({"status": 504, "text": ""}, "no clear answer"),
    (CallError("unavailable", "blog.example.com took longer than 30 seconds to answer."),
     "gave no answer"),
])
def test_anything_but_a_definite_answer_is_unsure_and_nothing_retries_by_itself(
        rig, reply, words):
    given(rig)
    rig.core.responses[POSTS] = reply
    piece = approved(rig)
    done = publish(rig, piece)
    assert done["state"] == "unsure" and words in done["said"]
    rig.advance(3)
    with pytest.raises(Exception, match="may be on the site already"):
        rig.store.update("piece", piece["id"], {"state": "approved"})
        publish(rig, piece)
    assert len(rig.core.fetched) == 1


def test_jimmyhq_refusing_the_request_before_it_left_is_a_definite_no(rig):
    given(rig)
    rig.core.responses[POSTS] = CallError(
        "not_allowed", "blog.example.com now leads to an address on the owner's own machine "
                       "or private network, which an app is never sent to. Nothing was sent.")
    piece = approved(rig)
    done = publish(rig, piece)
    assert done["state"] == "approved" and "did not send the draft" in done["said"]


# --- an edit after sending ------------------------------------------------------------------


def sent_piece(rig) -> dict:
    given(rig)
    rig.core.responses[POSTS] = MADE
    piece = approved(rig)
    publish(rig, piece)
    return rig.store.piece(piece["id"])


def test_an_edit_after_sending_updates_the_draft_while_it_is_still_a_draft(rig):
    piece = sent_piece(rig)
    rig.core.responses[f"{POSTS}/77?context=edit"] = {"status": 200,
                                                      "json": {"id": 77, "status": "draft"}}
    rig.core.responses[f"{POSTS}/77"] = {"status": 200, "json": {"id": 77, "status": "draft"}}
    done = rig.core.call("article.edit_sent", {"id": piece["id"], "title": "A better title",
                                               "body": "<p>Plot holders spent 18% less on "
                                                       "vegetables [S1:1].</p>"})
    assert "now has your edit" in done["said"] and done["state"] == "sent"
    look, change = rig.core.fetched[1:]
    assert look["method"] == "GET" and change["url"] == f"{POSTS}/77"
    assert change["json"]["title"] == "A better title" and "status" not in change["json"]
    assert "18% less" in change["json"]["content"]
    assert rig.store.revision(piece["id"])["kind"] == "edit"


def test_an_edit_is_not_sent_once_the_owner_published_the_post_and_says_so_plainly(rig):
    piece = sent_piece(rig)
    rig.core.responses[f"{POSTS}/77?context=edit"] = {"status": 200,
                                                      "json": {"id": 77, "status": "publish"}}
    done = rig.core.call("article.edit_sent", {"id": piece["id"], "body": "<p>New text.</p>"})
    assert "no longer a draft" in done["said"] and "did not change it" in done["said"]
    assert [f["method"] for f in rig.core.fetched] == ["POST", "GET"]
    assert rig.store.revision(piece["id"])["body"] == "<p>New text.</p>"


def test_an_edit_for_a_site_that_sends_nothing_out_says_the_site_was_not_changed(rig):
    piece = rig.to_card(rig.site(publisher="by_hand"))
    [card] = rig.cards_of("article")
    rig.core.decide(card["card_id"], "approve")
    done = rig.core.call("article.edit_sent", {"id": piece["id"], "body": "<p>New text.</p>"})
    assert "nothing on your site was changed" in done["said"]
    assert rig.core.fetched == []


def test_an_edit_whose_update_got_no_answer_blocks_another_until_the_owner_has_looked(rig):
    piece = sent_piece(rig)
    rig.core.responses[f"{POSTS}/77?context=edit"] = {"status": 200,
                                                      "json": {"id": 77, "status": "draft"}}
    rig.core.responses[f"{POSTS}/77"] = CallError("unavailable", "No answer.")
    done = rig.core.call("article.edit_sent", {"id": piece["id"], "body": "<p>One.</p>"})
    assert "may or may not be" in done["said"]
    with pytest.raises(Exception, match="got no answer"):
        rig.core.call("article.edit_sent", {"id": piece["id"], "body": "<p>Two.</p>"})
    rig.core.call("article.clear", {"id": piece["id"]})
    rig.core.responses[f"{POSTS}/77"] = {"status": 200, "json": {"id": 77}}
    assert "now has your edit" in rig.core.call(
        "article.edit_sent", {"id": piece["id"], "body": "<p>Two.</p>"})["said"]


# --- the password stays secret --------------------------------------------------------------


def test_the_password_never_appears_in_a_log_line_an_event_a_sentence_or_the_apps_records(
        rig, caplog):
    caplog.set_level(logging.DEBUG)
    given(rig)
    # A site that repeats everything it was sent, the sign-in too.
    echo = f"Bad sign-in for {USER} with {KEY} (Basic {TOKEN})"
    rig.core.responses[POSTS] = {"status": 401, "json": {"code": "incorrect_password",
                                                         "message": echo}}
    piece = approved(rig)
    said = publish(rig, piece)["said"]
    rig.core.responses[POSTS] = CallError("unavailable", f"The site said {KEY}")
    rig.store.update("piece", piece["id"], {"state": "approved"})
    said += publish(rig, piece)["said"]
    rig.ctx.close()
    found = every_byte(rig.data_dir) + str(rig.core.events).encode() + said.encode() + \
        caplog.text.encode() + str(rig.core.storage).encode()
    for secret in (KEY, TOKEN):
        assert secret.encode() not in found
    assert "[hidden]" in said


# --- the rules, one by one ------------------------------------------------------------------


def test_a_secret_is_taken_out_of_anything_the_site_says():
    assert scrub(f"echo {KEY} and {TOKEN}", [KEY, TOKEN]) == "echo [hidden] and [hidden]"
    assert site_message({"message": f"<b>No</b> &amp; {KEY}"}, [KEY]) == "No & [hidden]"
    assert site_message({"message": ["x"]}, []) == ""
    assert len(site_message({"message": "x" * 900}, [])) == 300


def test_a_web_page_where_json_was_expected_is_told_apart():
    assert looks_like_a_page({"headers": PAGE, "text": ""})
    assert looks_like_a_page({"text": "  <!DOCTYPE html>"})
    assert not looks_like_a_page({"headers": {"content-type": "application/json"},
                                  "text": "{}"})


def test_a_redirect_to_another_host_is_named_and_never_followed():
    assert OTHER in redirect_sentence(HOST, f"https://{OTHER}/x")
    assert "answered with a redirect" in redirect_sentence(HOST, f"https://{HOST}/x")
    assert read_answer({"status": 307, "headers": {"location": f"https://{OTHER}/"}}, HOST,
                       []).kind == "refused"


def test_a_401_with_an_unknown_code_is_still_read_as_a_wrong_sign_in():
    assert "did not accept" in refusal_sentence(401, {"code": "something_new"}, HOST, [])
    assert "refused the draft" in refusal_sentence(400, {"code": "rest_invalid_param"}, HOST,
                                                   [])


def test_a_yes_needs_a_whole_number_id_and_keeps_only_an_https_link():
    yes = read_answer({"status": 201, "json": {"id": 5, "link": "javascript:alert(1)"}}, HOST,
                      [])
    assert yes.kind == "sent" and yes.external == {"id": 5, "link": "", "host": HOST}
    assert read_answer({"status": 201, "json": {"id": True}}, HOST, []).kind == "unsure"
    assert read_answer({"status": 201, "json": {"id": "5"}}, HOST, []).kind == "unsure"


def test_a_publication_carries_no_header_image_unless_one_was_made():
    item = Publication(piece_id=1, title="t", html="<p>x</p>", markdown="x", site={})
    assert item.header is None
