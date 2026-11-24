"""The diary, things to remember, rest, the catalog itself, and what reaches the log."""

from __future__ import annotations

from jimmyhq.village import tools
from jimmyhq.village.tools import logged
from jimmyhq.village.tools.self import rest_refusal
from test_village_tools_fakes import (  # noqa: F401
    call,
    candidates,
    diary_lines,
    prioritize,
    refused,
    v,
)

# --- the catalog -------------------------------------------------------------------------

MODES = {
    "list_files": "WEO", "read_file": "WEO", "write_file": "WEO", "add_to_file": "WEO",
    "edit_file": "WEO", "write_diary": "WEO", "remember": "WEO", "rest": "WEO",
    "focus_on": "W", "finish_task": "W", "set_aside": "W", "take_on": "W",
    "start_own_task": "W", "review_work": "W", "hand_over": "W", "report_finding": "WE",
    "correct_finding": "WE", "suggest_priority": "W", "message_owner": "WEO", "say": "S",
    "post_to_board": "W", "resolve_post": "W",
}


def test_the_catalog_holds_the_22_tools_every_agent_has_each_in_its_modes():
    assert tools.ALL is tools.ALL
    all_tools = [t for t in tools.ALL if t.capability is None]
    assert len(all_tools) == 22
    letters = {"work": "W", "explore": "E", "outlet": "O", "social": "S"}
    assert {t.name: "".join(letters[m] for m in t.modes) for t in all_tools} == MODES
    for t in tools.ALL:
        assert set(t.required) <= set(t.params), t.name
        assert callable(t.run)
    # Beyond those, only the two web tools, the answer to an outside client and the
    # nine browser tools, each only under its capability.
    browser = ("open_page", "click", "type_text", "press_key", "scroll", "go_back",
               "switch_tab", "close_tab", "read_page")
    assert {t.name: (t.capability, tuple(t.modes)) for t in tools.ALL if t.capability} == {
        "web_search": ("web", ("work", "explore")), "web_read": ("web", ("work", "explore")),
        "reply_outside": ("outside", ("work", "explore", "outlet")),
        # Taking back a request to use an integration: only with a rule from the owner.
        "withdraw_request": ("hands", ("work", "explore")),
        **{name: ("browser", ("work", "explore")) for name in browser}}


def test_no_tool_names_a_person_and_the_owner_is_a_placeholder():
    words = " ".join(t.description + str(t.params) for t in tools.ALL)
    assert "{owner}" in words
    assert "owner_said" in next(t for t in tools.ALL if t.name == "take_on").params
    assert "message_owner" in MODES and "suggest_priority" in MODES
    assert "suggest_anchor" not in MODES and "suggest_anchor" not in words
    for t in tools.ALL:  # every placeholder is one the runtime knows how to fill
        t.description.format(owner="Sam")
        assert "Sam" not in t.description


def test_every_rule_is_a_named_function_registered_once():
    names = [name for name, _, _ in tools.rules()]
    assert len(names) == len(set(names))
    for name, setting, fn in tools.rules():
        assert callable(fn), name
        # The answer to an outside client is limited by the MCP door's own settings, and
        # the browser by its own and by web lookup's list of allowed private hosts.
        assert setting is None or setting.startswith(
            ("village.", "door.", "hands.", "browser.", "lookup.")), name
    for rule in ("files_confined", "shrink_guard", "file_limits", "shared_write_own_only",
                 "append_duplicate", "edit_exact_once", "edit_already_made", "rest_daytime",
                 "rest_on_task", "task_known", "resume_wait", "already_done",
                 "priority_never_finishes", "evidence_own_ok_actions", "owner_asked",
                 "requests_taken_per_day", "own_duplicate", "own_tasks_per_day",
                 "own_tasks_open", "review_distance", "third_pass_unchanged", "reviewed_as_is",
                 "date_slip", "handovers_per_day", "finding_twin", "finding_room",
                 "findings_open_hold", "urgent_findings_per_day",
                 "priority_suggestions_per_day", "chat_messages_per_day",
                 "breakroom_per_hour", "say_too_long"):
        assert rule in names, rule


# --- diary, remember ---------------------------------------------------------------------


def test_write_diary_appends_the_agents_own_words(v):  # noqa: F811
    d = call(v, "ada", "write_diary", {"text": "A good morning."}, mode="outlet")
    assert d.result == "wrote 15 characters in your diary" and d.category == "self"
    assert diary_lines(v, "ada") == [{"time": "10:00", "text": "A good morning.",
                                      "dream": False}]  # on the owner's clock, not UTC


def test_remember_makes_a_candidate_citing_the_last_three_actions(v):  # noqa: F811
    ids = [call(v, "ada", "write_file", {"path": f"n{i}.md", "content": "x"}).event.id
           for i in range(4)]
    d = call(v, "ada", "remember", {"section": "tools", "text": "edit_file needs exact text"})
    assert d.result == "noted; sleep will decide whether it stays"
    assert candidates(v, "ada") == [{"section": "tools", "text": "edit_file needs exact text",
                                     "evidence": ids[-3:],
                                     "ts": round(v.clock.now() * 1000)}]


# --- rest ----------------------------------------------------------------------------------


def test_rest_in_the_daytime_with_energy_is_refused(v):  # noqa: F811
    e = refused(v, "ada", "rest", {"minutes": 60})
    assert e.rule == "rest_daytime"
    assert e.sentence.startswith("Nothing happened, you are not resting: it's 10:00 and you "
                                 "have energy; rest is for the evening (for you, from 16:00)")
    assert "start_own_task" in e.sentence
    assert v.world.agents["ada"].mode == "work"


def test_rest_is_allowed_when_tired_and_runs_20_to_180_minutes(v):  # noqa: F811
    v.world.agents["ada"].needs["energy"] = 30
    d = call(v, "ada", "rest", {"minutes": 5})
    assert d.result.startswith("resting for 20 minutes: no actions until then. From 20:00")
    assert d.result.endswith("A message from Sam still reaches you")
    assert v.world.agents["ada"].mode == "rest" and d.args_logged == {"minutes": 20}
    v.world.agents["bo"].needs["energy"] = 30
    assert call(v, "bo", "rest", {"minutes": 999}).result.startswith("resting for 180 minutes")


def test_rest_defaults_to_an_hour(v):  # noqa: F811
    v.world.agents["ada"].needs["energy"] = 30
    assert call(v, "ada", "rest", {}).result.startswith("resting for 60 minutes")


def test_rest_on_a_task_is_refused_but_a_standing_priority_is_no_task(v):  # noqa: F811
    from jimmyhq.village.comms import tasklist

    v.world.standing = lambda agent_id: tasklist.standing(v, agent_id)
    v.world.agents["ada"].needs["energy"] = 30
    call(v, "ada", "start_own_task", {"title": "Tidy the notes"})
    e = refused(v, "ada", "rest", {"minutes": 30})
    assert e.rule == "rest_on_task"
    assert e.sentence == ("Nothing happened, you are not resting: you're on T1: finish it or "
                          "set_aside it first.")
    prioritize(v, "Draft newsletters")
    v.world.agents["bo"].needs["energy"] = 30
    call(v, "bo", "focus_on", {"task": "P1"})
    assert call(v, "bo", "rest", {"minutes": 30}).result.startswith("resting for 30 minutes")


def test_each_rest_refusal_names_its_rule():
    assert rest_refusal("you're on T4: finish it or set_aside it first")[0] == "rest_on_task"
    assert rest_refusal("it's 10:00 and you have energy; rest is for the evening")[0] == \
        "rest_daytime"


# --- what reaches the log ------------------------------------------------------------------

KEY = "sk-" + "a1B2c3D4" * 4
SECRETS = [
    KEY,
    "ghp_" + "0123456789abcdefABCDEF" * 2,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123",
    "Bearer abcdefghijklmnop1234",
    "x9Kq2LmZ7pTr4VbN8wYc3HdF6gJs",
    "4111 1111 1111 1111",
]


def test_anything_that_looks_like_a_secret_is_taken_out_of_logged_text():
    for secret in SECRETS:
        out = logged.text(f"the value {secret} was in the page")
        assert secret not in out and "[removed]" in out, secret
    assert logged.text("the password is hunter2 for now") == "the password is [removed] for now"
    assert logged.text("api key: abc123") == "api key: [removed]"
    assert logged.looks_secret(KEY) and not logged.looks_secret("an ordinary sentence")


def test_paths_addresses_card_ids_and_plain_numbers_stay_whole():
    for keep in ("notes/2026-11-19-weekly-newsletter-draft-version-two.md",
                 "https://example.com/campaigns/2026-giving-day-last-call?ref=home",
                 "handed over as 01JABCDEFGHJKMNPQRSTVWXYZ0; you will see their answer",
                 "evt-123456 and 1234567890123456 items", "shared/ada/review_of_the_draft_v2.md",
                 "review pass 2 of drafts/welcome.md recorded"):
        assert logged.text(keep) == keep


def test_logged_arguments_are_scrubbed_whatever_their_shape():
    out = logged.args({"path": "notes/a.md", "content": f"token = {KEY}", "password": "hunter2",
                       "paths": ["a.md", KEY], "nested": {"api_key": "x", "n": 3}, "ok": True})
    assert out == {"path": "notes/a.md", "content": "token = [removed]", "password": "[removed]",
                   "paths": ["a.md", "[removed]"], "nested": {"api_key": "[removed]", "n": 3},
                   "ok": True}


def test_a_long_text_argument_is_logged_as_its_start_size_and_fingerprint():
    one, two = "a" * 5000, "a" * 4999 + "b"
    out = logged.args({"content": one})["content"]
    assert out["chars"] == 5000 and len(out["start"]) == 2000 and len(out["sha"]) == 12
    assert logged.args({"content": two})["content"]["sha"] != out["sha"]
    assert logged.args({"content": "short"})["content"] == "short"


def test_a_secret_written_to_a_file_is_not_in_the_action_log(v):  # noqa: F811
    d = call(v, "ada", "write_file", {"path": "keys.md", "content": f"the key is {KEY}"})
    assert (v.home("ada") / "keys.md").read_text(encoding="utf-8") == f"the key is {KEY}"
    assert KEY not in str(d.event.data) and "[removed]" in d.event.data["args"]["content"]
    d = call(v, "ada", "write_diary", {"text": f"password: {KEY}"})
    assert KEY not in str(d.event.data)
    e = refused(v, "ada", "edit_file", {"path": "keys.md", "old_text": "nope", "new_text": KEY})
    assert KEY not in str(e.args_logged)
    assert KEY not in str(v.log.query(agent="ada", kinds=["validator_reject"])[-1].data["args"])
    d = call(v, "ada", "read_file", {"path": "keys.md"})
    assert KEY in d.result and KEY not in logged.result(d.result)
