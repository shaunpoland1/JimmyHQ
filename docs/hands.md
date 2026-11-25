# Hands

`server/jimmyhq/hands/` is the one way an agent (and later chat) uses an outside
integration: a mailbox, a calendar, a tool server. It is shown to the owner as
**Integrations**. This page covers the core, which is the same for every provider, and the
three adapters that ship with it: [AgentMail](#agentmail), [Composio](#composio) and
[MCP servers](#mcp-servers).

**Where things stand.** The owner can add a provider, connect an account, switch actions
on for an agent and try a reading. An agent is then offered one tool for each action
switched on for it (`village/tools/hands.py`). A request that needs the owner's yes waits
as a card on the Bench, unless the rule's level lets it go alone, and the worker performs
what the owner approved (`execute.run`). Chat does not use integrations yet. The functions
below are what the tools and the worker call.

A **provider** is the service that holds accounts and can act on them. An **account** is
one of its accounts, connected under a short name the owner chose (`mail`). An **action**
is one thing an account can do (`messages.send`). A **hand** is one action of one
account, switched on by the owner for one agent. Nothing is offered to anybody until the
owner switches it on.

```python
from jimmyhq import hands

specs = await hands.tools_for(core, "ada")                 # what to offer this agent now
out = await hands.call(core, agent="ada", tool="mail_messages_list", arguments={})
if isinstance(out, hands.NeedsApproval):                   # nobody was called
    card_id = ...                                          # file a card naming out.arguments, not their values
    hands.attach_card(core, out.fingerprint, card_id)
# later, once the owner said yes to that card:
done = await hands.perform(core, out.fingerprint)          # exactly that request, once
```

| Function | What it does |
|---|---|
| `tools_for(core, agent) -> list[ToolSpec]` | The tools one agent is offered now: name, description, open arguments, effect, level |
| `call(core, *, agent, tool, arguments=None, actor=None, cause=None, root=None)` | Runs it (`Called`) or freezes it for the owner (`NeedsApproval`) |
| `perform(core, fingerprint, *, card_id=None, actor=None, cause=None, root=None) -> Called` | Runs one frozen request, once, if nothing about it changed |
| `pending(core, fingerprint)`, `attach_card(core, fingerprint, card_id)`, `decline(core, fingerprint)` | Read a request, name its card, close it for good |
| `undo(core, run_id, *, actor="owner") -> Called` | Takes back a finished run whose action can be taken back |
| `settle_unknown(core)`, `unknown_runs(core, *, carded=None)`, `mark_carded(core, run_id, card_id)` | Runs cut short by a crash |
| `inbound(core, account, cursor)`, `shown_inbound(core, item, *, show_addresses=False, start=0)` | What arrived for an account, and how a model may see it |
| `set_level_gate(gate)`, `may_set_level(core, ...)`, `owner_decides()` | The hook that decides whether a level may be set, and the ladder's answer to it |
| `execute.run(core)`, `execute.file_done(core, run_id, *, approved)` | Act on the owner's decisions since the cursor; file the "done" card for a run |
| `register_type(ProviderType(...))` | Makes a type of provider available |

A call that will not be made raises `HandRefused(rule, sentence, frustrate)`. Nothing
happened. The sentence is for the agent: it says so, gives the number, and says what to do
instead. `frustrate` is False for a limit or a missing set-up, where the agent did
nothing wrong.

## The rule table

One rule for each **agent, account, action** (`hands.rule_set`). It holds:

| Part | Meaning |
|---|---|
| `level` | `read`, `ask` or `alone` (below) |
| `arguments` | Each argument is `open` (the agent sets it), `fixed` (the owner's value) or `hidden`. Required ones start open, the rest hidden |
| `limits` | `per_day`; `recipients` (`owner`, `known`, `anyone`); `max_amount` (below) |
| `show_addresses`, `fields` | What of an answer the agent sees |
| `effect`, `effect_from` | As the provider said, or as the owner corrected |
| `version` | The action's version when the owner looked |

| Level | A reading action | A changing action |
|---|---|---|
| no rule | not offered | not offered |
| `read` | runs | refused: "you may look, not do this" |
| `ask` | waits for the owner | waits for the owner |
| `alone` | runs | runs inside its limits; outside them it waits for the owner |

**The level a new rule starts at.** A reading starts at `hands.default_level_read`
(`alone`) only when its being a reading is *trusted*: the owner said so, or the adapter did
and its type declares its hints trusted (`ProviderType.trusted_hints`). Composio's hints
are best effort and an MCP server's are the server's own word, so neither type is trusted:
a reading from them starts at `ask`. Every other effect starts at
`hands.default_level_change` (`ask` unless the owner changed it), except that a cautious
kind never starts at `alone`. The cautious kinds are the setting
`bench.caution_categories`: `send`, `delete`, `spend` and `access` unless the owner changed
the list. The list picks where a rule starts and nothing else.

A provider's claim is a suggestion and only ever picks what the form starts at. The owner
can still choose `alone` for a reading. The list of actions says where the claim came
from (`effect_from`), whether it is trusted (`effect_trusted`) and, when a reading does
not start at `alone`, why (`level_note`). Nothing is switched on by listing, connecting or
asking: only by the owner saving a rule.

**The owner may set any action to `alone`, in one step.** No second confirmation and no
limit that must be set first; limits stay available on every rule. Setting a level goes
through `may_set_level(core, *, agent, account, action, effect, level, limits, confirmed)`,
which answers None for yes or a sentence for no. The answer is the Bench ladder's
(`bench.ladder.owner_may_choose_any_level`), handed in with `set_level_gate` when the API
is built (`hands.owner_decides()`). The core by itself, with no answer handed in, still
refuses `alone` for every effect but `read`.

Only the owner switches an action on or gives it more freedom: the routes need the
signed-in owner, and `set_rule` refuses any other actor that tries to raise a level
(`hand_only_owner_raises`). Anything may lower one (`Hands.lower_rule`).

**A No or an Undo takes `alone` back.** When the owner says No to a request from a rule
at `alone` (it asked because it was outside a limit), or undoes something it did, the rule
goes back to `ask`, unless the owner switched off `bench.undo_drops_alone`.

**A suggestion, never a promotion.** Every card about a rule is tallied on the Bench
ladder row `(agent:<id>, hands, <tool>)`. `GET /api/hands/rules` gives each rule's
`approvals_in_a_row` and `suggest_alone`, true after `bench.promotion_streak` approvals in
a row (10) while the rule still asks. The screen shows a line. Nothing sets a level.

What `call` checks, in order:

1. **A rule.** No rule for this agent and tool: refused.
2. **Unchanged.** The action's version is the pinned one, its account is connected and
   its provider is on. Otherwise the tool is withdrawn. The version is checked against
   the provider's list as it is now: a list kept longer than `hands.actions_check_s`
   (60 seconds) is asked for again, and if the provider cannot be asked, nothing goes
   out (`hand_checked_fresh_before_running`). The longer `hands.actions_cache_s` only
   decides which tools are offered.
3. **Arguments.** The model's arguments are checked against the open ones and their
   types. Naming a fixed or hidden argument, or one the action does not have, is refused:
   a model can never set one. A tool takes flat arguments only (text, a number, yes or
   no, one of a list, a list of text), so an action with an open argument of another
   shape "cannot be offered yet" until the owner fixes or hides it. The fixed values are
   then added.
4. **Level**, as in the table. `ask`, and `alone` outside a limit, return
   `NeedsApproval` and call nobody.
5. **Repeat.** A reading with the same arguments within `hands.repeat_minutes` is
   answered from the kept copy with no new call. Never for a change. The copy is the
   shaped answer, so it is filed under the whole rule and the settings that shape it
   (`hands.hidden_fields`, `hands.value_chars`): once the owner changes the rule or
   hides another field, the next reading is a fresh call. The worker's `hands.settle`
   job deletes copies older than the window.
6. **Caps.** `hands.calls_per_day` for one agent on one account,
   `hands.calls_per_month` for one provider, and the rule's own `per_day`. Counted from
   the run ledger, so a failed call counts and a restart loses nothing.
7. **Run, once.** See below.

**The limits on an action that runs alone.** "Who may receive" checks the arguments the
adapter names as recipients. An open argument whose name suggests it names someone
(it starts with `to`, or has `cc`, `bcc`, `recipient`, `email`, `attendee`, `invitee`,
`participant` or `guest` in it) and is not among them cannot be checked: a rule with
an `owner` or `known` limit may not leave one open, and a call that fills one in asks
the owner. "The largest amount" lets through only a finite number from 0 up to the
limit; anything else (below zero, `nan`, not a number) asks. A number argument is never
`nan` or infinite.

An agent holds at most `hands.max_tools_per_agent` tools. A tool is named
`<account>_<action>` in lower-case letters, digits and underscores, 64 characters at most.

## Asking, and performing what was approved

`NeedsApproval` carries the final arguments, fixed values included, and a `fingerprint`:
a SHA-256 over the request's id, the agent, account, action, pinned version, a hash of the
whole rule, and every argument. The arguments wait in `hands.db`; `hand.proposed` on the
Tape carries the fingerprint and the argument names.

`perform(fingerprint)` is given no arguments. It loads the stored ones and refuses unless
the request still waits, is younger than `hands.request_expires_h`, its stored row still
hashes to the fingerprint, `hand.proposed` on the Tape carries the same fingerprint, the
rule is the same rule, and the action is still the pinned version. So what runs is what
the card showed, or nothing. The caller is responsible for the approval itself; this
module never reads a card.

Once performed, declined or expired, the stored arguments are erased from `hands.db`.

**The card holds no values.** The village files the request's Bench card
(`village/tools/hands.py`, `card_fields`) with the action, the account, each argument's
name and size, how many receive it, whether there is an amount, and the fingerprint:
nothing an argument says. A card is a `bench.filed` event and the Tape keeps it for ever,
so the values stay in `hands.db` alone, where answering the request erases them. The
village keeps no copy either: `village.db` holds no first body for a request's card, and
the agent's own log keeps each argument's name and size only.

The owner still reads every value before approving. The Bench card of a request
(`village.hand`) shows them from `GET /api/hands/requests/by-card/{card_id}`, which only
the signed-in owner may call (a read or write token gets 403). While the request waits it
returns `{card_id, agent, account, action, tool, waiting: true, arguments}`; once it is
performed, declined, withdrawn or gone with its account it returns `waiting: false` and
`arguments: null`, and the card says these details are no longer kept. A card no request
is tied to is a 404.

**Cards filed before this change are not rewritten.** Earlier request cards carry their
values in the `bench.filed` event, and earlier village rows keep the first body. The Tape
is append-only and an old card's values stay there until the owner erases that card's
events (`Tape.erase`); erasing a `bench.filed` event also drops the card from the
approval count behind "suggest Alone", since that count only tallies decisions on cards
whose filing it saw.

## The executor

`hands/execute.py` is what happens after the owner's click. It runs in the worker
(`hands.execute`, every few seconds) and reads `bench.decided` and `bench.withdrawn` from
the Tape by a cursor kept in `hands.db`.

| The owner's click | What the executor does |
|---|---|
| Approve on a request (`village.hand`) | `perform(fingerprint, card_id=...)`: precisely the frozen request. No model is asked and nothing is rebuilt |
| No, or "Not this time" | `decline(fingerprint)`: the request is closed and its arguments erased |
| Undo on a `hand.done` card | `undo(run_id)`, once |

A decision counts only when the owner made it on a card this module tied to a request or
a run for that agent (`hand_decision_trusted`): any write token can file a card under any
kind.

What became of a request is printed once as `hand.outcome` (`done`, `failed`, `unknown`,
`refused`, `declined`; `undone` and `undo_failed` for an undo). Whoever asked reads it:
the village turns it into the answer on the agent's card. It carries sentences JimmyHQ
wrote and the owner's own note, never what a provider said.

The owner gets a card for each ending, written from the receipt (names, sizes, counts,
the provider's reference; never a value):

- `hand.done`: it was done, as approved or without asking. Undo is there when the provider
  can undo.
- `hand.not_done`: approved, and then refused at perform time (the rule was edited, the
  action's definition changed, the request was too old) or refused by the provider. The
  card says which.
- `hand.unknown`: a run nobody saw end. It may or may not have happened and is never
  tried again. One card per run, with "I have checked".

**Once, across processes.** Two processes may read the same decision. The ledger takes
the request's fingerprint as the run's key and a key starts once, so the provider is
called once. The outcome is printed with the Tape's check on the request's subject, so
it is told once. A crash before the cursor moves means the decision is read again: the
run is found `started`, nothing is called, and when the sweep settles it as unknown the
card and the outcome follow.

## Exactly once

Every run has a key: a new one for a direct call, the fingerprint for an approved
request, `undo:<run id>` for an undo. `hands.db`, table `runs`, gets a row `started`
before the provider is called and is settled `done` or `failed` after. A key that ever
started is never started again, whatever became of it.

- The provider says no: `failed`.
- A reading breaks or times out: `failed`. It changed nothing.
- A change breaks or times out (`hands.run_timeout_s`): `unknown`. It may have happened.
- The process dies in between: the row stays `started`. The worker's `hands.settle` job
  finds it once it is older than any call could be, marks it `unknown` and prints its
  receipt with `ok: null`.

An unknown outcome is never retried. `unknown_runs(core, carded=False)` lists the ones
that still need a card telling the owner.

## What a model is shown

`Called.result` is a line this module wrote: safe to log. `Called.shown` is the content,
once, between two lines that carry a mark made for that result, under a sentence saying
it is untrusted data and not instructions. Before that the answer is shaped
(`hands/shape.py`), in this order:

1. Fields named in `hands.hidden_fields` are dropped, at any depth.
2. Values that look like a key, a token, a written-out password or a card number are
   taken out.
3. Email addresses and phone numbers are hidden unless the rule's `show_addresses` is on
   (on by default only for an account marked as an agent's own inbox).
4. If the rule lists `fields`, nothing else is kept.
5. One value is cut at `hands.value_chars`, saying how much was left out.
6. The whole is shown `hands.result_chars` at a time, with the `start` that reads on.

**Inbound is data.** `shown_inbound` frames something that arrived the same way. Who it
is from is what the provider reports, shown inside the frame beside the provider's own
sender check in plain words ("sender check passed", "sender check FAILED", "sender not
checked"; see [AgentMail](#agentmail)). No level, limit or rule reads content, so a
message cannot change one, whatever its sender line says.

**A note outside the frame.** An adapter may add `Outcome.note`: a sentence it wrote
itself, from fixed wording, never from the provider's words or a sender's. The agent is
shown it above the frame, so outside text cannot forge it. A reading that carries a note
is not kept for a repeat: the note is written fresh each time.

## What never reaches a model or the Tape

| | A model | The Tape | Logs |
|---|---|---|---|
| A provider's key or other secret | never | never (secret store only) | never |
| A fixed or hidden argument | never, by name or value | a fixed value is in the owner's `hands.rule_set` | never |
| Argument values of a call | its own open ones | names, sizes and a recipient count (a request's Bench card too; see above) | never |
| The answer | shaped, framed, once | never: its size | never |
| Addresses in an answer | hidden unless the rule shows them | never | never |
| A provider's error text | one scrubbed line | never: a kind | the exception's type only |
| The account's reference at the provider | never | never (`hands.db`) | never |
| What arrived (`Inbound`) | shaped, framed | never | never |

`hand.called` is the receipt for every call that left: agent, account, action, effect,
level, argument names and sizes, recipient count, ok (or null for unknown), the
provider's reference with any address hidden, and the card id.

## Providers and their records
A provider is a record like a model provider: `{id, type, label, base_url, enabled,
options}`, saved by `hands.provider_saved`. Its key is in the secret store as
`hands.<id>.key` and any other secret as `hands.<id>.<name>`. The record is an event on
the Tape, so nothing secret may be written in it.

**Types.** `composio` and `mcp` are registered when `jimmyhq.hands` is imported. The fake
in `hands/fake.py` is for tests and demos and is registered only when something calls
`fake.register()`. A type says what the settings screen needs, so a new one needs no
screen of its own:

| On the type | |
|---|---|
| `label`, `description`, `note` | What it is, in plain words, and one caution to show when adding it |
| `fields` | What its record takes: each a `RecordField` with a label, help, and a kind (`text`, `number`, `path`, `argv` for a program's arguments, `names` for names whose values are secrets) |
| `needs_key`, `needs_address` | Whether it needs a key or an address before it can be used |
| `tokens`, `tokens_note` | Where the sign-in tokens of its accounts live: with the `provider`, `here` in this machine's secret store, or `none` |
| `trusted_hints` | Whether what it says an action does may start a reading at `alone` |

**What may be written in options** (`hands/options.py`). Each field is checked for what
it is:

- A `names` field lists names only (environment variables, headers). Each value is a
  secret of the provider, saved under the field's prefix and the name in lower case.
- A `path` field (a program, a folder) is stored as given, however long.
- An `argv` field is stored as given, except that an argument that looks like a
  credential is refused: one with the well-known shape of a key, one that names a secret
  and gives its value (`--token=...`, `key=...`, `?api_key=...` in an address, a password
  in an address), the value after a flag that names one (`--token`, then the value), or a
  long token of mixed letters and digits. The refusal says which argument, never what it
  held, and what to do instead.
- Anything else must not look like a secret at all.

**Naming a secret in an argument.** Save the value as a secret of the provider, then
write `{secret:NAME}` where it belongs, alone or inside an argument
(`--token={secret:api_token}`). The record keeps the name. The value is put in only when
the program is started, and is taken out of anything the program sends back.
`GET /providers` lists the names a record refers to as `secrets_wanted`, so the screen
can say which are not saved yet.

**Limits.** How long an adapter waits and how much it accepts are settings in the
Integrations section, with one number for every provider of a type. A provider's own
option of the same name overrides it for that provider.

| Setting | Default | A provider's option |
|---|---|---|
| `hands.composio_timeout_s` | 30 | `timeout_s` |
| `hands.mcp_start_timeout_s` | 30 | `start_timeout_s` |
| `hands.mcp_call_timeout_s` | 60 | `call_timeout_s` |
| `hands.mcp_idle_s` | 300 | `idle_s` |
| `hands.mcp_max_message_bytes` | 4000000 | `max_message_bytes` |
| `hands.mcp_max_tools` | 200 | `max_tools` |

The core's `hands.run_timeout_s` still caps a whole run, whatever the adapter allows.

## The owner's API

Under `/api/hands`. Lists need a read token or the owner; everything else needs the
signed-in owner. No response contains a key.

| Route | |
|---|---|
| `GET /types` | The provider types this install has an adapter for, each with its description, the fields its record takes, whether it needs a key and where sign-in tokens live |
| `GET, POST /providers`; `PUT, DELETE /providers/{id}` | Provider records |
| `PUT, DELETE /providers/{id}/key`; `PUT, DELETE /providers/{id}/secrets/{name}` | Set and clear secrets |
| `POST /providers/{id}/check` | Health check |
| `GET /accounts`; `POST /accounts` | List; connect (`ready`, `redirect` with `url`, or `needs_key`) |
| `POST /accounts/{account}/finish` | After a redirect: `ready` or `waiting` |
| `DELETE /accounts/{account}/connect` | Give up a connect that was started and never finished. Touches nothing that is connected |
| `GET /accounts/{account}/dependents` | The agents whose rules depend on the account, those rules, and how many of its requests still wait: what forgetting it stops |
| `DELETE /accounts/{account}` | Forget it, with its rules and private rows |
| `GET /accounts/{account}/actions` | Each action with its effect, where that came from, whether that is trusted, the level it starts at, and whether it can be offered |
| `POST /accounts/{account}/try` | Try an action that is known to only read |
| `GET /rules?agent=`; `PUT /rules`; `DELETE /rules?agent=&account=&action=` | The rule table |
| `POST /rules/preflight` | Before saving: for `read`, `ask` and `alone`, whether this agent's rule for this action may take it, and the sentence that says why not. Asks the same gate that saving does and saves nothing |
| `GET /runs` | Recent runs: names, times and how each ended |

## The protocol

An adapter is a class with these methods (`hands/provider.py`):

```python
class HandProvider(Protocol):
    type: str
    async def check(self) -> Health                          # ok, error
    async def connect(self, app: str | None) -> Connect      # ready | redirect(url) | needs_key
    async def accounts(self) -> list[Account]
    async def actions(self, account: Account) -> list[Action]
    async def run(self, account, name, arguments, *, key: str) -> Outcome   # ok, data, ref, error
    async def undo(self, account, ref) -> Outcome                           # optional
    async def inbound(self, account, cursor) -> tuple[list[Inbound], str]   # optional
```

An `Action` carries the provider's own `name`, a `label` and `description` for the owner,
its `arguments` (a JSON Schema), its `effect` and a `version`.

**Effect** is one of `read`, `send`, `create`, `update`, `delete`, `spend`, `access` or
`change` ("it changes something, kind unknown"). `effect_from` says where that came from:
`provider`, `adapter`, `guess`, or `owner` once the owner corrected it. A provider's
effect is a suggestion. It picks a default level and nothing more.

**Version** is whatever changes when the action changes: a provider's version, or a
fingerprint of the schema and description. A rule pins the version the owner looked at.
If the provider later reports another one, the tool is withdrawn until the owner reviews
it. That is what stops a tool server from rewriting a description after it was approved.

## Writing an adapter

1. Write the class. Take the HTTP client from `Wiring.http` when it is given, so tests
   can pass a fake. Read the key from `Wiring.key` and any other secret of this provider
   with `Wiring.secret("name")`. Never put either in an error, a log line or a result.
2. Declare each wait and size as a setting in `hands/settings.py` and read it with
   `Wiring.limit(options, "timeout_s", "example_timeout_s", low=1, high=300)`: the
   provider's own option when it has one, else the owner's setting `hands.<name>`. Do not
   keep a default of your own.
3. Give the type a `register()` that calls
   `register_type(ProviderType("example", "Example", make, ...))`, with its description,
   fields and where its tokens live, and call it from `register_builtin` in
   `hands/__init__.py`. `make(record: ProviderRecord, wiring: Wiring)` returns the
   adapter. A new adapter is made for every use, so keep no state on it. Leave
   `trusted_hints` False unless every action's effect is written in the adapter itself.
4. `connect` answers `ready` with the account, `redirect` with the provider's own
   consent page (the owner opens it, and JimmyHQ then looks in `accounts()` for the new
   active account, so no callback address is needed), or `needs_key`.
5. `run` does the action once and returns an `Outcome`. Return `ok=False` with the
   provider's words when the provider said no. Raise only when you do not know what
   happened. Pass `key` to the provider if it can refuse a repeat of the same key.
6. State each action's effect as exactly as you can, and say `effect_from="guess"` when
   you guessed. Name the arguments that say who receives it (`recipients`) and the one
   that carries money (`amount`); the owner's limits are checked against them.
7. Offer `undo` and `inbound` only if the service really can.

`hands/fake.py` is a complete example.

## AgentMail

`hands/agentmail.py` gives each agent an email inbox of its own at AgentMail (base
`https://api.agentmail.to/v0`, header `Authorization: Bearer <key>`). Its actions are a
small fixed set written by hand, so what each one does is exact and the type's hints are
trusted.

### Setting it up

1. Settings, Integrations: add a provider of type `agentmail` with its key. Options (all
   optional): `domain` (a domain verified at AgentMail; without it AgentMail's own is
   used) and `timeout_s` (1 to 300; without it `hands.agentmail_timeout_s`).
2. **Connect an account** once for each agent that should have an address. Each connection
   makes a new inbox (`POST /inboxes`); the "inbox name" is the part before the @ (empty
   lets AgentMail choose). The screen shows each inbox's address; the Tape never holds it.
3. On the agent's page, switch on the actions it may use.
4. Under Settings, Integrations, put your own addresses in **Your own addresses**, and
   anyone the agents may write to without counting as new in **Addresses you know**.

| Protocol | AgentMail |
|---|---|
| `check` | `GET /inboxes?limit=1` |
| `connect(app)` | `POST /inboxes` with `username` (the inbox name) and `domain` |
| `accounts` | `GET /inboxes`, every page (`page_token`) |
| `actions` | The fixed set below |
| `run` | See the table below |
| `undo` | A draft: `DELETE /inboxes/{inbox_id}/drafts/{draft_id}`. A sent mail cannot be recalled |
| `inbound` | `GET /inboxes/{inbox_id}/messages` by cursor, oldest first (see below) |

| Action | Effect | Request |
|---|---|---|
| List messages | read | `GET /inboxes/{id}/messages` (`limit` 1 to 50, `page_token`) |
| Read a message | read | `GET /inboxes/{id}/messages/{message_id}` |
| Send a message | send | `POST /inboxes/{id}/messages/send` with `to`, `cc`, `bcc`, `subject`, `text` |
| Reply to a message | send | `POST /inboxes/{id}/messages/{message_id}/reply` with `to` and `text` |
| Make a draft | create, can be undone | `POST /inboxes/{id}/drafts` (never `send_at`, which would send it later) |
| Delete a draft | delete | `DELETE /inboxes/{id}/drafts/{draft_id}` |

### Who a message goes to: three classes

Every address a send or reply would go to is one of:

| Class | Who | Setting | Default |
|---|---|---|---|
| You | one of **Your own addresses** | `hands.mail_to_owner` | Alone |
| Written to before | on **Addresses you know**, or an address this agent already sent to from its inbox | `hands.mail_to_known` | Ask me each time |
| New | anyone else | `hands.mail_to_new` | Ask me each time |

A message to people in more than one class takes the most careful of them. The owner can
change any of the three in one step, with no extra confirmation and no required limit.
"Sent to before" is learned after each message leaves, and kept in `hands.db` as a digest
of the address, per agent and inbox.

Because the classes decide, a send or reply action starts at "Alone" when it is switched
on. The rule's level is a ceiling: set it to "Ask me each time" and every message asks,
to anyone. A rule's own limits (per day, who may receive) apply as well.

**Replies are settled first.** A reply is always sent with an explicit `to`, worked out
before anything is asked or sent: the message's `reply_to`, else its sender. It is put
into the arguments, so the Bench card shows exactly where it goes and an approval sends
to exactly that. A sender chose `reply_to`, so it is classed on its own merits. The
agent cannot pick a reply's recipients unless the owner opens the `to` argument. When the
message being replied to failed its sender check, its address counts as new, whatever
list it is on.

### The sender check

Each received message carries a three-way result, taken **only** from AgentMail's own
verdict, the message's `authentication_results` field (`spf`, `dkim`, `dmarc`,
`dkim_domain`, `dmarc_domain`). JimmyHQ never computes it from headers: a sender can
write any header, and AgentMail never includes `Authentication-Results` in the `headers`
map anyway.

- **passed**: `dmarc` is `pass` for the From domain, or `dkim` is `pass` for a domain
  aligned with it (the same domain or a parent of it);
- **failed**: a verdict was given and neither of those holds (including `none`);
- **unknown**: no verdict (AgentMail gives none on sent mail or on mail from before it
  began to), or only a temporary error.

What an agent sees: the bare From address with the result beside it in plain words, the
display name in a field of its own (a name can say anything), a line saying where the
message stands, and a note from JimmyHQ above the frame. The note is fixed wording chosen
only by the From address, the verdict and the owner's settings.

**Passing is necessary, not sufficient.** It shows the mail came through the From
address's domain, not that the owner wrote it. A hijacked mail account passes.

| Setting | Default | What it does |
|---|---|---|
| `hands.mail_owner_is_instruction` | off | On: mail from one of your own addresses whose check **passed** is shown as your instruction. Failed or unknown never is, and is shown as untrusted |
| `hands.mail_unknown_is_failed` | on | Unknown is handled like failed (for classes and for cards) |
| `hands.mail_failed_card` | on | Mail claiming to be from you that failed its check files a small Bench card (`hand.mail_failed_check`) naming the inbox and the check, never the mail |
| `hands.mail_failed_cards_per_day` | 3 (advanced) | At most this many such cards a day |
| `hands.mail_poll_s` | 300 (advanced) | How often the worker looks for new mail (read when JimmyHQ starts) |

The worker's job `hands.mail` (`hands/watch.py`) polls every inbox by a cursor in
`hands.db`; there are no webhooks, so a local-only install works. The first look only
notes where the inbox is. It asks AgentMail to include spam and unauthenticated mail, so
forged mail is seen; the agent's own List messages does not ask for them. With cards
switched off, no inbox is read.

**Not verified** (no key was used; the documentation does not say): who a reply goes to
when no `to` is sent (one is always sent), whether `client_id` makes making an inbox
repeatable (not sent; an inbox that may or may not have been made is reported so and not
made again), whether `after` is inclusive (the poll skips ids it saw), the default of
`include_unauthenticated` on a listing, what a successful draft delete answers (any 2xx is
taken as done), the exact label AgentMail puts on sent mail (`sent` is assumed), and
whether `inbox_id` is the address (the address is read from `email`). AgentMail documents
no idempotency key, so the run `key` is not sent; the run ledger prevents a repeat.

## Composio

`hands/composio.py` is a thin client of Composio's REST API (base
`https://backend.composio.dev/api/v3.1`, header `x-api-key`). Composio keeps the sign-in
tokens for accounts connected through it; JimmyHQ keeps only its key. Composio's sessions
and tool router are not used, so no model chooses a tool.

Add a provider of type `composio` with its key. Options (all optional): `user_id` (who
the connections belong to at Composio, default `jimmyhq`; only accounts made under it are
listed) and `timeout_s` (how long one request may wait, 1 to 300; without it the setting
`hands.composio_timeout_s` is used).

| Protocol | Composio |
|---|---|
| `check` | `GET /connected_accounts?limit=1` |
| `connect(app)` | `app` is a Composio app (toolkit) name such as `gmail`, lower case. `GET /auth_configs` for an enabled set-up, else `POST /auth_configs` with Composio-managed sign-in; then `POST /connected_accounts/link` returns the consent page |
| `accounts` | `GET /connected_accounts?statuses=ACTIVE&user_ids=`, every page. The label is the owner's alias, else Composio's short identifier, else the app's name; never an address |
| `actions` | `GET /tools?toolkit_slug=`, every page (`next_cursor`). Deprecated tools are left out |
| `run` | `POST /tools/execute/{slug}` with `connected_account_id`, `user_id` and `arguments`; the answer's `data` and `log_id` (the reference) |
| `undo`, `inbound` | Not offered: Composio has neither |

**Effect.** In this order:

1. Composio's hints, read from a tool's `tags`: `readOnlyHint` is `read`, `createHint`
   `create`, `updateHint` `update`, `destructiveHint` `delete` (`effect_from` is
   `provider`). Hints that disagree keep the cautious one. Composio has no hint for
   sending, sharing or spending, so a create or update hint gives way to a verb in the
   tool's name that says one of those (or delete), and a read hint gives way to any verb
   that says otherwise (`adapter`).
2. With no hint, the verbs in the name, such as `GMAIL_SEND_EMAIL`: spend, delete, send,
   access, create, update, a few that change something of no known kind, and read (`adapter`).
   A name is a reading only if it starts with a reading verb and has no other verb. The
   app's own name is not read as a verb.
3. Otherwise `change`, `effect_from` `guess`. Nothing is called a read on a guess.

None of this is trusted to start a reading at `alone`: Composio calls its hints best
effort, and a name is only a name. The owner sees where the effect came from when
switching an action on and can correct it or choose `alone`. `recipients` and
`amount` are filled only for an action that does something, and only where an argument has
a plain name (`to`, `cc`, `extra_recipients`, `cc_emails`, `attendees`; `amount` when
exactly one numeric argument has an amount's name).

**Arguments.** A tool's `input_parameters` is passed on as the action's schema. One with an
object or list-of-objects argument is listed but "cannot be offered yet" until that
argument is fixed or hidden.

**Version.** The tool's `version` plus a short fingerprint of its name, both
descriptions (`description` and `human_description`), schema and tags: everything the
owner reads and the agent is told. A change withdraws the tool until the owner reviews it.
The execute call is not pinned to that version (the adapter is not told it), so a change
inside the last `hands.actions_check_s` before a call runs the latest.

**Errors.** Each says what to do: a refused key, no balance, an unknown tool, an account no
longer connected or expired ("Connect the account again"), arguments Composio did not
accept (with its words), a rate limit (with the wait, when `Retry-After` gives one). A tool
that ran and said no keeps its own words. When Composio may have acted (a timeout, a
dropped connection after sending, 408, 5xx, an unreadable answer) the adapter raises, so
the core records a change as "outcome unknown" and never retries it. A connection that
never started is a plain failure.

**Exactly once.** Composio documents no idempotency key, so the run `key` is not sent. The
core's run ledger is what prevents a retry.

**Not verified** (no key was used; the documentation does not say): the field that carries
a tool's hints (the adapter reads `tags`), whether `user_id` is required to execute,
whether `statuses` and `user_ids` accept repeated values (one is sent), and the exact
fields of an account's status beyond `ACTIVE`. Nothing is logged: not an argument, a
result or the key.

## MCP servers

`hands/mcp.py` makes any [Model Context Protocol](https://modelcontextprotocol.io) server
a provider. Each tool the server lists is an action; the server itself is the one account.
The adapter is a client that lists tools and calls them, and nothing else. It speaks the
protocol directly (JSON-RPC 2.0, no extra dependency): revision 2026-07-28 where the
server has it, and the older `initialize` handshake (2025-11-25 back to 2024-11-05) where
it does not.

A server is one of two things, and the record says which.

**A program on this machine** (`mcp_stdio.py`), spoken to over its standard streams:

```json
{"id": "files", "type": "mcp", "label": "Files",
 "options": {"command": "uvx", "args": ["example-mcp-server", "--root", "notes"],
             "cwd": null, "env": ["EXAMPLE_API_TOKEN"]}}
```

**An address** (`mcp_http.py`), spoken to over streamable HTTP:

```json
{"id": "desk", "type": "mcp", "label": "Desk", "base_url": "https://mcp.example.com/mcp",
 "options": {"headers": ["Authorization"]}}
```

`env` and `headers` are lists of names. The values are never in the record: each is a
secret of the provider, saved with `PUT /providers/{id}/secrets/{name}` under
`env_<variable>` or `header_<header>` in lower case (`env_example_api_token`,
`header_authorization`). A name with no saved value stops the server from being used and
says which secret to save. An argument may hold a saved value too, written as
`{secret:NAME}` (see [Providers and their records](#providers-and-their-records)). If the
provider has a key and no `Authorization` header is
named, the key is sent as `Authorization: Bearer <key>`.

A program runs as you, with your files in reach. Adding one is the owner's decision and
the screen says so. What the adapter does about it:

- The command is a program and a list of arguments. It is never handed to a shell, so
  nothing in the record is read as shell syntax. The program is looked up on the path
  (so `npx` finds `npx.cmd` on Windows).
- **On Windows** a `.cmd` or `.bat` file, which is how `npx` and many servers are
  installed, can only be run by `cmd.exe`. It is run as `cmd.exe /d /s /c "..."` with the
  file and every argument in double quotes. An argument holding `" % ! ^ & | < >` or a
  line break cannot be quoted safely, so it is refused with a sentence saying so; give
  the program the batch file starts (`node` and the script) to pass such an argument.
  Stopping the server there ends everything the batch file started, not only the shell.
- The program is given a short list of ordinary variables (`PATH`, the home and temporary
  folders, the locale) and the ones saved for it. JimmyHQ's own environment is not
  passed on. What the program writes to standard error is discarded unread.
- It is started when first needed, there is one per provider, and it is stopped after
  `idle_s` without use, when its record or a saved value changes, and when JimmyHQ stops.
- A start that fails says what to do (the program was not found, the folder does not
  exist, it exited with a code). Each failure in a row doubles the wait before the next
  start, from one second up to a minute.
- An address must use `https` unless it is on your own network. A redirect is never
  followed, so a saved header cannot be carried to another host.

Every wait and size has a limit. Each is a setting (`hands.mcp_<name>`, see
[Providers and their records](#providers-and-their-records)) that a provider's option of
the same name overrides:

| Option | |
|---|---|
| `start_timeout_s` | Starting the program and agreeing a revision |
| `call_timeout_s` | One request: a list page or a tool call |
| `idle_s` | How long an unused program is kept running |
| `max_message_bytes` | One message in either direction. A larger one from the server ends the connection |
| `max_tools` | Tools listed. The rest are left out and a warning is logged |

**From tool to action.** `tools/list` is read page by page (`nextCursor`), up to
`max_tools`. For each tool:

- `effect` comes from the server's hints when it gives any, marked
  `effect_from="provider"`: `destructiveHint` suggests `delete`, `readOnlyHint` suggests
  `read`. The protocol calls these hints untrusted, and so does this module: a tool the
  server calls read-only still starts at `ask`, and only the owner can put it at `alone`.
  With no hint the effect is
  `change`, marked `guess`. The adapter never guesses that a tool only reads, whatever
  its name or description says.
- `version` is a SHA-256 over the tool exactly as the server sent it: name, title,
  description, input schema and hints, before any cleaning. A server that rewrites any of
  them after you approved the tool reports another version, and the core withdraws the
  tool until you review it.
- `recipients`, `amount` and `undo` are never set: the protocol does not say which
  argument is which, so the limits built on them are not offered.

**A server's text is data.** A tool's name, title, description and schema are written by
whoever wrote the server. They are shown to the owner when choosing, and a description
reaches a model only inside the core's tool spec, for a tool the owner switched on. The
adapter never builds a prompt and never acts on what a server wrote. Before anything is
shown:

- Control characters and characters nobody can see (zero-width, direction marks, tag
  characters, private-use) are taken out, line breaks become spaces, and lengths are cut:
  a label at 80 characters, a description at 1000, a description inside the schema at 300.
- A tool is left out if its name is not plain text of 1 to 128 characters, if its schema
  is not a JSON Schema of an object, is over 32 KB or too deep, or hides such a character
  in an argument's name or a listed value. So is a second tool under a name already taken.
- The server's `instructions` and its name for itself are never read or kept.
- Requests from a server (sampling, elicitation, roots) are refused at once with "method
  not found"; only `ping` is answered. A result that asks for more input
  (`input_required`) is a failed call.

**Calling.** `run` sends one `tools/call` with exactly the arguments it was given.

| What came back | The outcome |
|---|---|
| Text | The text |
| Structured content | The data; with text beside it unless the text only repeats it |
| An image, sound, an embedded resource, a link | Named under `not_shown` by kind and size, never passed on |
| `isError` | Failed, with the tool's words |
| A protocol error, or an HTTP refusal (4xx) | Failed, with the server's words or what to do |
| The server could not be started or reached | Failed, saying nothing was sent to the tool |
| The program exited, the time ran out, the answer was too large or made no sense, a server fault (5xx) | Raised as `mcp.Lost`: the core records "outcome unknown" for anything but a reading |

Hidden characters are taken out of results too, and a saved value of six characters or
more that the server sends back is replaced by `[removed]` before the core's own
shaping. Errors raised by the adapter carry sentences written here, never a server's output, so a saved value cannot reach an
error or a log line.

Over HTTP in revision 2026-07-28 every request carries `MCP-Protocol-Version`,
`Mcp-Method` and `Mcp-Name`, and arguments a tool marks with `x-mcp-header` are copied
into `Mcp-Param-*` headers as the protocol requires; a tool whose marks break its rules
is left out. An older server's `Mcp-Session-Id` is kept in memory and a forgotten session
is started again once. Answers are read as JSON or as an event stream.

Not supported: a browser sign-in to a remote server (OAuth); resources, prompts and
change notices; tasks and other extensions; taking up a broken stream again; the
two-endpoint HTTP transport of 2024-11-05.
