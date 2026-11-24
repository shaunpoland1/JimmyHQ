"""Feeds between apps, tried from the attacker's side: a consumer reading a feed it was
not granted, a feed permission stretched to other kinds or another app, a consumer left
installed after its producer is gone, an item the owner had erased, and a manifest that
names a kind that is not its own. And the owner's side: what the dialog and each app's
page say about who reads what."""

from __future__ import annotations

import pytest

from apps_full_helpers import AppClient, client_for, full_app, install_full
from apps_install_helpers import install_folder
from jimmyhq.apps import feeds, install, manifest, permissions, quotas
from jimmyhq.apps.manifest import ManifestError

OFFERED = {"type": "object", "properties": {"url": {"type": "string"},
                                            "title": {"type": "string"}},
           "required": ["url", "title"], "additionalProperties": False}
NOTE = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"],
        "additionalProperties": False}
ARTICLES = [{"name": "articles", "kind": "article.offered", "label": "Articles",
             "description": "Articles you chose to offer, with their address and title."}]


@pytest.fixture(autouse=True)
def _kinds_go_with_the_test():
    """Tests share one process on Windows: the kinds these apps declare go with them."""
    from jimmyhq.tape import registry as kinds_registry

    def apps_own(key) -> bool:  # what a core module declares on first import stays
        return (key[0] if isinstance(key, tuple) else key).startswith("app.")

    tables = (kinds_registry._KINDS, kinds_registry._OWNERS, kinds_registry._EXAMPLES,
              kinds_registry._SCHEMA_EXAMPLES)
    kept = [{k: v for k, v in t.items() if apps_own(k)} for t in tables]
    backed = {k for k in kinds_registry._SCHEMA_BACKED if apps_own(k)}
    yield
    for table, was in zip(tables, kept, strict=True):
        for key in [k for k in table if apps_own(k)]:
            del table[key]
        table.update(was)
    kinds_registry._SCHEMA_BACKED.difference_update(
        {k for k in kinds_registry._SCHEMA_BACKED if apps_own(k)} - backed)


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


def speak(web, core, app_id: str) -> AppClient:
    return AppClient(web, core.auth.mint_app_token(app_id))


def producer(core, tmp_path, app_id: str = "finder", **extra) -> str:
    install_full(core, tmp_path, app_id, feeds=extra.pop("feeds", ARTICLES), **extra)
    return app_id


def consumer(core, tmp_path, app_id: str = "writer", reads=("finder.articles",),
             **extra) -> str:
    perms_ = [*extra.pop("permissions", []), *(f"feed:{r}" for r in reads)]
    install_full(core, tmp_path, app_id, permissions=perms_, **extra)
    return app_id


def declare(app: AppClient) -> None:
    body = app.put("/kinds", {"kinds": [
        {"name": "article.offered", "v": 1, "schema": OFFERED,
         "example": {"url": "https://news.example.com/a", "title": "A"}},
        {"name": "note", "v": 1, "schema": NOTE, "example": {"n": 1}}]}).json()
    assert body["ok"], body


def offer(app: AppClient, n: int) -> dict:
    return app.ok("tape.print", {"kind": "article.offered", "payload": {
        "url": f"https://news.example.com/{n}", "title": f"Article {n}"}})


@pytest.fixture
def pair(web, core, tmp_path):
    producer(core, tmp_path)
    consumer(core, tmp_path)
    finder = speak(web, core, "finder")
    declare(finder)
    return finder, speak(web, core, "writer")


# --- the manifest ---------------------------------------------------------------------------


def base(**changes) -> dict:
    return {"manifest": 1, "id": "finder", "name": "Finder", "summary": "Finds things.",
            "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
            "permissions": [], "server": {"entry": "finder_main:app"}} | changes


def test_a_feed_is_declared_with_a_name_its_own_kind_a_label_and_a_sentence():
    parsed = manifest.parse(base(feeds=ARTICLES))
    [feed] = parsed.feeds
    assert parsed.feed_kind(feed) == "app.finder.article.offered"
    assert manifest.parse(parsed.to_dict()) == parsed


@pytest.mark.parametrize("kind", ["app.posts.posted", "app", "tasks..created", "Article",
                                  "a.b.c.d.e", 12])
def test_a_manifest_naming_a_feed_kind_that_is_not_its_own_short_name_is_refused(kind):
    with pytest.raises(ManifestError) as refused:
        manifest.parse(base(feeds=[{**ARTICLES[0], "kind": kind}]))
    assert refused.value.fields == ("feeds[0].kind",)
    assert "own" in str(refused.value) or "short name" in str(refused.value)


def test_a_feed_kind_can_only_ever_be_the_apps_own_whatever_it_is_called():
    # Even a short name that looks like a core kind is put under the app's own prefix.
    parsed = manifest.parse(base(feeds=[{**ARTICLES[0], "kind": "tasks.created"}]))
    assert parsed.feed_kind(parsed.feeds[0]) == "app.finder.tasks.created"


def test_a_private_app_cannot_offer_a_feed():
    with pytest.raises(ManifestError) as refused:
        manifest.parse(base(feeds=ARTICLES, private=True))
    assert "feeds" in refused.value.fields
    assert "private" in str(refused.value)


def test_a_sandboxed_app_can_neither_offer_nor_read_a_feed():
    sandboxed = base(tier="sandboxed", screens=[{"id": "m", "label": "M", "entry": "i.html"}])
    del sandboxed["server"]
    with pytest.raises(ManifestError):
        manifest.parse(sandboxed | {"feeds": ARTICLES})
    with pytest.raises(ManifestError) as refused:
        manifest.parse(sandboxed | {"permissions": ["feed:reader.articles"]})
    assert "full" in str(refused.value)


@pytest.mark.parametrize("written", ["feed:reader", "feed:reader.*", "feed:*",
                                     "feed:Reader.articles", "feed:", "feed",
                                     "feed:reader.articles.more"])
def test_a_feed_permission_names_exactly_one_feed_of_one_app(written):
    with pytest.raises(permissions.PermissionRefused) as refused:
        permissions.parse_permission(written)
    assert "feed:reader.articles" in str(refused.value)


def test_an_app_cannot_ask_to_read_a_feed_of_its_own():
    with pytest.raises(ManifestError) as refused:
        manifest.parse(base(feeds=ARTICLES, permissions=["feed:finder.articles"]))
    assert "tape.read" in str(refused.value)


def test_a_feed_permission_is_a_sentence_the_owner_can_read():
    [line] = [x for x in permissions.describe(manifest.parse(base(
        permissions=["feed:reader.articles"]))) if x.permission == "feed"]
    assert line.id == "feed:reader.articles" and line.host == "reader.articles"
    assert "\"articles\"" in line.sentence and "\"reader\"" in line.sentence


# --- reading: only what was granted ---------------------------------------------------------


def test_an_offered_item_is_read_by_the_app_granted_its_feed(pair):
    finder, writer = pair
    printed = offer(finder, 1)
    found = writer.ok("feed.read", {"app": "finder", "feed": "articles"})
    assert [i["id"] for i in found["items"]] == [printed["id"]]
    assert found["items"][0]["item"] == {"url": "https://news.example.com/1",
                                         "title": "Article 1"}
    assert found["items"][0]["erased"] is False and found["next"] == printed["seq"]


def test_an_app_reading_a_feed_it_was_not_granted_is_refused_and_reads_nothing(
        web, core, tmp_path, pair):
    finder, _ = pair
    offer(finder, 1)
    install_full(core, tmp_path, "nosy", permissions=["tape.read"])
    refused = speak(web, core, "nosy").refused("feed.read", {"app": "finder",
                                                             "feed": "articles"})
    assert refused["code"] == "not_allowed"
    assert "feed:finder.articles" in refused["message"]
    assert "nothing was read" in refused["message"]


def test_a_feed_permission_cannot_be_stretched_to_another_apps_feed(web, core, tmp_path, pair):
    finder, writer = pair
    producer(core, tmp_path, "other")
    other = speak(web, core, "other")
    declare(other)
    offer(other, 9)
    for args in ({"app": "other", "feed": "articles"}, {"app": "finder", "feed": "notes"},
                 {"app": "finder", "feed": "articles", "kinds": ["app.finder.note"]}):
        refused = writer.refused("feed.read", args)
        assert refused["code"] in ("not_allowed", "invalid"), args


def test_a_feed_hands_over_only_its_one_kind_printed_by_its_own_app(core, pair):
    finder, writer = pair
    finder.ok("tape.print", {"kind": "note", "payload": {"n": 1}})
    mine = offer(finder, 1)
    # Someone else printing under the producer's kind is not the producer's feed.
    core.tape.print("app.finder.article.offered",
                    {"url": "https://news.example.com/x", "title": "Forged"}, actor="owner")
    found = writer.ok("feed.read", {"app": "finder", "feed": "articles"})
    assert [i["id"] for i in found["items"]] == [mine["id"]]
    assert all(i["item"]["title"] != "Forged" for i in found["items"])


def test_feed_read_takes_no_kinds_and_cannot_name_who_is_reading(pair):
    _, writer = pair
    for extra in ({"kinds": ["tasks.created"]}, {"as": "app:finder"}, {"actor": "owner"}):
        refused = writer.refused("feed.read", {"app": "finder", "feed": "articles", **extra})
        assert refused["code"] == "invalid"


def test_reading_on_from_next_hands_each_item_over_once(pair):
    finder, writer = pair
    printed = [offer(finder, n) for n in range(5)]
    first = writer.ok("feed.read", {"app": "finder", "feed": "articles", "limit": 2})
    assert len(first["items"]) == 2
    rest = writer.ok("feed.read", {"app": "finder", "feed": "articles",
                                   "after": first["next"]})
    assert [i["id"] for i in first["items"] + rest["items"]] == [p["id"] for p in printed]
    nothing = writer.ok("feed.read", {"app": "finder", "feed": "articles",
                                      "after": rest["next"]})
    assert nothing["items"] == [] and nothing["next"] == rest["next"]


def test_one_read_hands_over_at_most_the_owners_items_per_read(core, pair):
    finder, writer = pair
    for n in range(4):
        offer(finder, n)
    core.settings.set(quotas.OVERRIDES, ["writer.feed_items_per_read=3"], actor="owner")
    found = writer.ok("feed.read", {"app": "finder", "feed": "articles", "limit": 50})
    assert len(found["items"]) == 3


def test_reads_a_day_are_counted_and_refused_past_the_limit(core, pair):
    _, writer = pair
    core.settings.set(quotas.OVERRIDES, ["writer.feed_reads_per_day=1"], actor="owner")
    writer.ok("feed.read", {"app": "finder", "feed": "articles"})
    refused = writer.refused("feed.read", {"app": "finder", "feed": "articles"})
    assert refused["code"] == "quota"


# --- when the producer is not there -----------------------------------------------------------


def test_a_consumer_left_installed_after_the_producer_is_removed_is_told_so(core, pair):
    finder, writer = pair
    offer(finder, 1)
    install.remove(core, "finder")
    refused = writer.refused("feed.read", {"app": "finder", "feed": "articles"})
    assert refused["code"] == "not_found"
    assert "was removed" in refused["message"] and "Nothing was read" in refused["message"]


def test_a_producer_that_is_not_installed_is_named_not_answered_with_an_empty_list(
        web, core, tmp_path):
    consumer(core, tmp_path)
    refused = speak(web, core, "writer").refused("feed.read", {"app": "finder",
                                                               "feed": "articles"})
    assert refused["code"] == "not_found" and "is not installed" in refused["message"]


def test_a_switched_off_producer_is_named(core, pair):
    install.disable(core, "finder")
    _, writer = pair
    refused = writer.refused("feed.read", {"app": "finder", "feed": "articles"})
    assert refused["code"] == "unavailable" and "switched off" in refused["message"]


def test_a_producer_that_no_longer_declares_the_feed_is_named(web, core, tmp_path):
    producer(core, tmp_path, feeds=[{**ARTICLES[0], "name": "stories"}])
    consumer(core, tmp_path)
    refused = speak(web, core, "writer").refused("feed.read", {"app": "finder",
                                                               "feed": "articles"})
    assert refused["code"] == "not_found"
    assert "no longer offers" in refused["message"] and "stories" in refused["message"]


def test_a_producer_installed_after_its_reader_is_read_from_at_once(web, core, tmp_path):
    consumer(core, tmp_path)
    writer = speak(web, core, "writer")
    assert writer.refused("feed.read", {"app": "finder", "feed": "articles"})
    producer(core, tmp_path)
    finder = speak(web, core, "finder")
    declare(finder)
    offer(finder, 1)
    assert len(writer.ok("feed.read", {"app": "finder", "feed": "articles"})["items"]) == 1


# --- erased items ---------------------------------------------------------------------------


def test_an_item_erased_from_the_record_comes_back_as_erased_not_as_content(core, pair):
    finder, writer = pair
    kept, gone = offer(finder, 1), offer(finder, 2)
    core.tape.erase([gone["id"]], actor="owner", reason="test")
    found = writer.ok("feed.read", {"app": "finder", "feed": "articles"})
    by_id = {i["id"]: i for i in found["items"]}
    assert by_id[gone["id"]]["erased"] is True and by_id[gone["id"]]["item"] is None
    assert by_id[kept["id"]]["item"]["title"] == "Article 1"
    assert "Article 2" not in str(found)


# --- private apps ---------------------------------------------------------------------------


def test_a_private_app_may_read_a_feed_and_keeps_it_off_the_record(web, core, tmp_path, pair):
    finder, _ = pair
    offer(finder, 1)
    consumer(core, tmp_path, "diary", private=True)
    diary = speak(web, core, "diary")
    assert len(diary.ok("feed.read", {"app": "finder", "feed": "articles"})["items"]) == 1
    assert diary.refused("tape.print", {"kind": "x", "payload": {}})["code"] == "not_allowed"


# --- what the owner reads -------------------------------------------------------------------


def test_the_dialog_says_a_feed_in_the_producers_own_words_when_it_is_installed(
        core, tmp_path):
    producer(core, tmp_path)
    plan = install.plan_folder(core, full_app(tmp_path, "writer",
                                              permissions=["feed:finder.articles"]))
    [line] = [x for x in plan["permissions"] if x["permission"] == "feed"]
    assert line["sentence"].startswith("Read \"Articles\" from Finder:")
    assert line["waiting"] is False


def test_the_dialog_says_plainly_that_a_feed_of_an_app_not_installed_reads_nothing(
        core, tmp_path):
    plan = install.plan_folder(core, full_app(tmp_path, "writer",
                                              permissions=["feed:finder.articles"]))
    [line] = [x for x in plan["permissions"] if x["permission"] == "feed"]
    assert line["waiting"] is True
    assert "if you install it" in line["sentence"] and "reads nothing now" in line["sentence"]


def test_installing_a_producer_says_which_installed_apps_will_read_its_feed(core, tmp_path):
    consumer(core, tmp_path)
    plan = install.plan_folder(core, full_app(tmp_path, "finder", feeds=ARTICLES))
    assert plan["feeds"] == ["Writer, already installed, will read \"Articles\": Articles "
                             "you chose to offer, with their address and title."]


def test_both_apps_pages_say_who_reads_what(core, tmp_path):
    producer(core, tmp_path)
    consumer(core, tmp_path)
    assert install.detail(core, "finder")["feeds"] == [
        "Writer reads \"Articles\", which Finder offers: Articles you chose to offer, with "
        "their address and title."]
    assert install.detail(core, "writer")["feeds"] == ["Writer reads \"Articles\" from Finder."]


def test_an_apps_page_says_when_the_feed_it_may_read_has_nobody_to_offer_it(core, tmp_path):
    consumer(core, tmp_path)
    [line] = install.detail(core, "writer")["feeds"]
    assert "not installed" in line and "reads nothing" in line


def test_readers_are_found_from_what_the_owner_granted_not_from_what_a_manifest_says(
        core, tmp_path):
    producer(core, tmp_path)
    assert feeds.readers_of(core, "finder", "articles") == []
    consumer(core, tmp_path)
    assert feeds.readers_of(core, "finder", "articles") == ["Writer"]
    install_folder(core, full_app(tmp_path, "third", permissions=["feed:finder.stories"]))
    assert feeds.readers_of(core, "finder", "articles") == ["Writer"]


def test_an_app_reading_from_a_place_past_the_end_of_a_restored_tape_is_told_the_end(pair):
    finder, writer = pair
    offer(finder, 1)
    # The writer's own file was copied after a Tape that had a hundred fewer events.
    ahead = writer.ok("feed.read", {"app": "finder", "feed": "articles",
                                    "after": 2**31})
    assert ahead["items"] == [] and ahead["next"] < 2**31
    fresh = offer(finder, 2)  # a number the writer had counted as read
    found = writer.ok("feed.read", {"app": "finder", "feed": "articles",
                                    "after": ahead["next"]})
    assert [i["id"] for i in found["items"]] == [fresh["id"]]
