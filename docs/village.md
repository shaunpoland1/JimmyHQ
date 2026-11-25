# The Village

`server/jimmyhq/village/` is where JimmyHQ's autonomous agents live. Each agent has a
simulated **body**: a few needs that rise and fall with what it does, and that decide
what it does next. The body exists so an agent paces itself, stops pushing at something
that is not working, and ends its day, without a model having to be told to.

| Part | Is | Lives in |
|---|---|---|
| The world | The body's rules: needs, modes, sleep, guards. Pure Python, no I/O | `village/world/` |
| The turn | One step of one agent: read, think, act | `village/beat/`, `village/context/`, `village/catalog.py` |
| Tools and comms | What an agent can do, and the rules each tool checks | `village/tools/`, `village/comms/`, `village/files.py` |
| The night | Sorting a day into lasting memory while the agent sleeps | `village/memory/` |
| The record | Tape kinds, the lens, the owner's API, births and retirements | `kinds.py`, `record.py`, `lens.py`, `routes.py`, `roster.py` |
| The simulator | Scripted agents on a fast clock, with exit tests | `village/sim/` |

Every mechanical rule is one named function, registered with `@rule("name", setting)` in
`village/rules.py`. `RULES` lists them with the setting each one reads.

## The process

`jimmyhq village` is a third supervised process beside web and worker
(`village/process.py`). It is the only one that holds the bodies, so the web process
never touches an agent. The web process prints to the Tape (a chat line, a priority, a
configuration, a Bench decision) or leaves a row in the `commands` table (birth,
retirement, rollback). The village process:

- every second, takes those in by cursor (`intake.py`): chat is mirrored into the
  village log once, a decision is applied, a command is run between turns;
- every minute, moves the world forward (`Village.tick`). A gap of over 15 minutes is
  logged as a pause: the bodies did not live that time;
- per agent, forever: turn, wait, turn. A turn that raises is logged and waited out
  (`village.beat.fault_wait_s`); a loop that ends is started again.

A body is saved to `state.json` after every turn and tick, so a restart resumes where it
stopped. The Tape cursor is saved too, so nothing the owner did meanwhile is lost or
taken in twice.

## The turn

The code and the Tape call a turn a "beat" (`village/beat/`, `village.beat.*` settings); everywhere else it is a turn.

`beat/run.py` goes through the stages in order:

**asleep** (no model call; the night's sorting is checked) → **intake** → **flush**
(held hand-overs and findings) → **pick** (`World.pick_mode`) → **rest** (no model call,
but an unanswered message from the owner gets one turn) → **resume_note** → **feel** →
**context** → **think** → **validate** → **act** → **pace** → **save**.

- **Context** (`context/build.py`) is ordered from what changes least to what changes
  every turn. System: the frame (`docs/frame.md`), then memory. User: an urgent call;
  priorities and tasks; right now (the Bench, nudges); chat; coworkers, break room,
  board, impressions; last actions; working files; the last answer; diary; feelings; time.
- **Think** is one `Brain.chat` call with `tool_choice="required"`, the agent's own turn
  model and its `local_only`. `thought` is the first required argument of every tool.
- **Validate** treats a reply the tools cannot read (cut off, no tool, bad arguments) as
  the model's slip: refused without frustration.
- **Act** runs the world's guards, then the tool, then `World.act`. The turn never
  changes a body itself.
- **Pace** sets the wait: `heartbeat_min_s` after a success, longer when frustrated or
  tired. Owner chat, a coworker's `say` or a decision ends the wait early.

**Refusals.** A tool that will not do what was asked raises `Refusal(rule, sentence)`:
nothing happened. The sentence is for the agent: second person, the number, what to do
instead. A refused action gets `village.beat.retries_after_refusal` more goes in the
same turn, with the refusal as the tool's result. A village limit or a malformed reply
never adds frustration. `Done(ok=False)` is only for an action really tried outside the
village that failed there: the web tools return it for a search or a page that failed,
and an integration tool for a call that did not work or whose outcome is unknown.

## Tools

`tools.ALL` holds the 22 tools every agent has, each offered in some modes (work, explore,
outlet, social), and the tools that need a capability (see below): the two web tools,
`reply_outside`, `withdraw_request` and the nine browser tools. The 22 are:

- **Files** (`list_files`, `read_file`, `write_file`, `add_to_file`, `edit_file`):
  confined to the agent's own folder and the shared folders.
- **Self** (`write_diary`, `remember`, `rest`).
- **Tasks** (`focus_on`, `finish_task`, `set_aside`, `take_on`, `start_own_task`,
  `suggest_priority`): a task is named by its ref (`P1`, `T4`), never by its title. A
  priority is standing and never finishes; a task is a JimmyHQ task assigned to the agent.
- **Work** (`review_work`, `hand_over`, `report_finding`, `correct_finding`): cards on the
  owner's Bench (`village.work`, `village.finding`, `village.priority`, `village.done`).
  The file goes in the Vault and a copy in `deliverables/`. A decision is trusted only
  when the card's `filed_by` matches the row in `cards`.
- **Talk** (`message_owner`, `say`, `post_to_board`, `resolve_post`).

Arguments and results are scrubbed of anything that looks like a secret before they are
logged (`tools/logged.py`); the agent still reads the real result.

## Capabilities: the web

A capability is a name in an agent's configuration (`capabilities: [...]`), set by the
owner (on the Village screen, or by giving an app's role) and by nothing else. A tool that names one is offered only to
an agent that holds it. `village/capabilities.py` describes each in the owner's words and
says what is not set up yet. Every agent starts with none.

An app's role is a capability too, `app.<id>.<role>`: given with the role on the app's
page (`apps.roles`), with the role's charter as a standing priority for that agent alone
(id `p-app.<id>.<role>`). Work an app gives to a role is a task on the agent's list with
the app's details beside the title; finishing it files no "done" card, and setting it
aside tells the app (`apps.work_reported`). An agent made for a role is born with the role
text and model the app suggests, and is given the role as soon as it is born.

**`web`** gives an agent `web_search(thought, query)` and `web_read(thought, url, start)`
in work and explore (`tools/web.py`). Both only read, through `jimmyhq.lookup`, and the
agent uses them by itself. So:

- **Two switches, both the owner's.** The capability on the agent, and `lookup.enabled`
  for JimmyHQ. With either off the tools are not offered and the frame does not mention
  them (`capabilities.usable`).
- **Outside text is data.** A web tool's result is a line the village wrote ("read
  ADDRESS: characters 0 to 6000 of 18400"). That line is what the log, the list of last
  actions and the night see. What the page said is `Done.shown`: it is in the next turn's
  view once, between two lines carrying a mark made for that result, under a sentence
  saying it is untrusted and not to be obeyed, and it is never logged. A page can change
  only what the agent chooses to do next.
- **Only addresses it was given.** One a search of its own returned, one a page it read
  links to (`web/known.json` in the agent's folder), or one the owner wrote in chat with
  it or in a priority they set. `lookup.fetch_any_url` is not honoured for agents. Every
  rule in [Web lookup](lookup.md) applies unchanged underneath.
- **Limits, counted from the log.** Each request that left is a `web_searched` or
  `web_fetched` event, so a restart loses no count: searches and pages a day for each
  agent, and searches a month for the village. A limit is a refusal without frustration.
- **Repeats and long pages.** The same search or page within `repeat_minutes` is answered
  from the copy kept under `web/kept/`, with no new request, and says so. A page is shown
  `result_chars` at a time; the cut is stated, with the `start` that reads on.
- **The wall.** The web tools count toward the world's wall: four fruitless reads in a
  row and `web_read` is refused until a `web_search` finds something.
- **Memory.** A line whose evidence is an action that brought in outside text (a web
  search or page, a page in the browser, an integration's answer, a reply to an outside
  message) is stored as `web:unconfirmed`, whatever else it cites, and is shown to the
  agent as "from outside, not confirmed" (`memory/provenance.py`).
- **Receipts** are `lookup.searched` and `lookup.fetched` with `private=False`, actor
  `agent:<id>`, caller `village.web`.

| Setting | Default | Meaning |
|---|---|---|
| `village.web.searches_per_day` | 30 | Searches one agent may make in a day |
| `village.web.pages_per_day` | 60 | Pages one agent may read in a day |
| `village.web.searches_per_month` | 3000 | Searches all agents may make in a month |
| `village.web.repeat_minutes` | 60 | How long a search or page is kept for a repeat |
| `village.web.result_chars` | 24000 | How much of a page is shown at once |

## Integrations

`tools/hands.py` is the village's only door to `jimmyhq.hands` (see `hands.md`). Each
action the owner switched on for an agent is one tool for that agent, built for the turn
from the owner's rules and offered while working and exploring. The capability `hands` is
derived from those rules: an agent with no rule has no such tool, and there is no switch.

- **It ran.** The logged result is a line JimmyHQ wrote. What came back, an error
  included, is shown once as untrusted text (`Done.shown`). An action that changed
  something without asking leaves a `hand.done` card for the owner.
- **It asks first.** A `village.hand` card shows exactly what will be done: the action,
  the account, every argument in full as it will be sent, who receives it and how many,
  and the request's fingerprint. The agent waits on it like any card (`World.submit`). It
  cannot edit the request; `withdraw_request` takes it back.
- **The answer.** The owner's click is not the answer. The executor performs or closes
  the request and prints `hand.outcome`; `benchlink.apply_outcome` turns that into the
  decision the agent sees in its next turn: done with the provider's reference, not done
  with the reason, "outcome unknown: check before retrying", or the owner's no.

An agent with a rule also gets the "Using integrations" section in its copy of
`how-things-work.md` and a paragraph in its frame.

### The browser

`browser` is another capability a tool is offered under: nine tools (`open_page`,
`click`, `type_text`, `press_key`, `scroll`, `go_back`, `switch_tab`, `close_tab`,
`read_page`) that drive a headless browser of the agent's own. It needs no switch for the
whole of JimmyHQ, only a Chromium-family browser on the machine; without one the tools are
not offered and the Village screen says what to install. The world already knew these
tools by name (`world/tools.py`), so the wall and the repeat guard apply as they are: the
turn hands the world the page as the agent last saw it (`tools.browser.before`). What a
page shows reaches the agent as marked, untrusted text, with a picture of the page when
its model takes pictures. Everything else is in [The browser](browser.md).

## Waiting on the owner

A card that needs the owner (`village.work`, `village.finding`, `village.priority`) is
something the agent waits on. `benchlink.file` calls `World.submit`: one `bench_submitted`
event, a little purpose, and a wait that adds `frustration.wait_per_hour` while the agent
is awake, up to `wait_cap_per_item` for one card and `wait_cap_total` for all of a day's
waiting. `benchlink.apply_decision` calls `World.respond` with the verdict: the wait ends,
and an approval adds purpose once. A `village.done` card informs and is not waited on. A
revision, or a card that replaces another, keeps the wait and adds no purpose. On start
`benchlink.rebuild_waiting` makes each body's waits agree with the `cards` table.

## The night

Once per sleep, `memory/night.py` runs a pass: up to `village.night.tries`,
`retry_gap_min` apart, the first `smart_only_tries` on the night model only.

1. **Gather:** the changes doc, memory with ids, what the agent asked to remember, the
   diary without dreams, the record since the last sorted night.
2. **Ask** for operations (`add`, `replace`, `reinforce`, `drop`) as JSON. An answer that
   is cut off comes back as an error carrying the text so far: every operation that
   arrived whole is kept, and the model is asked once more, shorter, for the rest.
3. **Apply.** Cited event ids are checked against the log; each line's `source` becomes
   `owner`, `seen`, `village`, `web:unconfirmed` or `self-reported`. A wholly rejected
   pass changes nothing and is tried again.
4. **Afterwards:** impressions, fading, a snapshot, `village.slept`, a dream.

A rollback (`POST /api/village/agents/{id}/rollback`) snapshots the present first, so
it can itself be undone.

## What the Village screen reads

Every time the API returns is unix milliseconds. The world keeps seconds internally, so
`rest_until` and `nap_until` are converted at the route edge; Tape and log times were
already milliseconds.

- `GET /api/village/agents` rows carry `mood` (`{valence, arousal}` or null), `sleep_kind`
  and `wake_at`: when a resting or napping agent will be up, or, for a night sleeper, the
  next usual wake time on its own body clock (null once that hour has passed, or for a
  forced sleep, which ends on energy).
- `GET /api/village/agents/{id}/vitals?hours=24` (1 to 168) is the five needs over time from
  `need_change` events, at most 200 points: `[{t, energy, frustration, social, curiosity,
  purpose}]`.
- `GET /api/village/capabilities` lists `{id, label, description, tools, ready, problem,
  settings}` for each capability a tool is offered under. `problem` is the sentence that
  says what is not set up yet (web lookup off, no search provider ready), or null, and
  `settings` the settings section where that is fixed.
- The setting `village.show_vitals` (default on) says whether cards show needs and mood.

## Where things are stored

| Where | Holds |
|---|---|
| `village.db` | The event log (`evt-N`), and the tables `cursors`, `commands` (owned by `roster.py`), `cards`, `task_refs` (owned by `comms/benchlink.py`) |
| `village/agents/<id>/` | `state.json`, `home/files/`, `diary/`, `memory/`, `impressions/`, `deliverables/`, `snapshots/`, `queues.json`, `web/` |
| `village/shared/<id>/` | What an agent shares; coworkers may read it |
| `village/shared/left/<id>/`, `village/retired/<id>/` | A retired agent's work, read-only, and its archived folder |
| The Tape | `village.born`, `retired`, `named`, `configured`, `prioritized`, `said`, `slept`, `restored`, all printed in `record.py` |

The log is **not the Tape**: an agent writes several events a turn, and most matter only
to the body. The world's clock, the log and every view count **seconds**; tables, files
and Tape payloads store **unix milliseconds**. Convert by name (`runtime.from_ms`,
`comms.ms`), never by guessing from the size of a number. Screens read the `village` lens.

## The body

An agent has five needs from 0 to 100. **Energy**, **social** and **purpose** are good
when high; **frustration** and **curiosity** are urges that build. Every change is logged
with a cause and the events that justify it: a task an agent only *says* it finished
earns no purpose. **Mood** is computed from the needs, never set. **Feelings** are words
(`tired`, `stuck`, `useful`) produced only when logged events support them.
**Temperament** is rolled once at birth from a seed and never edited.

`World.pick_mode` answers `work`, `rest`, `outlet`, `social`, `explore` or `sleep`; a mode
holds until its need has recovered by a margin. Night sleep runs by the agent's own
clock, in the owner's time zone; a tired agent with nothing but a standing priority goes
to bed early. Guards (`World.check`) refuse the same action from the same state, and set
a task aside at high frustration with a note of what was tried.

## The simulator, and changing a number

The numbers in `world/defaults.py` came out of days of live rehearsal. Before changing
one, run `uv run python -m jimmyhq.village.sim` (`--seeds 20`, `--set key=value`); it
touches no data and exits non-zero if an exit test fails.

The whole runtime is exercised without a network in `tests/test_village_e2e.py`: a real
Core, a simulated clock and a scripted model. Start there to see a day from the inside.
