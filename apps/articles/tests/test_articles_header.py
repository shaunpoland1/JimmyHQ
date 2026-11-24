"""A header image for a ready article: off until the owner sets it up, in his own style
words only, never a real person, kept in the app's storage, and sent with a WordPress draft
only when it is a real picture, through the same host."""

from __future__ import annotations

import struct
import zlib

import pytest
from articles import header
from articles.header import HeaderError, ImageRequest, Rendered

HOST = "blog.example.com"
KEY = "sk-test-SECRET-0123456789"
STYLE = "Soft watercolour, early morning light, lots of green, seen from above."
SCENE = {"description": "Rows of young lettuce in raised beds at dawn, a watering can "
                        "resting on the path. Seen from above."}


def on(make_rig, **settings):
    return make_rig(settings={"header_images": True, **settings})


def ready(rig, style: str | None = STYLE, **site) -> dict:
    piece = rig.to_card(rig.site(**site))
    if style is not None:
        rig.core.call("header.style.set", {"site_id": piece["site_id"], "style": style})
    return piece


def made(rig, piece, answer=None) -> dict:
    rig.core.replies.append(answer if answer is not None else SCENE)
    return rig.core.call("header.make", {"id": piece["id"]})


def size_of(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


class Real:
    """A stand-in for a provider that makes real pictures, once the platform has one."""

    id, label, sends_out, sendable = "real", "Real", True, True

    def __init__(self):
        self.asked: list[ImageRequest] = []

    def needs(self):
        return None

    def render(self, request):
        self.asked.append(request)
        return Rendered(header.png(4, 2, [(1, 2, 3)]), "image/png")


# --- off until the owner sets it up -----------------------------------------------------------


def test_header_images_are_off_until_the_owner_switches_them_on_and_nothing_is_asked(rig):
    piece = ready(rig)
    asked = len(rig.core.asked)
    with pytest.raises(Exception, match="Header images are switched off") as said:
        made(rig, piece)
    assert "nothing was made" in str(said.value)
    assert len(rig.core.asked) == asked and header.latest(rig.ctx, piece["id"]) is None


def test_without_the_sites_own_style_guidance_nothing_is_made(make_rig):
    rig = on(make_rig)
    piece = ready(rig, style=None)
    asked = len(rig.core.asked)
    with pytest.raises(Exception, match="no style guidance"):
        made(rig, piece)
    assert len(rig.core.asked) == asked
    assert header.must_have_style("   ") is not None and header.must_have_style("x") is None


def test_an_article_still_being_written_gets_no_header_image(make_rig):
    rig = on(make_rig)
    site = rig.site()
    rig.web()
    piece = rig.topic(site)
    rig.core.call("header.style.set", {"site_id": site["id"], "style": STYLE})
    with pytest.raises(Exception, match="once the article is ready"):
        made(rig, piece)


# --- the flow -----------------------------------------------------------------------------------


def test_a_ready_article_gets_a_placeholder_kept_in_the_apps_storage_and_shown_on_it(make_rig):
    rig = on(make_rig, header_width=300, header_height=120)
    piece = ready(rig)
    shown = made(rig, piece)
    assert shown["ref"].startswith("uploads/") and shown["ref"].endswith(".png")
    data = (rig.data_dir / shown["ref"]).read_bytes()
    assert size_of(data) == (300, 120)
    zlib.decompress(data[data.index(b"IDAT") + 4:-16])  # a whole, readable picture
    assert shown["description"] == " ".join(SCENE["description"].split())
    assert shown["provider"] == "practice" and shown["sent_with_draft"] is False
    assert shown["name"] == f"article-{piece['id']}-header.png"
    assert rig.core.call("header.get", {"id": piece["id"]})["header"] == shown
    assert any(t["step"] == "header" for t in rig.store.turns(piece["id"]))
    [event] = [e for e in rig.core.events if e["kind"] == "app.articles.header.made"]
    assert set(event["payload"]) == {"piece", "image", "provider"}  # ids and words only


def test_a_newer_header_image_replaces_the_older_one_on_the_article(make_rig):
    rig = on(make_rig)
    piece = ready(rig)
    first = made(rig, piece)
    second = made(rig, piece, {"description": "A wheelbarrow of compost beside a shed door."})
    assert second["ref"] != first["ref"]
    assert rig.core.call("header.get", {"id": piece["id"]})["header"]["ref"] == second["ref"]


# --- the style is the owner's ---------------------------------------------------------------


def test_the_model_is_given_the_owners_style_words_and_no_style_of_articles_own(make_rig):
    rig = on(make_rig, header_role="cheap")
    piece = ready(rig)
    made(rig, piece)
    asked = rig.core.asked[-1]
    assert asked["role"] == "cheap"
    assert asked["system"] == ("The site's style guidance for header images, in the owner's "
                               f"own words:\n{STYLE}")
    revision = rig.store.revision(piece["id"])
    assert revision["title"] not in asked["prompt"]  # the article is data, not instructions
    assert any(revision["title"] in block for block in asked["untrusted"])
    for word in ("watercolour", "photograph", "minimal", "vibrant", "cinematic", "flat"):
        assert word not in asked["prompt"].lower()


def test_each_site_is_drawn_in_its_own_owners_words_and_the_provider_gets_them_as_written(
        make_rig):
    rig = on(make_rig)
    piece = ready(rig)
    other = rig.core.call("sites.save", {"name": "Kitchen notes"})
    rig.core.call("header.style.set", {"site_id": other["id"], "style": "Bold ink lines."})
    real = Real()
    rig.core.replies.append(SCENE)
    header.make(rig.ctx, piece["id"], provider=real)
    assert real.asked[0].style == STYLE
    assert rig.core.call("header.style.get", {"site_id": other["id"]})["style"] == \
        "Bold ink lines."


def test_style_guidance_is_kept_exactly_as_the_owner_wrote_it_and_has_a_length_limit(rig):
    site = rig.site()
    kept = rig.core.call("header.style.set", {"site_id": site["id"],
                                              "style": "  Pencil.\r\nNo people.  "})
    assert kept["style"] == "Pencil.\nNo people."
    with pytest.raises(Exception, match="at most"):
        rig.core.call("header.style.set", {"site_id": site["id"], "style": "x" * 2001})


# --- never a real person ----------------------------------------------------------------------


@pytest.mark.parametrize("text, words", [
    ("A portrait of a gardener kneeling between the beds.", "portrait"),
    ("Dr. Rivera standing in her garden plot at dawn.", "named someone"),
    ("A plot in the style of the council, with Rivera's hat on a post.", "named someone"),
    ("A gardener who looks like a famous chef, digging.", "portrait"),
    ("Too short.", "characters long"),
    ("x" * 700, "characters long"),
    (None, "did not describe"),
])
def test_a_description_that_could_show_a_real_person_is_refused_and_nothing_is_made(
        make_rig, text, words):
    rig = on(make_rig)
    piece = ready(rig)
    with pytest.raises(Exception, match=words):
        made(rig, piece, {"description": text})
    assert header.latest(rig.ctx, piece["id"]) is None
    assert not (rig.data_dir / "uploads").exists() or not list(
        (rig.data_dir / "uploads").iterdir())


def test_capitals_that_only_start_a_sentence_are_fine():
    text = "Rows of lettuce at dawn. A watering can rests on the path! Seen from above."
    assert header.check_description(text) == text
    with pytest.raises(HeaderError):
        header.check_description("Rows of lettuce near the Eiffel Tower at dawn.")


# --- sent with the draft only when sound ----------------------------------------------------


def wordpress_ready(make_rig):
    rig = on(make_rig)
    rig.core.grant_host(HOST, secrets={"wordpress_user": "editor", "wordpress_password": KEY})
    piece = ready(rig, publisher="wordpress", wordpress_host=HOST)
    return rig, piece


def approve_and_publish(rig, piece):
    rig.core.decide(rig.cards_of("article")[0]["card_id"], "approve")
    return rig.core.call("article.publish", {"id": piece["id"]})


def test_a_practice_placeholder_is_never_sent_to_the_owners_site(make_rig):
    rig, piece = wordpress_ready(make_rig)
    made(rig, piece)
    rig.core.responses[f"https://{HOST}/wp-json/wp/v2/posts"] = {"status": 201,
                                                                 "json": {"id": 9}}
    approve_and_publish(rig, piece)
    assert [f["url"] for f in rig.core.fetched] == [f"https://{HOST}/wp-json/wp/v2/posts"]
    assert "featured_media" not in rig.core.fetched[0]["json"] and rig.core.sent_files == []


def test_a_real_picture_goes_to_the_same_host_and_becomes_the_drafts_featured_image(make_rig):
    rig, piece = wordpress_ready(make_rig)
    rig.core.replies.append(SCENE)
    shown = header.make(rig.ctx, piece["id"], provider=Real())
    rig.core.responses[f"https://{HOST}/wp-json/wp/v2/media"] = {"status": 201,
                                                                 "json": {"id": 31}}
    rig.core.responses[f"https://{HOST}/wp-json/wp/v2/posts"] = {"status": 201,
                                                                 "json": {"id": 9}}
    done = approve_and_publish(rig, piece)
    media, post = rig.core.fetched
    assert media["url"] == f"https://{HOST}/wp-json/wp/v2/media" and media["file"] == \
        shown["ref"]
    assert media["headers"]["Content-Type"] == "image/png"
    assert media["headers"]["Content-Disposition"].startswith("attachment; filename=")
    assert post["json"]["featured_media"] == 31 and post["json"]["status"] == "draft"
    assert [f["ref"] for f in rig.core.sent_files] == [shown["ref"]]
    assert "featured image" in done["said"]


def test_a_header_image_the_site_refuses_leaves_the_draft_without_it_and_says_so(make_rig):
    rig, piece = wordpress_ready(make_rig)
    rig.core.replies.append(SCENE)
    header.make(rig.ctx, piece["id"], provider=Real())
    rig.core.responses[f"https://{HOST}/wp-json/wp/v2/media"] = {
        "status": 403, "json": {"code": "rest_cannot_create", "message": "No uploads."}}
    rig.core.responses[f"https://{HOST}/wp-json/wp/v2/posts"] = {"status": 201,
                                                                 "json": {"id": 9}}
    done = approve_and_publish(rig, piece)
    assert done["state"] == "sent" and "header image was left out" in done["said"]
    assert "featured_media" not in rig.core.fetched[-1]["json"]


def test_a_header_image_is_not_sent_once_header_images_are_switched_off(make_rig):
    rig, piece = wordpress_ready(make_rig)
    rig.core.replies.append(SCENE)
    header.make(rig.ctx, piece["id"], provider=Real())
    rig.core.change_setting("header_images", False)
    assert header.for_publication(rig.ctx, piece["id"]) is None
