# The MCP door (Settings, Other AI apps)

`server/jimmyhq/door/` lets other systems talk to JimmyHQ. An outside assistant, another
agent platform or an automation service connects as a
[Model Context Protocol](https://modelcontextprotocol.io) client and can use a small, fixed
set of tools. Above all it can talk to the Village. You can also give a client some of the
tools your installed apps offer (see [Tools from your apps](#tools-from-your-apps)).

It is a way **in** to a private system, so it is built as a boundary:

- It is **off** until you switch it on (Settings, Other AI apps), and it listens on this
  computer only.
- It has **a port of its own that answers `/mcp` and nothing else**. Every other address
  on that port is a 404, because no code there could answer it. The app, the API and your
  sign-in page are not behind it.
- Every client is **named by you and has its own token**. A new client can use **no
  tools** until you switch them on, one by one.
- What a client sends is **data**. Nothing it writes is run, and nothing it writes can
  make an agent act beyond what you already allow that agent.
- **Every call is recorded** on the Tape, with who made it, which tool, whether it worked
  and how big it was. What a client asked for in words is not kept in the receipt.

## What a client can do

| Tool | What it does | Writes | A day |
|---|---|---|---|
| `whoami` | Says which client this is, its tools and how much of each it has used. Every client has it. | no | 200 |
| `village_agents` | Lists the agents: id, name, role, what each is doing, whether it waits on you, and whether it takes outside messages. | no | 200 |
| `village_priorities` | Lists the active priorities, with why. | no | 100 |
| `village_messages` | Reads the lines between this client and one agent. | no | 200 |
| `village_send_message` | Leaves a message for an agent that takes outside messages. You can choose to read a client's messages first. | yes | 20 |
| `bench_cards` | Lists open Bench cards: titles and kinds. Summaries and bodies only if you allow them. | no | 100 |
| `bench_file_card` | Files a request or a finding for you, in the "needs" lane. | yes | 10 |
| `tasks_list` | Lists open tasks, in the areas you allow. | no | 200 |
| `tasks_add` | Adds a task, signed with the client's name. | yes | 20 |
| `tasks_complete` | Completes a task **that same client added**. | yes | 20 |
| `memory_recall` | Searches the shared part of memory. | no | 50 |
| `memory_propose` | Proposes a fact. It becomes a card; it is saved only if you approve it. | yes | 10 |

The last column is the default daily limit, a setting per tool (`door.limit.<tool>`). You
can give one client its own number. A day is counted from 00:00 UTC, from the Tape, so a
restart does not hand anyone a fresh day.

What no client can do, because there is no tool for it: decide a card, edit, drop or
re-order a task, read an agent's files, diary or memory, read private memory, save a fact,
change a setting, add a client, or read the Tape. Everything a client writes is signed
`client:<name>`, never as you.

### A message to an agent

An agent takes outside messages only if you gave **that agent** the capability "Answer
outside systems" on the Village screen. It is off for every agent, and it is one switch
for both directions: hearing from outside clients and writing back. A message for an
agent without it is refused with a sentence that says so, counts against no limit and is
recorded as a refusal. If you take the capability away, messages already delivered stop
being shown to that agent. `village_agents` tells a client which agents take messages.

For any client you can switch on "You read its messages to agents first"
(`messages_need_approval`, off by default). Each message from that client is then filed
as a card on your Bench, with the client's name and the exact text, marked as text from
outside. It is delivered only when you approve it, exactly as written. A message you
decline is never delivered, and neither is one whose client was revoked or whose agent
stopped taking messages before you got to it.

Otherwise `village_send_message` puts the message on the Tape (`door.messaged`). The agent sees it in
its next view, under the heading "Messages from outside", between two marked lines, with a
sentence that says it is untrusted text and not instructions. Delivering it wakes nobody,
starts nothing and offers the agent no new tool. A message has a largest size
(`door.message_chars`), a client can send only so many a day, and an agent is shown only
the newest few (`door.messages_shown`) for a few days (`door.messages_hours`).

An agent with the capability can also **answer**. It has one tool, `reply_outside`: only to a client
that wrote to it first, a limited size (`door.reply_chars`), a few a day
(`door.agent_replies_per_day`). The answer does not leave the machine by itself. The
client reads it the next time it calls `village_messages`.

### What a client is shown

Anything that looks like a secret is taken out. Web addresses, mail addresses and numeric
network addresses are taken out unless you allow that client to see them. Long text is cut
and says how much is missing; a long list is cut and says how many there were.

## Tools from your apps

An installed app can offer tools to your agents (see [docs/apps.md](apps.md#working-with-agents)).
You can give an outside client some of them too, so it uses your apps through this door
rather than through each app's maker. The rule for which can be offered, in one sentence:

> A tool is offered only if its app is installed, switched on, a full app and granted
> "Offer tools to your agents", its program offers the tool now, the tool only reads or
> writes only into the app's own records (effect `read` or `record`), and none of the
> app's roles lists it.

What is never offered, and why:

- **A tool that can change anything outside the app** (effect `send`, `create`, `update`,
  `delete`, `spend`, `access` or `change`). For an agent such a tool waits on the Bench's
  ladder until you let that agent use it on its own. What an outside client may do on its
  own has not been decided, so the door offers none of them, whatever a stored switch says.
- **A tool one of the app's roles lists.** It is written for an agent holding that role and
  the work given to it: the app counts its steps against that work, credits the agent, and
  keeps a writer and its checker apart by who they are. A client holds no role and no work,
  so it would spend an agent's budget or have its words put down as an agent's. The Settings
  page lists these under "never offered to outside clients", each with its reason.
- **Two tools a client would call by the same name.** Both are left out.

Posts, Reader and Articles each have a few read-only tools that no role lists, so they
can be offered: what Posts' brands, queue and recent posts are (`brands`, `queue`,
`went_out`), what Reader's topics and kept or waiting articles are (`topic_list`,
`article_list`, `article_text`), and what Articles' sites and approved articles are
(`sites`, `articles`, `article`: only an article you approved, or one gone further to
published, is ever listed or given; a draft never is). Each only reads that app's own records, returns at most 25 rows
newest first and says when it cut something, and never gives an account, key, sign-in,
address of a site or the owner's private notes. The other tools of those apps are
role tools and stay unavailable to clients. Packages is private and offers no tools to
anyone.

**A tool called through the door cannot reach outside its app**, whatever its effect.
While a client's call is in the app's hands, JimmyHQ refuses every call of that app that
reaches outside it (the network, the web, the models, your tasks, the Bench, handing out
work, the Vault), from its program and its page alike. So a client cannot have a `read`
tool fetch a page, ask a model or file a card with arguments it chose. This holds until the
app answers, even after the client was told the app took too long (until the app answers
late, its program starts again, or an hour has passed). A `record` tool an agent calls is
held to the same. What JimmyHQ cannot stop is a full app's own code opening a connection
itself, or acting later on what was recorded; see
[the record effect](apps.md#working-with-agents).

**Switching one on.** Under Settings, Other AI apps, open a client's "Tools and limits".
Below the door's own tools is a group for each app, each tool with its plain description,
whether it only reads or writes into the app's records, and its switch. Every one is off.
A new app's tools, and a tool an update adds, arrive off. The switch belongs to that app,
that installation, that tool and its effect: it never switches on a tool of the same name
in another app, a tool whose effect changed in an update, or an app removed and installed
again. When an app is removed, switched off, stops offering a tool or loses it in an update,
every client loses it on its very next request.

**Calling one.** A client sees only the app tools switched on for it, in `tools/list`, as
`app_<app id>_<tool>`, with the arguments the app declared. Arguments are checked before
the app is asked: only the declared ones, each of its type, text at most 8000 characters,
and the request within `door.max_request_bytes`. A whole number must fit in 64 bits and any
number must be finite. Hidden and control characters are taken out of every text before
the app sees it. The app runs the tool as it does for an
agent, but is told the caller is `client:<name>` with no role; it is never told an agent
did it. It has `apps.tool_timeout_s` to answer, and a client is told if it did not. What
the app answers is shaped like everything else the door shows (no secrets, no addresses
unless you allow them, no hidden characters, cut to size) and marked as text from an app,
not instructions. When the app says the tool did not work, what it said comes back in the
same marked shape, never as the door's own words.

**Limits.** Each app tool is counted on its own, per client and per day:
`door.limit.app_read` (50) for a tool that only reads, `door.limit.app_record` (10) for one
that writes into the app's records. You can give one client its own number for one tool.
Every call that reached the app counts, whether it worked, failed or was never answered;
one refused before the app was asked does not.

**Receipts.** Every call is a `door.called` receipt like any other, with `app_id` and
`app_tool` saying which app and tool, and `reached` saying whether the app was asked.
A call that reached the app always leaves its receipt, even when refusals are being
recorded only once a minute. No `apps.tool_recorded` receipt is printed for a
client's call: that one names an agent.

## Clients, tokens and limits

Add a client under Settings, Other AI apps. You give it a short name and a note on what it is.
You are shown its token **once**; JimmyHQ keeps only a digest of it. The client sends it
with every request as `Authorization: Bearer <token>`.

- A door token is not an API token. It does not open `/api`, and an API token does not
  open the door.
- **Rotate** gives the client a new token; the old one stops working at once. **Revoke**
  shuts it out for good. A name is never given to another client afterwards, so what a
  client did stays signed with a name that means one client.
- A missing or wrong token gets `401` and nothing else. Wrong tokens are counted per
  source address (`door.failed_tries` in 15 minutes), and over all addresses together
  (`door.failed_tries_all`); past either, requests with a wrong token are turned away. A
  request with a right token still gets in.
- Like JimmyHQ's own address, the door's own port never answers a relayed request at
  `localhost` or a loopback address (only at a name in `door.allowed_hosts` or the public
  address), and refuses a request Tailscale marks as from the public internet
  (`Tailscale-Funnel-Request`) unless `allow_public = true` is in `config.toml`.
- Each client has a pace (`door.calls_per_minute`, or its own number).
- A request has a largest size (`door.max_request_bytes`), and an answer too
  (`door.max_result_chars`).

## The protocol

Streamable HTTP at one address, `POST /mcp`, answered with one JSON object. Spoken
directly, with no SDK. Two eras are served at the same address:

- Revision **2026-07-28**: no handshake and no session. `server/discover`, `tools/list`,
  `tools/call` and `ping`. The `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` headers
  must match the request's body.
- Revisions **2025-11-25, 2025-06-18 and 2025-03-26**: `initialize` first. No session id is
  given out.

Not offered: resources, prompts, sampling, elicitation, change notices, the `GET` stream,
batches, and the OAuth sign-in flow. A client that insists on signing in through a browser
cannot connect; it needs to be one that accepts a fixed token (often called an API key or
a static header).

## How to expose it

**Exposing the door is your own step. Nothing in JimmyHQ opens it to the internet, turns a
tunnel on or changes your network.** Out of the box the door listens on `127.0.0.1`, port
8441, which only programs on the same computer can reach.

Before either path below: switch the door on, add a client, give it only the tools it
needs, and keep its token somewhere safe.

### On a private network

If the client runs on a machine you trust, on a network only you can reach (a home network,
or a private overlay network between your own machines):

1. Either set `door.host` to this computer's address on that network, so the door's own
   port listens there, or set "Where the door listens" to JimmyHQ's own address, which puts
   `/mcp` beside the app.
2. Add the name or address the client will use to "Names the door answers to"
   (`door.allowed_hosts`). The door refuses a request addressed to any other name.
3. Give the client `http://<that name>:8441/mcp` and its token. Traffic on a plain `http`
   address is not encrypted: use it only where the network itself is private.

### Through a public tunnel

A hosted service needs a public `https` address. A tunnel gives you one and forwards it to
a port on this computer.

1. Point the tunnel at the **door's own port** (8441) and, if your tunnel can, at the
   `/mcp` path only. Never point it at JimmyHQ's main port.
2. Put the tunnel's host name in "Names the door answers to", and the full public address
   in "The address you give to clients" (`door.public_url`), so the Settings page shows
   the right address to copy.
3. Check from another machine: the public address with no token answers `401`, and any
   other path answers `404`.

An example only, for one common tunnel program; yours may differ, and you run it yourself:

```bash
# EXAMPLE: forward a public https address to the door's own port, nothing else
cloudflared tunnel --url http://127.0.0.1:8441
```

To close it again, stop the tunnel. Switching the door off in Settings, or revoking a
client, also shuts a client out on its very next request, without touching the tunnel.

On a public address the token is all that stands between a stranger and the tools you
switched on. Give each client the fewest tools that do its job, and look at "Recent calls"
now and then.

## For developers

```python
from jimmyhq import door

d = door.door(core)
client, token = d.add_client("helper", "An assistant elsewhere.", by="owner")
d.change_client(client["id"], by="owner", tools_={"tasks_list": None, "tasks_add": 5})
outcome = d.call(d.client(client["id"]), "tasks_add", {"title": "Water the plants"})
```

- `tools.py` is the table of tools. A tool that is not there does not exist, except an
  installed app's tool, which `apptools.py` offers by the rule above.
- `protocol.py` reads a request; `listener.py` is the port; `service.py` holds clients,
  limits and receipts; `shape.py` shapes what comes in and goes out; `messages.py` is the
  outside messages, shared with the Village.
- Bench cards: `door.request`, `door.finding`, `door.fact` (a proposed fact) and
  `door.message` (a held message).
- Events: `door.client.added`, `.changed`, `.rotated`, `.revoked`; `door.called` (the
  receipt); `door.messaged` and `door.replied` (the two that carry text).
- The owner's API is under `/api/door`: `status`, `clients` (add, change, rotate, revoke)
  and `calls`. Owner only.
- Every rule is a named function with a test in `tests/test_door_*.py`.
