# Nodes: lending a second machine's models

A **node** is a second machine of yours that joins your JimmyHQ to lend it something it has
and the first machine lacks, most often a graphics card. The machine JimmyHQ already runs on
is the **hub**. Stage 1, which is what exists today, lends **models**: a model server on the
node shows up on the hub as one more provider you can give a role to.

What does not move: your agents, the Tape, settings, secrets, the Bench and every decision
stay on the hub. The node holds no copy of the hub's data and needs no password of yours. If
you have one computer, nothing on this page applies and nothing changes: no node code runs,
no extra process starts, and the node link does not answer.

## The two-machine setup, step by step

The example is a small always-on hub and a second machine with a graphics card, both on the
same private network. Names and addresses here are placeholders.

### 1. Make sure the node can reach the hub

JimmyHQ listens on `127.0.0.1` unless told otherwise, so first give the hub an address the
node can open. Either way works:

- **Through your mesh VPN's HTTPS address.** If you already reach JimmyHQ from your phone as
  in [remote.md](remote.md), the node can use the same address, such as
  `https://example-machine.example-tailnet.ts.net:8440`. Nothing more to do.
- **Directly on the private network.** In `config.toml` in the hub's data folder
  (`jimmyhq data-dir` prints where), set the address to listen on and restart:

  ```toml
  [server]
  host = "0.0.0.0"
  allowed_hosts = ["192.0.2.10"]
  ```

  `allowed_hosts` is the name or address the node will type; JimmyHQ refuses a request
  addressed to a name it does not know. Listening on every address means anything on that
  network can reach the sign-in page, so do this only on a network you trust.

Check from the node: opening the hub's address in a browser there should show the sign-in
page.

### 2. On the hub: make a join code

Open **Settings, Nodes** and choose **Add a machine**. It shows a one-time code and the
exact command to run on the other machine. The code works once, for ten minutes
(`nodes.join_code_minutes`), and is not shown again. Making a new code ends the one before.

### 3. On the node: install JimmyHQ and join

Install JimmyHQ on the node the same way you did on the hub. Do **not** run `jimmyhq run`
there: a node runs only `jimmyhq node run`. Then:

```text
jimmyhq node join https://example-machine.example-tailnet.ts.net:8440 ABCDE-FGHJK
```

The hub knows the node by its machine name; `--name gpu-box` chooses another (lower-case
letters, digits and `-`, 16 at most). The node is given a long token, kept in its own secret
store (`secrets.json` in its data folder, readable only by you), and remembers the hub's
address in `node.json` beside it.

### 4. On the node: say which model server to lend

Start your model server on the node as you normally would, listening on the node's own
loopback address. It does not need to listen on the network. Then:

```text
jimmyhq node offer model --name big --url http://127.0.0.1:8080/v1
```

`--url` is the server's OpenAI-compatible address (the one that answers `/models` and
`/chat/completions`). `--name` is a short name of your choosing. Offer several by running
the command again with other names; `--remove` takes one back. `jimmyhq node status` lists
what the node offers.

### 5. On the node: run it

```text
jimmyhq node run
```

To start it at login instead, `jimmyhq service install --node` writes a service definition
and prints the one command that switches it on:

| System | The definition it writes | Switched on with |
|---|---|---|
| macOS | a LaunchAgent, `ai.jimmyhq.node.plist` | the `launchctl bootstrap` line it prints |
| Linux | a systemd user unit, `jimmyhq-node.service` | the `systemctl --user enable --now` line it prints |
| Windows | a scheduled task, `JimmyHQNode` | the `schtasks /Create` line it prints |

It needs no administrator rights on any of the three. On Linux, a user service stops when
you log out unless lingering is on for your user (`loginctl enable-linger`), which matters
for a machine with no screen. If you set `JIMMYHQ_DATA` when installing the service, it is
carried into the definition.

### 6. On the hub: give the model a role

Within a few seconds the node shows as **Connected** under Settings, Nodes, and its model
server appears under **Settings, Models** as **big on gpu-box**. Give it a role like any
other provider. It is marked as one of your own machines, so a request that must stay local
may use it.

## What you will see

- **Settings, Nodes** lists each node, whether it is connected, what it offers and which
  roles use it.
- The **status popover** in the status bar lists each node beside the hub's own processes.
- A node that stops reporting in for 30 seconds (`nodes.gone_after_s`) is shown as out of
  reach. Its models are passed over, and a role moves to its next choice, exactly as for any
  provider that is down.
- If the node goes away while an answer is being written, the call fails the way any model
  failure does, with a sentence that says the node went away.
- **Remove** revokes the node's token at once, cuts off anything it was doing, removes its
  providers and takes them out of every role that used them (the dialog says which). The
  node notices, stops and forgets its token. `jimmyhq node leave` does the same from the
  node's side.

## Versions

Keep both machines on the same JimmyHQ version. If they differ, both sides say so: the hub
under Settings, Nodes, and the node in its terminal and its log (`logs/node.log` in its
data folder). A small difference only earns that sentence. If the two speak different
versions of the node link, nothing is sent to the node until they match, and the sentence
says which machine to update.

## How safe is the link

Be exact about what protects what:

- **The join code and the node's token travel over your network as it is.** JimmyHQ does not
  add encryption of its own to the link. Over `https`, or over a private network that
  already encrypts (a mesh VPN), that is fine. Over plain `http` on a network other people
  share, anyone who can watch the traffic could read the token, and with it pretend to be
  your node and see the prompts sent to it.
- **The node refuses the unsafe case.** `jimmyhq node join` and `jimmyhq node run` will not
  talk plain `http` to a hub that is not on this machine or a private address. If you accept
  the risk, `--allow-plain-http` on `join` says so and is remembered for that hub.
- **A node token opens the node link and nothing else.** It cannot read the Tape, use the
  API, reach the MCP door, decide a card or fetch a secret. API tokens, door tokens and your
  own session are refused on the node link in turn. Tokens are stored as digests, compared
  in constant time, never logged, and revoked the moment you remove the node.
- **Guessing is held back.** Five wrong join codes, or five unknown tokens, from one address
  pause that address for fifteen minutes. A joined node is never locked out by someone
  else's guessing. Behind a proxy on the hub that does not pass on the visitor's address,
  every visitor counts as one address.
- **What a node can see.** A node that lends a model sees the prompts sent to that model and
  writes the answers. Join only machines you own.
- **What a node can be made to do.** Only what it offered: the hub can ask the node's
  configured model servers for their model list, a chat completion or embeddings, and
  nothing else. It cannot make the node fetch any other address.

## How it works

A decision worth explaining: the hub **relays** model calls through the node process. It
does not connect to the node's model server itself.

```text
hub: Brain (web, worker or village process)
       |  ordinary OpenAI-compatible call, on the hub's own loopback address
       v
hub: web process  /api/nodes/relay/<node>/<offer>/...      (relay key)
       |  becomes a job
       v
node: jimmyhq node run     <-- asks for jobs over a connection the NODE opened
       |  http://127.0.0.1:PORT/v1/...                      (node token)
       v
node: your model server, on loopback
```

The other way to build it would have the hub call the node's model server directly across
the network. That needs the model server to listen beyond loopback, with no sign-in of its
own, and needs the hub to be told and trust an address for the node. The relay needs
neither: nothing on the node listens, the hub never connects out to a node, and a node
behind a firewall or on a laptop that changes address works unchanged.

- **The node calls the hub.** It reports in every few seconds (`nodes.heartbeat_s`) and
  holds one request open asking for work. After a failure it tries again after 1, 2, 4, …
  seconds, never more than 30 apart, for as long as it runs.
- **Streaming.** The node passes the model server's answer back as it is produced, so a
  streamed reply reaches the caller piece by piece.
- **Back-pressure.** A slow reader on the hub slows the node's upload, which slows the
  node's reading of its model server. Nothing is buffered without limit.
- **Timeouts.** A request a connected node does not take within `nodes.pickup_timeout_s`
  (8 seconds) fails and the role's next model is tried. A model that goes quiet is given the
  usual `models.read_timeout_s`. If the caller gives up, the node is told at its next report
  and stops the model.
- **On the hub, a node's model is an ordinary provider record** (`model.provider.saved`)
  whose address is the hub's own relay, kept in step with what the node offers. If you
  change the hub's port, the records are corrected at the next start.
- **On the Tape:** `node.joined`, `node.offered` (what it offers and its version, printed
  when either changes, not on every report) and `node.left`. They name the node and its
  offers; they never carry an address, a code or a token. "Last seen" is a vital sign and
  lives with the other heartbeats, not on the Tape.

## A browser on a node (not built yet)

The link was made so that a node can lend more than models without the protocol changing.
The agents' browser is being built separately; this is the seam it plugs into.

- **Offering.** A node's report carries `browsers: [names]` beside `models: [names]`. The hub
  records them (`node.offered` has `offers: ["models", "browser"]` and `browsers`), and
  Settings, Nodes already shows them.
- **A job.** The hub asks with `nodes.hub(core).call(node_id, "browser", name, payload)`,
  where `payload` is any JSON object, for example `{"call": {"op": "open", "args": {...}}}`.
  It travels the same way a model job does and is refused the same way when the node is
  gone, removed or on another protocol version. `call` returns the status, the content type
  and the whole body; `submit`, `head` and `chunks` do the same in pieces for a long answer.
- **On the node.** `NodeAgent.handlers` maps a job kind to an async generator that takes
  the job and yields reply frames: one `HEAD` (`{"status", "content_type"}`), any number of
  `DATA`, then `END`, or `ERROR` with one of the known reasons. The browser package adds
  `handlers["browser"]`. Until it does, a node answers such a job with "cannot do that kind
  of work yet".
- **What stays the same.** The token, the gate, heartbeats, versions and removal. A browser
  job needs no new route and no new credential.

## The routes

For the signed-in owner only (an API token is refused):

| Route | Does |
|---|---|
| `GET /api/nodes` | Every node: name, offers, roles using it, connected, last seen, version |
| `POST /api/nodes/join-codes` | A new one-time join code, shown once |
| `DELETE /api/nodes/<id>` | Remove a node and revoke its token |

For a second machine:

| Route | Key | Does |
|---|---|---|
| `POST /api/nodes/join` | the join code | Exchanges the code for a node token |
| `POST /api/nodes/link/heartbeat` | node token | "Still here", version, and what is offered |
| `GET /api/nodes/link/jobs` | node token | Waits for the next job |
| `POST /api/nodes/link/jobs/<id>/reply` | node token | The answer to one job, as frames |
| `POST /api/nodes/link/leave` | node token | The node leaves; its token is revoked |

For the hub's own processes: `/api/nodes/relay/<node id>/<offer>/{models,chat/completions,embeddings}`,
opened by a key that never leaves the hub.

Until a join code is made, the join and the relay answer 404, and the link refuses everyone.

## Not here yet

- **Agents on a node.** Moving the agents' process to the second machine is stage 2.
- **Browsers on a node.** See the seam above.
- **A key for the node's model server.** A model server that wants an API key of its own is
  not supported yet; run it without one on loopback.
- **Through a reverse proxy.** The link has been exercised directly between two machines'
  processes. A proxy in front of the hub that buffers whole requests would hold a streamed
  answer back until it is complete; one that streams (as a mesh VPN's does) is expected to
  work.
