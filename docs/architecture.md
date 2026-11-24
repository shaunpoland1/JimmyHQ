# Architecture

JimmyHQ is a local server you open in a browser. This page describes the foundations
that exist today. It grows as each area is built.

## Processes

| Process | Does |
|---|---|
| Supervisor (`jimmyhq run`) | Starts the others and restarts one if it exits |
| Web (`jimmyhq web`) | Serves the API and the built web app |
| Worker (`jimmyhq worker`) | Runs scheduled jobs, so a slow job never slows the app |
| Village (`jimmyhq village`) | Holds the agents' bodies and runs each agent's loop |

They share one data folder (`jimmyhq data-dir` prints it; `JIMMYHQ_DATA` overrides it).
It is outside the source tree so data cannot be committed by accident.

## Modules

Features are modules: `server/jimmyhq/<name>/` on the server and `web/src/modules/<name>/`
in the web app. A server module brings its own event kinds, settings, routes and jobs,
and is listed in `server/jimmyhq/modules.py`. Each has a short page in `docs/`:
[tasks](tasks.md), [bench](bench.md), [models](models.md), [speech](speech.md), [chat](chat.md), [wall](wall.md), [memory](memory.md), [village](village.md), [lookup](lookup.md), [browser](browser.md), [hands](hands.md) (shown as Integrations: outside
services an agent may use, one action at a time, as the owner allows), [door](door.md)
(the MCP door, shown as Other AI apps: how named outside systems reach a small set of tools,
the other way round),
[remote](remote.md) (reaching it from your other devices over a private network),
[nodes](nodes.md) (a second machine of yours lending its models; nothing runs for it on an
install with one machine),
[apps](apps.md) (the app platform: what other people build on JimmyHQ, installed with the
owner's consent and held to what they agreed to).

## Stores

| File | Holds | Rebuildable |
|---|---|---|
| `tape.db` | The Tape: every decision and action, append-only except for erasure the owner asks for | No. This is the record. |
| `vault/` | Large content, named by its sha256. A file goes only when the last event naming it is erased | No |
| `views.db` | Lens state: the stored views screens read | Yes, by replaying the Tape |
| `chat_offrecord.db` | Chat kept off the record: messages, replies and titles, with a real delete | No. Never on the Tape; deleting is final. |
| `memory/` | The Library: plain Markdown notes a person can read and edit by hand | No. The files are the source of truth for memory. |
| `memory.db` | The memory index and fact details | Yes, from the Library and the Tape |
| `memory_store.db` | Earlier versions of notes, facts waiting for a decision, what was declined, how far each source has been read | No. Has a real delete. |
| `village.db` | The Village's event log, its commands and its cards | No |
| `village/` | Each agent's body, files, diary, memory and snapshots | No |
| `hands.db` | Integrations: the run ledger, requests waiting for a yes with their arguments, each account's reference at its provider | No. Has a real delete. |
| `apps/<id>/versions/` | An installed app's files, exactly as they were consented to | Yes, by installing it again |
| `apps/<id>/data/` | An app's own values and records (`app.db`). Kept when the app is removed unless the owner says otherwise | No. Never on the Tape. Has a real delete. |
| `apps/apps.db` | The apps platform's database: the app inbox (work waiting for an app), the event kinds full apps declared (kept after an app is removed so what it wrote can be read), open mounts, quota counts, found updates, plug bookkeeping, the number each full app last said waits on the owner, device keys and where each app's install came from | No. The inbox, the declared kinds and the device keys cannot be made again; the rest is working state that comes back by itself (quota counts start again). |
| `auth.db` | The owner's password hash, sessions, API tokens, door clients, nodes | No |
| `secrets.json` | Keys for outside services, owner-only | No |
| `metrics.db` | Heartbeats and job runs | Yes |
| `models.db` | Each model provider's last health check and cached model list | Yes |
| `config.toml` | Optional: host and port, the only values needed before the Tape opens | |

### Versions and updates

Each store that cannot be rebuilt carries a version number in its own file. When an
update opens a store written by an earlier release, it brings the file up to date one
step at a time, and each step is finished or not done at all, so a step that fails leaves
the file as the last step left it, with nothing lost. A file from before versions existed
is brought up to date the same way. The stores marked Yes above carry no version.

If a store was written by a newer JimmyHQ than the one starting (after going back to an
older version, say), JimmyHQ does not start and does not touch the file. It names the file
and says to update JimmyHQ, or to put back a backup made with this version.

### Back up and move your data

Everything JimmyHQ keeps is in the data folder (`uv run jimmyhq data-dir` prints where).
JimmyHQ makes backups of it by itself once you choose where they go. `settings export`
covers settings only.

#### Backups

Under Settings, Backups, choose a **backup folder** outside the data folder, ideally on
another drive. Backups are off while it is empty. A **second backup folder** is optional
and gets its own copy each time; the two never depend on each other, so one that fails
(a drive not plugged in, say) does not stop or undo the other. A folder inside the data
folder is refused, and a second folder that is the first again is refused too.

- **When.** Every `backup.every_hours` (24), measured from the last backup in that folder,
  by the worker. After a failure it tries again after `backup.retry_hours` (2). **Back up
  now** on the same page, or `uv run jimmyhq backup now`, makes one at once. One backup
  or test runs at a time (`backup.lock` in the data folder).
- **Where each one goes.** A folder `jimmyhq-backup-<UTC time>Z` (for example
  `jimmyhq-backup-20261122T164800123Z`) in the backup folder, holding `manifest.json` and
  `data/`. It is written into a hidden `.jimmyhq-backup-...partial` folder first, checked,
  put on the disk, and renamed at the end, so a half-made backup is never listed as a
  whole one and older ones are removed only once the new one is safely there. What a
  stopped backup left behind is cleared at the next one; another computer's half-made
  backup in the same folder is left alone unless it has not been touched for a day.
- **Whose backup it is.** Each data folder has a random id of its own, made once, in the
  file `installation_id` (owner-only). A backup names it, so two computers sharing a
  backup folder never prune or test each other's backups, even when their data folders
  are at the same path. The id is in every backup, so a restored or moved data folder
  goes on with the backups it made before. If you restore a copy on a second computer
  and keep the first running too, delete `installation_id` in the copy's data folder
  before you start it: it gets an id of its own, and the two keep their backups apart.
  Backups made before ids existed are still listed, tested and pruned as this data
  folder's own.
- **What is in one.** Every store, the Vault, the Library (`memory/`), the Village's
  folders, each app's files and data, `secrets.json`, `config.toml`. Each database is
  copied with SQLite's own backup API, so a backup made while JimmyHQ runs is a whole
  picture of each database even with writes in its `-wal` file, and never a copy of the
  bytes caught half-way. Other files are copied as they are, with their permissions where
  the system has them. Every other database is copied before the Tape, and the Vault and
  the other files after it, so what a store says it has read of the Tape is always in the
  copied Tape, and every Vault file an event names is there. The manifest lists each
  file's path, size and sha256, each store's schema version, the JimmyHQ version and when
  it was made (unix ms, UTC).
- **What is left out**, and why: `logs/` (this computer's runs), JimmyHQ's own lock files
  at the top of the data folder (the processes running now; an app's `uv.lock` goes in),
  SQLite's `-wal` and `-shm` files (the copies already hold them),
  a full app's Python environment `apps/<id>/venvs/` (built for this computer; updating
  or installing the app builds it again), an unfinished install `apps/_staging/`, an
  agent's browser profile (its cookies are locked to this computer; the agent signs in
  again), links (a backup never follows one out of the data folder), and the files an
  operating system adds to folders by itself (`.DS_Store`, `._*`, `Thumbs.db`,
  `desktop.ini`), which are never copied back on a restore either. The stores marked
  "Yes" under Rebuildable are left out unless `backup.include_rebuildable` is on: they are
  made again from the rest, which makes the first start after a restore slower.
- **How many are kept.** `backup.keep` (7) in each folder. Older ones are removed only
  after a new one was made and passed its check, so a failed backup removes nothing and
  the newest good one is never removed. Only backups of this installation are removed:
  another's in the same folder are left alone. Newest means by when each backup says it
  was made; one dated later than the computer's clock (made while the clock ran ahead)
  counts as the oldest, so it is not the one tested and is the first removed once newer
  ones are there.
- **The restore test.** Each backup gets a quick check as it is made: every file there at
  its size, nothing extra, and each database passing SQLite's `quick_check`, opened
  read-only. Every `backup.test_every_days` (7), and when you press **Test the newest
  backup**, the newest backup in each folder gets the full test: every file's sha256
  against the manifest, then each database copied to a temporary folder and opened, with
  SQLite's `integrity_check` and a schema version this JimmyHQ knows. It never touches the
  data folder. A `.DS_Store` or `Thumbs.db` that a file manager left in a backup does not
  fail it. `uv run jimmyhq backup verify <backup>` runs the same test.
- **What you see.** The Backups page shows each folder's last backup, last failure and
  last test. While backups are on, if a folder's newest good backup is older than
  `backup.warn_after_days` (3), the newest one failed its test, the last backup or test is
  dated later than the clock says it is now (the next one is then made at once), or a
  backup or test has been running for longer than `backup.stuck_after_hours` (12), one
  card on the Bench says so. It goes away by itself once backups work again. A run that
  is stuck (on a drive that stopped answering, say) cannot be stopped from inside
  JimmyHQ; restarting JimmyHQ stops it, and nothing is lost. Each backup, failure and test
  is on the Tape (`backup.made`, `backup.failed`, `backup.test_passed`,
  `backup.test_failed`), naming the folder only as first or second and by a short
  fingerprint, never by its path.

A backup holds what signs you in (`auth.db`) and the keys for outside services
(`secrets.json`). Put backups somewhere only you can read: an owner-only folder, an
encrypted drive, a synced folder that is yours alone. JimmyHQ makes each backup folder
owner-only where the drive allows it; a drive without permissions (most USB sticks,
many network shares) cannot.

#### Restoring

Restoring replaces everything JimmyHQ keeps, so it is done at the computer itself, from
a terminal, and never from the web app.

```bash
uv run jimmyhq backup list                      # the backups in your backup folders
uv run jimmyhq backup verify <backup folder>    # the restore test, on one backup
uv run jimmyhq backup restore <backup folder>
```

`restore` refuses while JimmyHQ is running: stop it first (Ctrl-C where `jimmyhq run`
runs; if it starts at login, `uv run jimmyhq service uninstall` prints the command that
switches it off). It runs the full restore test and refuses a backup that fails it, or
one from a newer JimmyHQ, with the same words JimmyHQ uses at start-up. It says what it
will do and asks you to type `restore` (or pass `--yes`). Then it copies the backup next
to the data folder, checks the copy, **moves the current data folder aside** to a sibling
named `<data folder>-before-restore-<time>`, and puts the copy in its place. Nothing is
deleted: the old data stays in that sibling until you remove it yourself, and the backup
is left as it was. A backup from an older JimmyHQ is brought up to date when JimmyHQ next
starts, as after any update.

While it swaps the folders it holds JimmyHQ's lock on both, so JimmyHQ cannot start on
either half-way (on Windows, which cannot rename a folder with a file in it open, the
lock is let go just before the two renames, which follow each other at once). If a data
folder appears in between anyway, or the copy cannot be put in place, it stops and says
so: either the data folder was put back as it was, or, if even that failed, where your
data is now (the moved-aside folder) and where the checked copy is, and which one to
rename to get back or to finish. Do not start JimmyHQ until one of them is back in
place. When the data folder is a link to another folder, the folder it leads to is
restored and the link is kept.

#### Moving to another computer

Install JimmyHQ there, stop it, and restore a backup with `jimmyhq backup restore`, or put
a copy of the data folder where `jimmyhq data-dir` prints on that computer (or anywhere
else, with `JIMMYHQ_DATA` set to that place).

A copy of the folder made by hand is a backup too, but only while JimmyHQ is stopped. Each
`.db` file is a SQLite database in write-ahead mode, so while it is open its newest writes
are in the `-wal` file beside it, and several processes are writing. A copy taken file by
file can catch a database and its log at different moments, and a `.db` copied without
its `-wal` and `-shm` files is missing what was written last. The stores also belong
together: an event on the Tape names a file in the Vault, and the stored views are built
from the Tape. JimmyHQ's own backups avoid all of this. The notes in `memory/` are plain
files and can be copied by themselves at any time.

Things to know:

- A backup made before you erased something still holds it (see [Erasure](#erasure)).
  Pruning is how erased material leaves your backups: once every backup made before the
  erase has been removed (after `backup.keep` newer ones), none holds it. To be rid of it
  sooner, delete those backup folders yourself.
- Settings that describe one computer (the address of a speech server or a local model,
  allowed hosts, the path to a browser or to `uv`, the backup folders) come along in a
  backup. Check them after a move.
- A full app's own Python environment is not in a backup. After a restore or a move,
  update or install the app again so it is built there.
- A service that starts at login belongs to the computer: run
  `uv run jimmyhq service install` again on the new one.

## The Tape

One table. Each event has a kind, a version, an actor, a payload, and three lineage
columns: `cause` (the event that led to it), `root` (shared by a whole chain of work) and
`subject` (the thing it is about).

- **Kinds are typed.** A kind is a pydantic model registered with `@kind(...)` and an
  example. A payload that does not validate is refused.
- **Old events are upgraded on read** by upcasters. Stored bytes are never rewritten.
- **Kinds are namespaced.** An app's kinds start with `app.<id>.`.
- **`Tape.print` is the only write path.**
- **Nothing is rewritten, with one exception: erasure the owner asks for.** See below.

### Erasure

The Tape is append-only except for `Tape.erase(event_ids, actor=, reason=)`. It exists so
that "delete for good" can be true for things that were put on the record, such as a
[conversation](chat.md#deleting).

What it does, in one transaction:

- Each event named keeps its row: `seq`, `id`, `ts`, `kind`, `v`, `actor`, `cause`, `root`
  and `subject` are untouched, so order, counts and lineage stay true. Its payload becomes
  an empty tombstone (`{}`) and the row is marked `erased`. It reads back with `erased`
  true and `readable` false, so every reader that steps over an unreadable event steps
  over it, and every lens folds it as nothing.
- One `tape.erased` event is printed: `reason` (the event kind that explains it, such as
  `chat.conversation.deleted`), how many events, and the count by kind. Never what they
  said.

Who may: the actor must be `owner` or `system` (a job carrying out a setting the owner
chose). A token, an agent or an app is refused (`TapeRefused`), and no HTTP route reaches
it except through a module's own owner-only route. `expect_subject_seq` makes it refuse if
the subject has changed since the caller looked. A `tape.erased` event cannot be erased.
Erasing something already erased, or an unknown id, does nothing and prints nothing.

What it guarantees about the bytes:

- The Tape's connections run with SQLite's `secure_delete`, so the old payload is
  overwritten with zeros in `tape.db`, including the overflow pages of a long one. Appends
  free nothing, so this costs them nothing.
- Straight afterwards the write-ahead log is folded into the database and emptied
  (`wal_checkpoint(TRUNCATE)`), so no earlier image of a page stays in `tape.db-wal`. If a
  reader in another process holds the log for more than about two seconds, or the process
  stops first, the text may still be in the log. That debt is written to `tape.db` itself
  in the erase's own transaction (table `tape_pending`), so it survives a restart and any
  process can see it: `Erasure.settled` is false, `Tape.pending()` says `settle: true`, and
  the next erase, `Tape.settle()` or the worker's hourly `Tape.finish()` pays it. Callers
  pass that on honestly instead of reporting the bytes gone.
- Tests open the files and search the bytes.
- No `VACUUM`: it would rewrite the whole file to save nothing. On a Tape of 300,000
  events (600 MB) erasing a 100-event conversation takes about 2 ms.

What it does not reach:

- **A copy made earlier**: a backup of the data folder, a snapshot, a synced copy. Those
  still hold the text. JimmyHQ's own backups lose it as the ones made before the erase
  are pruned (see [Backups](#backups)).
- **The disk beneath the file.** The file's bytes are zeroed; a file system that keeps old
  blocks (copy-on-write, snapshots, SSD wear levelling) may still have them.
- **Other stores.** A module that copied text elsewhere removes it itself.

Vault files. A file there is named by the hash of its content, and several events may
name one file. `erase` queues, in its own transaction, the references the erased payloads
held that no other event names (`Erasure.orphans`, and `Tape.pending()["vault"]`).
`Tape.release_orphans(vault)` removes them, and has to be safe against a writer in another
process that stores the same text at that moment. Storing a file (`Vault.put`) and
printing the event that names it are two steps with no lock around them, so:

- the removal runs inside one write transaction on the Tape, which no `print` in any
  process can interleave with, and first checks again that no event names the file. One
  that is named again is simply taken off the queue;
- `Vault.put` touches a file it finds already there, and a file stored or put again within
  the last 60 seconds (`ORPHAN_QUIET_MS`) is left for the next try, so a writer between
  its `put` and its `print` is not left pointing at nothing;
- the look and the delete are one step (`Vault.discard` moves the file aside, reads its
  time there, and puts it back if it was just used).

This rests on one assumption: a writer prints its event within a minute of storing the
file. Every writer in the code does so in the next statement. The cost is that a long
message stored less than a minute before its conversation is deleted stays in the Vault
until the worker's next hourly run, and the delete says so (`settled: false`).

TypeScript types for payloads are generated into `web/src/events.gen.ts`, and a test fails
if they drift.

## Lenses

A lens is a pure function `fold(state, event) -> state` with a version. The runner stores
its state and catches it up as events arrive. Bumping the version replays from the first
event. Screens read lenses; they do not scan the Tape on each request.

After an erasure a lens must not keep what was erased. When a lens meets a `tape.erased`
event that names a kind it folds, its state is thrown away and replayed from the first
event, where the erased events now fold as nothing. Every process does this for itself at
its next read, and lenses that fold none of those kinds are left alone. A replay reads
every event of the lens's kinds: about 3 seconds for 300,000 chat events. A lens that
knows exactly what an erasure took from its state can implement `Lens.erased` to take it
out directly and skip the replay; the chat list does, so deleting a conversation costs
about 60 ms on that Tape. Either way the old state row in `views.db` is overwritten
(`secure_delete`) and its log emptied.

## Settings

A setting is declared once with its type, default, range, label and help text. Values are
events on the Tape, read through a lens. The settings screen is generated from the
declarations, so a new setting needs no UI work.

### Settings as files

Every setting can be read and changed from a JSON file and from the command line as well
as from the Settings screen, and a file of preferred settings (a profile) can be shared.

```json
{
  "jimmyhq_settings": 1,
  "name": "Quiet",
  "description": "Optional. What this set of settings is for.",
  "settings": { "village.show_vitals": false, "tasks.stalled_after": 5 }
}
```

- `jimmyhq_settings` is the format version. A file from a newer format is refused with a
  sentence saying to update. `name` and `description` are optional and make it a profile.
- Keys are the declared setting keys, and values are checked exactly as the screen checks
  them. A `null` value means "back to the default". An unknown key (often one from a module or app
  that is not installed), a wrong type or a value out of range is refused with a sentence
  that says what to do. A file is applied all or nothing, unless you choose to skip the
  entries that are refused.
- Each applied change is an ordinary `setting.updated` event, so the Tape shows who
  changed what. Applying is not one database transaction: the file is checked first, then
  each change is written, so a crash part way leaves earlier changes in place (and on the Tape).
- A setting is `portable` unless its declaration says `portable=False`. Personal or
  machine-specific settings (your name, time zone, allowed hosts, the speech server and local
  model, where memory notes are kept, the price table's currency) are left out of an export
  as a profile and kept in an export of everything, which is a personal backup.
- Secrets never appear in a file. API keys and tokens live in the secret store, which the
  settings code does not read, and an entry whose key looks like a credential is refused
  without its value being shown back.

**Screen.** Settings has a Settings file section: Export (as a profile or everything),
Import (pick a file or paste it, preview every change, then Apply) and an Undo on the
confirmation.

**API.** `GET /api/settings/export?portable=&changed=` downloads a file;
`POST /api/settings/plan` returns what a document would do and changes nothing;
`POST /api/settings/import[?skip_invalid=true]` applies it (owner only). Bodies are
limited to 256 KB.

**Command line.** These work whether or not JimmyHQ is running; they write to the same
Tape as the owner, and a running server sees the change on its next read.

```bash
jimmyhq settings list [--changed]
jimmyhq settings get <key>
jimmyhq settings set <key> <value>        # value is JSON; plain text is fine for text
jimmyhq settings reset <key>
jimmyhq settings export [--profile] [--changed] [--name N] [--description D] [-o file]
jimmyhq settings import <file> [--dry-run] [--skip-invalid]
```

Exit status is 0 on success, 1 when something is refused and 2 when the file cannot be
read. Example profiles are in `profiles/`.

## Access

- The server listens on the local machine only unless configured otherwise.
- One owner per install. The password is stored as an scrypt hash.
- A browser session is an HttpOnly cookie; only its digest is stored.
- API tokens carry scopes (`read`, `write`) and can be revoked. The owner makes one with a
  label and a level, reader (`read`) or writer (`read` and `write`), under Settings, Access
  or with `uv run jimmyhq owner token create <label> [--level reader|writer]`; `token list`
  and `token revoke <id>` do the rest. The routes are `GET /api/auth/tokens`,
  `POST /api/auth/tokens` (`{"label", "level"}`) and `POST /api/auth/tokens/{id}/revoke`,
  for the signed-in owner only, never a token. The token itself is in the answer to the
  create call and nowhere else: only its digest is kept, and the `token.created` event
  holds the id, the label and the scopes. A revoked token stops working on its next
  request, an open live feed included.
- Lowering "Stay signed in for" (`access.session_days`) shortens sessions already given
  out: on its next request a session is cut to end no later than that many days from then.
- Whether a request comes from this computer is decided in one place
  (`web/locality.py`). A proxy or tunnel on this computer, such as Tailscale Serve,
  connects from the loopback address too, so a loopback request that carries a forwarding
  header (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`) or is addressed to a name other than
  the machine's own is not local. Such a request is not answered for `localhost`,
  `127.0.0.1`, `::1` or the bound address, even if one is listed, only for another name in
  the allowed hosts (the setting refuses this computer's own names); it is counted as
  `forwarded:<the last X-Forwarded-For entry>`, the one the nearest relay added, for the
  sign-in pause and wrong node join codes (remote, in its own bucket); an app's token is not taken from it; and the session cookie is `Secure` when
  it says `X-Forwarded-Proto: https`. A request Tailscale marks as from the public
  internet (`Tailscale-Funnel-Request`) is refused unless `allow_public = true` is in
  `config.toml`. The device route (`/api/in/`) and the MCP door count wrong keys and
  tokens under the same key, so knocking through a tunnel never turns away a caller on
  the machine itself; a right key or token always passes.
- A door client's token opens the MCP door only, and a node's token the node link only
  (`/api/nodes/link`). Neither is accepted as an API token, and an API token is accepted
  on neither.
- Changes sent from another site's page are refused.
- Five wrong passwords in fifteen minutes pause sign-in.
- The owner changes the password under Settings, Access (`POST /api/auth/password`, with
  the current password and the new one). A wrong current password changes nothing and
  counts as a wrong sign-in. A change signs out every other browser; the one that asked
  stays signed in, but on a new session: in the same step as the new hash is stored its
  old session is ended and the reply sets a fresh cookie, as sign-in does. A request the
  web app had already sent with the old cookie is answered 401; the app then asks
  `/api/auth/state`, and if it is still signed in sends that request once more.
- "Sign out everywhere else", in the same place (`POST /api/auth/sessions/end-others`),
  ends every session but the one that asks and says how many it ended. A session is a row
  in `auth.db` that is looked up on every request, so an ended one stops working at once.
- Both routes take the signed-in owner only, never an API token. Each prints one event
  (`owner.password_changed`, `owner.sessions_ended`) that holds a count and nothing else.

### A forgotten password

There is no recovery by email or from the sign-in page. On the computer JimmyHQ runs on,
in a terminal:

```bash
uv run jimmyhq owner set-password
```

It asks for the new password twice without showing it, and never takes it from an
argument, a pipe or a file. It signs out every browser, clears a sign-in pause, and
leaves API tokens, door clients and joined machines as they are. It works whether or not
JimmyHQ is running, and a running JimmyHQ does not need a restart. Whoever can run it can
already read every file JimmyHQ keeps, so it asks for no proof beyond being there. Do not
delete `auth.db` to get back in: that also removes the tokens, clients and machines.

## Vital signs

Heartbeats and job runs go to `metrics.db`, not the Tape. The Tape records work, and
"still alive" ticks would bury it.
