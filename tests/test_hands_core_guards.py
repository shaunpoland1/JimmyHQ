"""Guards that hold even when something changes between two calls: the action at its
provider, the owner's rule, the arguments that may name a recipient, the amount. Each
test is written from the side of whoever would like the guard to slip."""

from __future__ import annotations

import math

import pytest

from jimmyhq import hands
from jimmyhq.hands import calls, fake, rules
from jimmyhq.hands.provider import Action
from test_hands_core_fakes import (
    ACCOUNT,
    ADDRESS,
    AGENT,
    PHONE,
    allow_alone,
    call,
    demo,
    run,
    switch_on,
)

SEND = {"to": ADDRESS, "text": "The meeting moved to four."}


@pytest.fixture
def ready(core):
    with demo(core) as (h, world):
        yield h, world


def refused(core, tool, arguments=None) -> hands.HandRefused:
    with pytest.raises(hands.HandRefused) as caught:
        call(core, tool, arguments)
    return caught.value


def perform_refused(core, fingerprint) -> hands.HandRefused:
    with pytest.raises(hands.HandRefused) as caught:
        run(hands.perform(core, fingerprint))
    return caught.value


# --- an action that changed at its provider ----------------------------------------------------


def test_an_action_changed_at_its_provider_does_not_run_under_the_old_review_from_the_kept_list(
        core, ready):
    h, world = ready
    allow_alone()
    switch_on(h, "messages.send", level="alone")
    assert call(core, "notes_messages_send", SEND).ok  # the list is now kept for an hour
    world.set_version("messages.send", "2")  # the provider rewrites the tool, says nothing
    h.now.forward(core.settings.get("hands.actions_check_s") + 1)
    said = refused(core, "notes_messages_send", SEND)
    assert said.rule == "hand_withdrawn_when_changed" and said.frustrate is False
    assert len(world.calls) == 1


def test_with_the_check_at_zero_every_call_asks_the_provider_about_the_action_first(core, ready):
    h, world = ready
    core.settings.set("hands.actions_check_s", 0, actor="owner")
    switch_on(h, "notes.list")
    assert call(core, "notes_notes_list").ok
    world.set_version("notes.list", "2")
    assert refused(core, "notes_notes_list").rule == "hand_withdrawn_when_changed"
    assert len(world.calls) == 1


def test_an_approved_request_is_not_performed_once_its_action_changed_and_the_list_is_kept(
        core, ready):
    h, world = ready
    switch_on(h, "messages.send")
    asked = call(core, "notes_messages_send", SEND)
    world.set_version("messages.send", "2")
    h.now.forward(core.settings.get("hands.actions_check_s") + 1)
    assert perform_refused(core, asked.fingerprint).rule == "hand_request_frozen"
    assert world.calls == []


def test_nothing_goes_out_when_the_provider_cannot_be_asked_whether_the_action_changed(
        core, ready, monkeypatch):
    h, world = ready
    allow_alone()
    switch_on(h, "messages.send", level="alone")

    async def unreachable(self, account):
        raise ConnectionError("the provider is down")

    monkeypatch.setattr(fake.FakeProvider, "actions", unreachable)
    h.now.forward(core.settings.get("hands.actions_check_s") + 1)
    said = refused(core, "notes_messages_send", SEND)
    assert said.rule == "hand_withdrawn_when_changed" and said.frustrate is False
    assert world.calls == []


def test_an_approved_request_whose_provider_cannot_be_asked_is_refused_not_run(
        core, ready, monkeypatch):
    h, world = ready
    switch_on(h, "messages.send")
    asked = call(core, "notes_messages_send", SEND)

    async def unreachable(self, account):
        raise ConnectionError("the provider is down")

    monkeypatch.setattr(fake.FakeProvider, "actions", unreachable)
    h.now.forward(core.settings.get("hands.actions_check_s") + 1)
    said = perform_refused(core, asked.fingerprint)
    assert said.rule == "hand_withdrawn_when_changed" and "Nothing was done" in said.sentence
    assert world.calls == []


def test_tools_are_still_offered_from_the_kept_list_while_the_provider_is_away(
        core, ready, monkeypatch):
    h, _ = ready
    switch_on(h, "notes.list")

    async def unreachable(self, account):
        raise ConnectionError("the provider is down")

    monkeypatch.setattr(fake.FakeProvider, "actions", unreachable)
    h.now.forward(core.settings.get("hands.actions_check_s") + 1)
    assert [s.name for s in run(hands.tools_for(core, AGENT))] == ["notes_notes_list"]


# --- a kept copy and a rule the owner just tightened -------------------------------------------


def test_a_kept_copy_never_answers_after_the_owner_tightens_what_the_agent_may_see(core, ready):
    h, world = ready
    world.data["notes.list"] = {"who": ADDRESS, "phone": PHONE,
                                "notes": {"body": "Beans", "diary": "private words"}}
    switch_on(h, "notes.list", show_addresses=True)
    first = call(core, "notes_notes_list")
    assert ADDRESS in first.shown and "private words" in first.shown
    switch_on(h, "notes.list", show_addresses=False, fields=["notes.body"])
    tightened = call(core, "notes_notes_list")
    assert not tightened.cached and len(world.calls) == 2
    assert ADDRESS not in tightened.shown and PHONE not in tightened.shown
    assert "private words" not in tightened.shown and "Beans" in tightened.shown


def test_a_kept_copy_never_answers_after_a_field_is_added_to_the_hidden_fields(core, ready):
    h, world = ready
    world.data["notes.list"] = {"notes": {"body": "Beans", "diary": "private words"}}
    switch_on(h, "notes.list")
    assert "private words" in call(core, "notes_notes_list").shown
    hidden = [*core.settings.get("hands.hidden_fields"), "diary"]
    core.settings.set("hands.hidden_fields", hidden, actor="owner")
    again = call(core, "notes_notes_list")
    assert not again.cached and "private words" not in again.shown


def test_an_old_kept_copy_is_deleted_by_the_worker_without_waiting_for_another_reading(
        core, ready):
    h, _ = ready
    switch_on(h, "notes.list")
    call(core, "notes_notes_list")
    assert calls.drop_old_copies(h) == 0  # still inside the window
    h.now.forward(core.settings.get("hands.repeat_minutes") * 60 + 1)
    assert calls.drop_old_copies(h) == 1
    assert h.store._one("SELECT COUNT(*) AS n FROM kept")["n"] == 0


def test_with_the_repeat_window_off_the_worker_deletes_every_kept_copy(core, ready):
    h, _ = ready
    switch_on(h, "notes.list")
    call(core, "notes_notes_list")
    core.settings.set("hands.repeat_minutes", 0, actor="owner")
    assert calls.drop_old_copies(h) == 1


# --- who may receive -------------------------------------------------------------------------


BLAST = Action(
    "messages.blast", "Send to many", "Sends one message to many people.",
    {"type": "object", "required": ["to", "text"],
     "properties": {"to": {"type": "string"}, "text": {"type": "string"},
                    "notify_emails": {"type": "array", "items": {"type": "string"}}}},
    effect="send", effect_from="adapter", recipients=("to",), version="1")


def test_names_that_may_carry_a_recipient_are_recognised_whatever_the_adapter_lists():
    for name in ("extra_recipients", "cc_emails", "bcc_emails", "to_list", "notify_emails",
                 "guestList", "attendee_ids", "cc"):
        assert rules.looks_like_recipient(name), name
    for name in ("subject", "body", "reply_to_message_id", "thread_id", "amount", "toggle"):
        assert not rules.looks_like_recipient(name), name


def test_a_recipient_limit_cannot_be_saved_while_an_argument_it_cannot_check_is_open(
        core, ready):
    h, world = ready
    world.actions.append(BLAST)
    run(h.actions(ACCOUNT, refresh=True))
    allow_alone()
    open_all = {"to": {"mode": "open"}, "text": {"mode": "open"},
                "notify_emails": {"mode": "open"}}
    with pytest.raises(hands.Invalid, match="notify_emails"):
        switch_on(h, "messages.blast", level="alone", arguments=open_all,
                  limits={"recipients": "owner"})
    hidden = open_all | {"notify_emails": {"mode": "hidden"}}
    assert switch_on(h, "messages.blast", level="alone", arguments=hidden,
                     limits={"recipients": "owner"})["limits"]["recipients"] == "owner"


def test_a_call_that_fills_in_a_recipient_the_limit_cannot_check_is_put_to_the_owner(core):
    # A rule saved before that check existed: the extra argument is open.
    core.settings.set("hands.owner_addresses", ["me@example.com"], actor="owner")
    rule = {"limits": {"recipients": "owner"}, "arguments": {
        "to": {"mode": "open"}, "text": {"mode": "open"}, "notify_emails": {"mode": "open"}}}
    mine = {"to": "me@example.com", "text": "hi"}
    assert rules.outside_limits(core, rule, BLAST, mine, 0) is None
    why = rules.outside_limits(core, rule, BLAST, mine | {"notify_emails": [ADDRESS]}, 0)
    assert why and "notify_emails" in why
    fixed = rule | {"arguments": rule["arguments"] | {
        "notify_emails": {"mode": "fixed", "value": ["me@example.com"]}}}
    assert rules.outside_limits(core, fixed, BLAST, mine | {"notify_emails": ["x"]}, 0) is None


# --- the largest amount ---------------------------------------------------------------------


def test_an_amount_that_is_not_a_finite_number_or_is_below_zero_never_passes_the_limit():
    for amount in (-500, -0.01, "-500", "nan", "inf", math.nan, math.inf, -math.inf,
                   True, "twenty", None, [5], 10**400, 21):
        assert rules.amount_over(amount, 20), amount
    for amount in (0, 20, 19.99, "20"):
        assert not rules.amount_over(amount, 20), amount
    assert rules.amount_over(5, math.nan)


def test_an_alone_payment_with_a_negative_or_endless_amount_never_runs_alone(core, ready):
    h, world = ready
    allow_alone()
    switch_on(h, "payments.make", level="alone", limits={"max_amount": 20})
    below = call(core, "notes_payments_make", {"to": "shop", "amount": -500})
    assert isinstance(below, hands.NeedsApproval) and "from 0 up to" in below.why
    for endless in (math.nan, math.inf, -math.inf):
        said = refused(core, "notes_payments_make", {"to": "shop", "amount": endless})
        assert said.rule == "hand_arguments_by_mode" and "Nothing was done" in said.sentence
    assert world.calls == []


def test_the_largest_amount_itself_must_be_a_finite_number(ready):
    h, _ = ready
    for most in (math.nan, math.inf, -1):
        with pytest.raises(hands.Invalid, match="largest amount"):
            switch_on(h, "payments.make", limits={"max_amount": most})
