# The Bench

The Bench is the owner's single inbox for everything waiting on their decision: a finding
from an agent, a draft to approve before it is sent, something done on their behalf that
they can undo. Empty means done. The code is `server/jimmyhq/bench/`.

Two house rules shape it:

- a human click approves anything that leaves the building;
- only the owner's own click records a decision. A token, an agent or an app never decides.

## Cards

A card is filed by an actor and sits in one of three lanes:

| Lane | Means |
|---|---|
| `needs` | Waiting on the owner |
| `done` | Done for you: informational, with an optional Undo |
| `waiting` | The owner said "not yet", or the card is snoozed |

The filer declares the card's buttons (`actions`). Without any, a `needs` card gets
Approve, Not yet and No (No needs a typed reason), and a `done` card gets Got it, plus Undo
when filed with `undo=True`. The action ids `approve`, `not_yet`, `no`, `ok` and `undo` are
the Bench's own buttons and have a fixed meaning: the ladder tallies decisions by them. A
filer may list them among its own actions, but only exactly as the Bench defines them
(label, style, `needs_reason`, `closes`, `moves_to`), and no other action may borrow one of
their labels (Approve, Not yet, No, Got it, Undo). Labels are compared by their letters and
digits alone, so case, spacing, punctuation, full-width letters and characters that do not
show make no difference; letters from another alphabet that merely look the same are not
caught. Anything else is refused with a 422, so the button the owner reads and the action id
on the Tape always mean the same thing. A card already on the Tape from before this was
checked, whose buttons break the rule, cannot be decided (409): it has to be withdrawn and
filed again.

A decision either closes the card, moves it to another lane, or just records the click. A
closed card (decided or withdrawn) leaves the lanes and stays readable by id.

Snoozing hides a `needs` card until a time. Nothing wakes it: the lane is worked out when
the Bench is read, so the card is simply back once the time has passed.

The quick snooze choices (Later today, Tomorrow morning, Next week) are worked out on the
server and returned by `GET /api/bench` as `snooze_choices`, each `{id, label, until}` with
`until` in unix milliseconds, next to `timezone` (the owner's IANA zone name, or `local` for
the server's own). They follow the settings `bench.snooze_later_hours`,
`bench.snooze_morning_hour`, `owner.week_starts` and `owner.timezone`; the browser's clock
and zone play no part, and the screen shows what the server offers.

## Using it from another module

```python
from jimmyhq import bench

card_id = bench.file_card(
    core, filed_by="agent:scout", kind="posts.draft", source="Posts",
    title="Draft ready", summary="A post about the spring release", body=text,
    subject="post:42",
)
bench.update_card(core, card_id, by="agent:scout", note="Shortened the intro.")
bench.withdraw_card(core, card_id, by="agent:scout")
card = bench.get_card(core, card_id)          # None when there is no such card

bench.on_decision("posts.", lambda core, decision: ...)
```

`on_decision` runs in the process where the click arrived (the web process) and only while
it is up. Anything that must not miss a decision, or that runs in the worker, reads
`bench.decided` events from the Tape instead: the payload carries the card's kind, subject,
the action and the reason, `filed_by` and `updates`, and its `cause` is the `bench.filed`
event.

Before acting on a decision, check who filed the card: `decision.filed_by` (or that
`decision.card_id` is a card you filed yourself). Any write token can file a card under any
kind, source and subject, so the kind alone does not prove the card is yours.

A decision is for one revision of the card. `updates` in `bench.decided` (and
`decision.updates`) is how many times the card had been updated when the owner clicked; act
on that revision, not on whatever the card says later. Over HTTP the owner's click must name
the revision it saw (`seen_updates`), and is refused with a 409 if the filer has updated the
card since, so nobody approves words they have not read.

Two processes can act on one card at the same instant (the owner approves in the web
process while the worker withdraws). Every write about a card is guarded on the Tape: the
first one wins, and the loser is refused with a 409 and nothing is printed. A
`bench.decided` or `bench.withdrawn` printed from now on is therefore one that took effect.
A Tape written before this guard can still hold a closing event after the one that closed
the card; a reader of raw events ignores those, as the card fold and the ladder do.

A card takes at most 1000 updates. After that, withdraw it and file a new one.

Problems are raised as `bench.BenchError` with a `status` and a one-sentence `detail`.

Do not print `bench.*` or `ladder.*` events yourself.

## Where things are stored

- Every card event has the Tape subject `bench:<card id>`.
- The `bench` lens holds open cards in list form and the last 200 decisions. It never holds
  a body. `GET /api/bench` is one read of it.
- A body of up to 4 KB travels in its event; a longer one goes to the Vault and the event
  keeps the reference.
- The full card is rebuilt from its own events (one indexed read by subject), which is why
  closed cards stay readable without staying in lens state.
- A `done` card nobody dismissed leaves the lane after the `bench.done_expire_days` setting
  and leaves the lens after a year.

## The permission ladder

`ladder.py` answers one question: may this actor do this without asking?

A row is `(actor, tool, action)` at a level: `read`, `approve` (may act once the owner
approves on the Bench) or `alone` (may act, and files a `done` card saying so).

```python
level = bench.check(core, "agent:scout", "posts", "publish", category="publish")
if level == "alone":
    publish()
    bench.record_alone_action(core, actor="agent:scout", tool="posts", action="publish",
                              category="publish", title="Published the post", undo=True)
elif level == "approve":
    bench.file_card(core, filed_by="agent:scout", kind="posts.draft", source="Posts",
                    title="Publish this?", ladder={"actor": "agent:scout", "tool": "posts",
                                                   "action": "publish", "category": "publish"})
```

The rules are enforced in the functions and again in the lens fold:

- a row nobody has set is `approve`;
- only the owner can raise a level; anything can lower one;
- a No or an Undo on a card that belongs to an `alone` row drops the row to `approve`;
- nothing is barred from `alone`. The owner can raise any row in one step, with no second
  question and no limit that must be set first. The setting `bench.caution_categories`
  (`send`, `delete`, `spend`, `access` unless the owner changed it) only says which kinds
  of action a screen should start at "ask" and mark as cautious (`cautious` on a row);
- a No or an Undo on an `alone` row drops it to `approve`, unless the owner switched off
  `bench.undo_drops_alone`. The answer of that moment is written into `bench.decided`
  (`keeps_level`), so a replay reads it the same whatever the setting says later.

**A replay never grants what was once refused.** Four categories (`money`,
`message_person`, `delete`, `access`) could once never be `alone`. A `ladder.set` from that
time carries no `unbarred` mark, and the fold still passes over an `alone` in one of those
categories from such an event. Only a level the owner sets now (`unbarred: true`) counts.

Pass `ladder=` when filing a card for an action, so the owner's decision is tallied on the
right row. Only a decision that took effect is tallied: the ladder lens follows which cards
with a row are still open and passes over a `bench.decided` about a card already closed.
After `bench.promotion_streak` approvals in a row (10 unless changed) the row is marked
`could_promote`. That is a suggestion for a screen to show; nothing is raised by itself.

## Routes

All under `/api/bench`. Reading needs the owner or a read token; filing, updating and
withdrawing need the owner or a write token (a token files only as itself, changes only
its own cards, and cannot attach `files`: it has no way to store a Vault blob of its own, so
that is a 403); deciding, snoozing and setting ladder levels need the signed-in owner.

| Method and path | Does |
|---|---|
| `GET /api/bench` | The three lanes, counts, recent decisions, `snooze_choices`, `timezone` |
| `GET /api/bench/{id}` | One card in full, with its history |
| `GET /api/bench/files/{id}/{index}` | One attached file, as a download |
| `POST /api/bench` | File a card |
| `POST /api/bench/{id}/update`, `/withdraw` | The filer's changes |
| `POST /api/bench/{id}/decide`, `/snooze`, `/unsnooze` | The owner's clicks. `decide` takes `action`, `seen_updates` (required: the card's `updates` as read), `note`, `reason` |
| `GET /api/bench/ladder`, `PUT /api/bench/ladder` | Read the ladder, set a level |
