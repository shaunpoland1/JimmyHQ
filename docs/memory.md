# Memory

`server/jimmyhq/memory/` is what JimmyHQ knows about its owner and their world. There is
one Library, one index over it and one way to recall from it.

| Layer | Is | Lives in |
|---|---|---|
| The Tape | The raw record of what happened | `tape.db` |
| The Library | Plain Markdown notes a person can read, edit and grep. **The files are the truth.** | `<data folder>/memory/` |
| The Index | Passages, keyword search, vectors, and what is known about each fact | `<data folder>/memory.db` |
| The Store | Earlier versions of notes, proposed facts, what the owner declined, how far each source has been read, the timeline | `<data folder>/memory_store.db` |

The Index can be thrown away and rebuilt from the files. What it knows about a fact that
is not in the file (who saved it, how sure, from what) is also on the Tape, and is read
back from there if the database is lost.

The Store is the opposite: **it cannot be rebuilt, so do not delete it**, and it is
private, so it really deletes (see [History that can be deleted](#history-that-can-be-deleted)).
That is why it is a file of its own and not more tables in `memory.db`: one file people
are told they may throw away, one they are told they may not. Both are opened with
SQLite's `secure_delete`, so a removed row is overwritten, not just unlinked.

## The Library

First use creates `memory/shared/` with six starter notes (`about-me.md`,
`preferences.md`, `people.md`, `projects.md`, `decisions.md`, `rules.md`) and an empty
`memory/agents/`, which holds one folder per agent. A starter note the owner deletes is
not brought back.

Anyone may add, edit, move or delete notes in the app or with any editor. A **scan**
notices what changed on disk and brings the index and the record up to date. It runs when
the owner asks (`POST /api/memory/scan`), in the worker every
`memory.scan_interval_s` seconds while `memory.watch_disk` is on, and for one note when
that note is read or about to be changed.

What a scan does and does not look at:

- A note is a file whose name ends in `.md`. Other files are ignored.
- Hidden names (starting with a dot), and editor leftovers (`name~`, `#name#`, `~$name`,
  `.tmp`, `.swp`, `.bak` and similar) are ignored, folders included.
- **Links are never followed**, wherever they point. A linked folder (or Windows
  junction) is not entered; a linked note is listed as skipped. A hard link cannot be
  told apart from an ordinary file and is read like one.
- A note that is not UTF-8, contains a NUL, is larger than `memory.max_note_kb`, or has a
  name the Library cannot address is **skipped with a reason** (shown in
  `GET /api/memory/status`). If it was part of memory before, it leaves memory until it
  is fixed, and its facts come back with the same ids when it is.
- An unchanged note (same size and modification time) is not read again. A note changed
  within the last two seconds is read once more at the next scan, in case a second edit
  landed inside the disk's timestamp resolution.
- A **rename** is recognised when a note has gone and a new one has exactly the same
  bytes. Its facts keep their ids. If the content changed too, the notes are recorded as
  one removed and one added, and each fact that still carries its id is recorded as moved.

### Facts

A fact is one statement with a stable id. Exactly which lines are facts:

> A line that starts **in the first column** with `-`, `*` or `+`, then a space or tab,
> then text containing at least one letter or digit, **outside a fenced code block**.

Three places look like that and are not facts, because an id written there would change
what the file means to another program or to a Markdown viewer:

- **YAML front matter**: the block from a first line of `---` to the next `---` or `...`,
  when it starts with a `key:` line and holds only such lines, list items and `#`
  comments. A `tags:` list there belongs to whatever tool reads it. A note that merely
  begins with a rule is not affected.
- **An HTML comment that runs over several lines**, and a bullet that opens a comment it
  does not close. An id inside would end the comment early.
- **A code block**, by the usual rules: a block opened with four backticks is not closed
  by three, and a line with a language name after the marks does not close a block.

Indented lines directly under it (nested bullets, a wrapped sentence) belong to it: they
are searched with it and removed with it. Numbered lists, indented bullets, bullets inside
code fences and all other text are free text: searchable, but with no identity.

The id is a 26-character ULID in a trailing HTML comment, which Markdown viewers hide:

```markdown
- Prefers meetings after 10 a.m. <!-- m:01JABCDEFGHJKMNPQRSTVWXYZ0 -->
```

- A fact typed without an id is given one at the next scan (or at once when saved through
  the app). The file is rewritten atomically and **nothing else in it changes**, byte
  for byte: line endings, trailing spaces, a missing final newline. If the file changed
  again while it was being read it is left for the next scan.
- **The background scan waits for a file to go quiet.** Writing ids replaces the file, and
  a program that still had it open would then finish its save into a file that no longer
  exists. So the worker's scan, and the check made when a note is opened, leave a note
  changed in the last two seconds exactly as it is and as it was recorded, and come back
  for it about ten seconds later. Pressing Scan does not wait.
- If the ids cannot be written (the file or folder is read-only), the note keeps what was
  recorded about it and is listed under skipped with the reason.
- Editing the line's text edits the fact. Deleting the line retires it. Moving the line
  to another note moves it. **Copying** a line leaves the id with the original; the copy
  gets a new id.
- Typing a retired fact's line back, id included, revives the same fact.
- Text inside an HTML comment is not searched. The starter notes use that for their
  one-sentence description.

What does not belong in a file a person reads is kept in `memory.db` by fact id: `kind`,
`confidence` (0 to 1), `confirmed` (the owner has said it is true), `saved_by` (the actor),
`evidence` (short references such as `tape:<event id>` or `chat:<message id>`), and times.
A fact typed by the owner, in the app or on disk, is confirmed, has confidence 1 and takes
its kind from the note it is in (through `memory.kind_notes`), or `note`.

### Writing safely

- Every write through the app is a temp file in the same folder followed by a replace, so
  a reader sees the old note or the new one, never half. The temp file is hidden
  (`.<26 characters>.tmp`); one left behind by a crash is removed by a scan an hour later.
- Paths in the API are relative to the Library with forward slashes. Refused: absolute
  paths, `~`, `..`, `.`, empty parts, backslashes, drive letters, `: * ? " < > |`, NUL and
  control characters, names Windows reserves (`con`, `nul`, `com1`, ...), names starting
  with a dot, names starting or ending with a space or ending with a dot, anything not
  ending in `.md`, anything through a link, anything longer than 400 characters or deeper
  than 12 folders, a name longer than 240 bytes, and a spelling that differs from a name
  already there only in case or in how an accented letter is stored (macOS keeps names
  decomposed; a browser sends them composed). The refusal names the spelling to use, which
  is the one `GET /tree` lists.
- The web and worker processes may write at once. Every change to the Library holds a
  file lock, `memory.lock` in the data folder, across processes. On a file system that
  cannot lock, a second guard remains: an app write is recorded with a guarded Tape write
  on the note's subject, and if another writer got in between it is refused with a 409
  and the next scan records what is really on disk.
- A hand edit that nobody has recorded yet is recorded (as `owner:disk`) before an app
  write to the same note, so history never loses a version.

## Events

Subjects are `memory_note:<path>`, `memory_fact:<id>` and `memory_candidate:<id>`. No
event carries a note's text, a fact's text, a search query, a proposed fact's statement or
anything read from a source.

| Kind | Means |
|---|---|
| `memory.note.saved` | A note was created or changed. `ref` is the hash of its bytes and `size` their length; the bytes are in the Store under this event's id until they are purged. `moved_from` when it is a rename. |
| `memory.note.removed` | A note left the Library: deleted, renamed (`moved_to`) or no longer readable (`reason`). |
| `memory.fact.saved` | A fact exists, with kind, confidence, confirmed, evidence and the `note_ref` to read it in. `revived` when it came back. |
| `memory.fact.edited` | `changed` lists what did: `text`, `note`, `kind`, `confirmed`. |
| `memory.fact.retired` | A fact's line is gone, with a reason. |
| `memory.recalled` | The receipt for one retrieval (below). |
| `memory.history.purged` | Stored versions were deleted for good. `why` is `note_deleted`, `forgotten_everywhere` or `retention`; then counts only: `versions`, `bodies`, `notes`. |
| `memory.mined` | One mining pass over one source, counts only: `source`, `trigger` (`schedule` or `owner`), `read`, `skipped`, `proposed`, `saved`, `sent`, `ignored`, `dropped` by reason, the `model` that answered and whether it was `local`, `error` (our sentence), and `cursor`: how far the source has now been read, as a plain marker (a Tape position for the built-in sources; left out if a source's cursor is anything but letters, digits and `:_.-`). |
| `memory.candidate.settled` | What became of a proposed fact: `candidate_id`, `source`, `outcome` (`kept`, `declined`, `withdrawn`), `via` (`bench`, `memory`, `miner`, `library`), `fact_id`, `auto`. |

The actor is who did it: `owner`, `agent:<id>`, `system` for the starter notes,
`system:memory` for mining, and `owner:disk` for a change found on disk.

## Recall

```python
from jimmyhq import memory

found = await memory.arecall(core, "when do they like to meet?",
                             actor="agent:scout", caller="chat.reply",
                             scope=("shared", "agents/scout"), k=6, budget_chars=2000)
block = found.render()      # the numbered block for the prompt; the only prompt text
found.passages              # text, note, heading, fact_id, score, why, confirmed
found.trimmed               # ranked high enough but did not fit the budget
found.methods               # ("keyword",), ("keyword", "meaning"), ...
found.meaning_note          # why meaning search did not run, in plain words, or None
```

`memory.recall(...)` is the same for code that is not async. `scope` names folders:
`"shared"`, `"agents/<id>"`, any other folder, or `"*"` for everything.

How it works:

1. **Keyword search**: SQLite FTS5 with stemming over each passage and its headings. The
   question's words are matched with OR and ranked by BM25. A match less than
   `memory.keyword_floor` (20%) as good as the best is dropped, so passages that only
   share common words do not pad the result.
2. **Meaning search**, when an embeddings model is usable: the question is embedded and
   compared with every passage's vector. Matches under `memory.min_similarity` are dropped.
3. **Fusion** by reciprocal rank: a passage scores `weight / (60 + rank)` in each list it
   is in (`memory.weight_keyword`, `memory.weight_meaning`, both 1), summed. Only order
   matters, so the two methods' own scores never need to agree.
4. **Small preferences**: a confirmed fact scores 15% higher (`memory.confirmed_boost`);
   text changed today scores 10% higher (`memory.recency_boost`), halving every
   `memory.recency_half_life_days` (90).
5. **Near-duplicates** of a passage already admitted are dropped.
6. **The budget**: passages are admitted whole, best first, until there are `k` or the
   next would take `render()` past `budget_chars`. One that does not fit is left out and
   listed in `trimmed`; a smaller one further down may still fit. Nothing is ever cut.

### Receipts

Every recall prints `memory.recalled`: the caller and actor, the question's length and
a keyed hash of it (not the question), the scope, each **admitted** passage's id, note, character
count and why it matched, how many were trimmed, which methods ran, the embeddings model
if meaning search ran, and `shown_chars`, which is exactly `len(found.render())`. The
receipt describes what the model was given, to the character, and a test rebuilds the
length from the receipt's own numbers to prove it.

- **The hash is keyed.** `query_hash` is HMAC-SHA256 of the question under a random key
  made on first use and kept in the secret store as `memory.receipt_key` (never logged,
  never returned by the API). The same question gives the same hash on one install, so two
  recalls can be told apart or matched; without the key a guess cannot be checked against
  it, and the same question on another install hashes differently. Someone who holds both
  the Tape and `secrets.json` can still test guesses. Deleting the key makes every earlier
  hash unmatchable; a new one is made at the next recall.
- **A private recall says nothing about the question.** `recall(..., private=True)` (and
  `arecall`) prints the receipt with `query_hash` `""` and `query_chars` `0`, and with no
  cause, root or subject, so it is tied to no conversation. Everything about what was
  handed over (passages, character counts, methods, `shown_chars`) is unchanged, and so is
  `render()`. Chat passes it for messages sent off the record. The embeddings model is
  still asked, under the same local-only rule, and its receipt carries no text.

## Meaning search

Vectors come from the models module's `embeddings` role (`brain.embed`, see
[models](models.md)). It is optional and never blocks:

- No embeddings model, a provider that is down, or one slower than
  `memory.embed_timeout_s`: the search matches words only and says why.
- `memory.embed_local_only` (on by default) allows only a provider marked local to see
  note text or a question. With it on and only a hosted embeddings model, search matches
  words only and the status says what to change.
- The worker embeds passages in the background, `memory.embed_batch` at a time. After a
  failure it waits `memory.embed_retry_s` before asking that model again.
- A vector is stored as float32 bytes with the model that made it (`provider/model`) and
  the hash of the passage text, so an unchanged passage keeps its vector through a
  rebuild. When the configured model changes, the old vectors are ignored at once,
  made again in the background, and deleted once the new ones are complete.
- The index and the question always use the first usable model of the role. If it fails,
  the role's fallbacks are not tried: vectors from two models cannot be compared.

**Scale.** Vectors are compared by brute force with numpy, one matrix product per search,
with the matrix cached per process until the index changes. Measured on a laptop with
768-dimension vectors:

| Passages | Search | Reload after any change | Held in memory |
|---|---|---|---|
| 5,000 | 0.4 ms | 15 ms | 15 MB |
| 20,000 | 1.6 ms | 75 ms | 61 MB |
| 50,000 | 4 ms | 200 ms | 154 MB |

Each process that searches holds its own copy. Past roughly a hundred thousand passages
that stops being reasonable (several hundred megabytes and a visible pause after each
change) and an approximate index is needed. A Library of 10,000 notes with 20,000 facts
scanned from nothing in 6 s, a rescan with nothing changed took 0.07 s, a keyword search
10 to 15 ms, saving one note 6 ms and a full reindex 1.3 s.

## Remembering and forgetting

```python
fact = memory.remember(core, "Prefers meetings after 10 a.m.", kind="preference",
                       actor="agent:miner", confidence=0.7, evidence=["tape:01J..."])
fact.created        # False: a live fact already said this, and that fact was returned
memory.forget(core, fact.id, actor="owner", reason="No longer true.")
```

Without `note`, the kind picks the note: `memory.kind_notes` (by default preference,
person, project, decision and rule each to their starter note) and `memory.default_note`
(`shared/about-me.md`) for everything else. A note that does not exist is created.

`remember` refuses a duplicate of a live fact anywhere in the Library and returns the
existing one. Two statements are duplicates when they contain the same numbers in the same
order and, ignoring case and punctuation, are equal (spaces ignored too, so "10am"
matches "10 a.m."), or one only adds a few words to the other (at least 85% of the
distinct words shared). "After 10" and "after 11" are different facts, and so are two
statements of any length in which one word is swapped for another ("every Monday" and
"every Tuesday", "always" and "never"). The rule is cautious on purpose: wrongly refusing
a fact loses something, and keeping a near-repeat only costs a line. It does not
understand meaning: a long statement and the same one with "not" added count as one, so
a caller that records a reversal should forget the old fact first.

`forget` removes the line and anything nested under it, and retires the fact. The note's
earlier versions, which still contain the line, stay in the Store and can be read from its
history; `forget_everywhere` (below) is the one that removes them. A fact whose line is
taken out, by `forget` or by hand, is remembered as declined, so mining does not put it
back.

## History that can be deleted

Each time a note changes, its bytes are kept so its history can show what it said before.
They are kept by memory, in `memory_store.db`, not in the shared Vault, because the Vault
cannot delete and memory must be able to. A body is stored once per content hash; a table
links each version (the id of its `memory.note.saved` event) to its body; a body is removed
with the last version that points at it. A version whose text is gone answers 410, "The
text of this version is no longer stored", and its history entry has `available: false`.

These delete history. All but erasing a proposed fact that was never saved print
`memory.history.purged` with counts only:

- **Deleting a note with its history**: `memory.delete_note(core, path, actor=...,
  history=True)`, `DELETE /notes/{path}?history=true`. Every version of the note goes,
  followed back through renames (and not a different note that later took an old name), and
  the words of its retired facts leave the index and mining's records. It also works on a
  note that was deleted earlier without its history.
- **Forgetting a fact everywhere**: `memory.forget_everywhere(core, fact_id, actor=...,
  reason=...)`, `DELETE /facts/{id}?everywhere=true`. The fact is retired, and **every
  stored version of any note that contains its line** (its id or its exact words) is
  deleted: whole versions, so other text in those versions goes with it. Its words are
  blanked in the index, in the proposed fact it came from (with its quotes, its evidence
  and any reason given for declining it) and in the record of declines, where only a hash
  stays (enough to stop the same words being proposed again; it cannot be read back).
- **Erasing a proposed fact**: `memory.erase_candidate(core, id)`,
  `DELETE /candidates/{id}`. A proposed fact that was declined, ignored or withdrawn is
  otherwise kept as it was proposed, statement and quotes included, and a decline keeps
  the statement in the record of declines so that a near-repeat can be recognised.
  Erasing blanks the statement, quotes, evidence and reason, leaves only the hash in the
  record of declines, withdraws its Bench card if one is open, and, if the fact was saved,
  forgets that fact everywhere, which prints the purge event of that step. Erasing a
  proposed fact that was never saved prints none (there are no versions to count).
- **Retention**: `memory.history_keep_versions` (at most N versions per note) and
  `memory.history_keep_days` (nothing older than D days, even a note's only version; the
  note itself is never touched). 0 keeps everything, and both default to 0. The worker
  applies them about once an hour.

After a purge the keyword index is merged so the deleted words leave it, orphaned vectors
are dropped, and the write-ahead logs of both databases are checkpointed and emptied. A test
searches the raw bytes of every file in the data folder and finds nothing. If something was
reading at that moment the emptying is retried by the worker.

**When a conversation is deleted for good** (see [chat](chat.md#deleting)), chat calls
`memory.evidence_erased(core, refs, actor=, reason=)` with the references of the erased
messages. Every quote of them is removed from the proposed facts that cited them, and the
Bench card of each such proposal has its text erased from the Tape (`Tape.erase`) and
leaves the Bench. A proposal still waiting for a decision is filed again from its other
evidence, or withdrawn when it rested only on that conversation. **Facts already saved
stay**, and so does each fact's `evidence`: the references now name erased events, which
hold no words. The chat source reports an erased event as no longer there.

Two things make that hold when the timing is unkind. A mining pass asks the model without
holding the Library, which can take minutes; if a conversation is deleted meanwhile, the
pass would otherwise store quotes of it afterwards. So just before a batch's proposals are
stored, holding the Library (which `evidence_erased` takes too), each one's evidence is
checked again with the source's `exists`: evidence that has gone is dropped with its
quotes, and a proposal left with none is dropped (`no_evidence`). And `evidence_erased`
can be called again with the same references: chat does so until it has succeeded once,
and does not record the conversation as deleted before then.

What "forget everywhere" cannot reach, said plainly:

- **A Bench card.** A proposed fact sent to the Bench puts its statement and its evidence
  quotes on the Tape as a card, like anything else the owner is asked to decide, and the
  Bench's stored view (`views.db`) lists the card's title while it is open. The Tape does
  not delete. So for a fact that had a card, these stay in `tape.db` after any delete here:
  the statement (the card's title), and in the card's body up to five quotes of up to 240
  characters each from the items it rested on, which can be the owner's own chat messages,
  a task's title and notes, a reply of the model's, or another card's text. A fact saved
  automatically never had a card, and with `memory.mine_max_open_cards` at 0 no card is
  ever filed. Removing a card's text needs a delete in the Bench and the Tape, which
  memory cannot do.
- **A reason.** The reason typed when forgetting a fact is part of `memory.fact.retired`
  on the Tape and of the retired fact in the index, and the reason typed on a Bench card
  is part of the Bench's record. Do not put in a reason what the delete is meant to remove.
- **The hash in the record of declines** is a plain SHA-256 of the statement's words. It
  cannot be read back, but someone who has the file and guesses the exact statement can
  confirm the guess.
- **The source.** The chat message, task or decision a fact was read from stays where it
  is. Equally, deleting or archiving source material later does **not** delete facts
  already saved from it: "forget everywhere" is the tool for that.
- **Other copies of the words**: a line copied into another note (the copy is a different
  fact and stays in its file, though stored versions containing the same words are
  deleted), a backup, an editor's own history, and blocks the disk has freed but not yet
  reused. `secure_delete` and truncating the log are about SQLite's files, not about what
  an SSD or a snapshotting file system keeps underneath.

## Mining

Mining reads what happens and proposes facts. It is `mining.py` and `sources.py`.

```
sources -> items -> a model picks facts, citing item numbers -> code checks every one
        -> candidate -> saved automatically | a Bench card | ignored
```

### Sources

A source yields items: `Item(ref, when_ms, who, text, where)`, where `ref` is a stable
reference without spaces such as `tape:<event id>` and `who` is `"owner"`, `"assistant"`
or another actor. Any module or app adds one:

```python
from jimmyhq import memory

def collect(core, cursor, limit):
    # Read at most `limit` things forward from `cursor` (None the first time), oldest
    # first, never the same thing twice. Return what to resume from.
    return [memory.Item("journal:41", when_ms, "owner", "text", "Journal"),
            memory.Item.skipped("journal:42", when_ms, "locked")], "42"

memory.register_source(memory.Source(
    id="journal", label="Journal", description="What you write in your journal.",
    default_on=False, collect=collect,
    waiting=None,   # optional: (core, cursor) -> (count, there_are_more), for the status
    exists=None,    # optional: (core, ref) -> bool; a candidate whose evidence has all
))                  # gone is withdrawn
```

Registering declares the switch `memory.source.<id>` (a bool, labelled "Learn from:
<label>", with the description as its help), so the settings screen shows it with no UI
work. Call it at import time, from a module that `modules.load()` imports. The cursor is
stored in `memory_store.db`, so a restart resumes where it stopped and the web and worker
processes agree. `memory.unregister_source(id)` takes one out.

Three come with the core. All three read the Tape, which only ever holds what is on the
record; nothing in memory opens the off-the-record chat store, and a test proves it.

| Source | Reads | Passes over, counted by reason |
|---|---|---|
| `chat` | The owner's messages and the model's complete replies, from conversations on the record | `archived` conversations; `not_owner` (a message sent by a token); `incomplete` (a failed or stopped reply); `off_record_question` (a reply regenerated on the record to a question asked off it); `off_record_context` (any other reply written after the conversation was first switched off or on the record: the model that wrote it was shown the off-the-record part and may repeat it, so from then on only the owner's own on-the-record messages are read) |
| `tasks` | A task's title and notes when the owner files it and when the owner completes it | `not_owner` (filed or completed by an agent or a token, or completed by the owner after an agent or a token edited it, since an edit rewrites the title and notes); `gone`. Rounds of a repeating task are not read. |
| `bench` | Each decision as two items: the card (its filer's words) and the owner's answer with its note or reason. The owner's item holds nothing the filer wrote: not the card's title, and for a button the filer made up, not its label | `own_card` (memory's own cards); `empty` (Not yet, Got it or Undo with no note); `gone`. A long card body is shortened, not skipped. |

Every source also has `empty`, `too_long` (longer than `memory.mine_item_chars`),
`instruction` (below), `unreadable` and `model_failed`.

**Written by a token.** The choice made here: text a token or an agent wrote is never read
as the owner's. A token's chat message and an agent's task are skipped; an agent's Bench
card is shown to the model marked with its filer, and cannot carry a fact by itself.

### The miner

A worker job (`memory.mine`) runs a pass every `memory.mine_interval_s`, and
`POST /api/memory/mine` runs one now. A pass takes at most `memory.mine_batch` things from
each source that is on, oldest first, and makes one model call per source through the
Brain: the role is `memory.mine_role` (default `cheap`), `local_only` is
`memory.mine_local_only` (**on by default**: the owner's life is not sent to a hosted model
unless they choose that), and `keep_text=False`, so the receipt has token counts and no
text. Only one pass runs at a time across both processes.

If no model can be asked under those rules the pass does nothing, reads nothing, and
`GET /sources` says exactly why and what to change. When a model call fails, the cursor
does not move and the source's `last_error` says what happened:

- **The provider's failure** (cannot be reached, switched off, no key, out of credit, over
  its limit): nothing is counted against the items and none is ever passed over, however
  long it lasts. They are read when the model answers again.
- **A failure that may be about what was asked** (an answer that cannot be read, a cut-off
  or refused answer, a rejected request, no answer in time): the next pass takes a smaller
  batch, halving until it is smaller than the one that failed, and grows back as passes
  succeed. A single item that fails three times on its own is passed over and counted as
  `model_failed`, so one bad item costs one item and cannot stop a source for ever. An item
  that only ever times out is not passed over: raise the time, or choose a faster model.

**Time.** A pass takes at most `memory.mine_pass_seconds` (60): a model call still running
then is given up, and the sources not reached are listed as `unfinished` and read first at
the worker's next tick, ten seconds later, without waiting for the interval. Other jobs
wait for a pass, so this is the longest mining can hold them, plus the time to file what
was found.

**The prompt.** The system text says what a fact is, how to set confidence, what not to
propose, and that the items are data. The items follow in the user message between two
lines carrying a marker made fresh for each call, one item per line:
`[3] (the owner; Chat: Plans; 2026-01-08) "its text as a JSON string"`. The "where" in
the heading (a conversation's title, a card's source) is whatever its writer typed, so it
is reduced to letters, digits, spaces and `:,.'-` before it is shown, and cannot imitate a
heading. The answer is
structured: `{"facts": [{"statement", "kind", "confidence", "evidence": [numbers]}]}`.

**What code does with the answer** (design rules 6 and 7). Each proposed fact ends exactly
one way, and `proposed = saved + sent + ignored + everything dropped`:

| Dropped as | When |
|---|---|
| `no_evidence` | It cites no item number, or any number that is not in the list |
| `not_from_owner` | None of the items it cites was written by the owner |
| `secret` | The statement looks like a password, key, token, PIN or card number |
| `too_long` | The statement is longer than 200 characters |
| `invalid` | Not a one-line statement, a confidence that is not a number from 0 to 1, a statement that reads as an instruction, carries a link, an image or a tag, or repeats the call's marker, or not an object at all |
| `too_many` | Everything after the first 50 facts of one answer |
| `declined` | The owner already said no to it, or forgot it: the same words ignoring case, punctuation and spacing, or a near-duplicate by the Library's rule |
| `duplicate` | A live fact, or a candidate still waiting, already says it |

A kind the Library does not file under becomes `note`. The fact's evidence is the `ref`s of
the items it cited, never anything the model wrote. Short quotes of up to five cited items
are kept with the candidate, with anything that looks like a secret replaced by
`[removed]`.

**Saved without asking** needs more than confidence. All of these must hold, or the fact
goes to the Bench however sure the model says it is: every item it cites was written by
the owner; every number in the statement is in those items; at least half of the
statement's own words (small words aside, compared by their first five letters) are in
those items; and it carries no web address. This is a comparison of words, not of meaning:
it stops a statement that has little to do with what it cites, and it also sends a fair
paraphrase, or a statement in another language than the item, to the Bench.

**What this can and cannot guarantee.** Code guarantees: a fact is tied to real items; a
fact resting only on text the owner did not write is never kept; a fact resting partly on
such text is never saved without asking; an item counted as the owner's holds only what
the owner typed and fixed words of memory's own; a statement saved without asking shares
its numbers and half its words with what the owner wrote; an item that plainly addresses a
model ("ignore your instructions", "system prompt", something imitating the fence) is
never shown to it; the fence cannot be closed from inside, because item text is
JSON-encoded and the marker is unguessable; a statement shaped like a secret is not
stored; no answer, however malformed or large, stops the pass or the worker. Code cannot guarantee that a
statement is *true*, or that the model was not steered by wording the filter does not know,
inside something the owner really wrote (a page they pasted into a message, say). The
filter for instructions and the filter for secrets are patterns: they catch the obvious
forms and they have false positives ("The owner's password is long" is dropped). What
stands between a wrong fact and memory is then the confidence thresholds, the Bench, and
the fact that everything saved automatically is listed for review. Setting
`memory.auto_save_above` above 1 means nothing is ever saved without the owner.

### Candidates

Every fact that survives is a candidate in `memory_store.db`: statement, kind, confidence,
evidence refs, quotes, source, when proposed, and a state.

| State | Means |
|---|---|
| `waiting` | For the Bench, and there is no room yet |
| `bench` | Has an open card |
| `saved` | In the Library. `saved_automatically` says how it got there; `needs_review` is true until the owner confirms or forgets it |
| `declined` | The owner said no, or forgot it. Never proposed again |
| `ignored` | Confidence below `memory.ignore_below` |
| `withdrawn` | It stopped being valid, or its card was taken off the Bench |

- Confidence **at or above** `memory.auto_save_above` (0.85): saved at once with
  `remember(..., confirmed=False)`, actor `system:memory`.
- **Below** `memory.ignore_below` (0.4): recorded as ignored. If the model later proposes
  the same thing with enough confidence, the new proposal replaces it.
- **In between**: the Bench.
- Each is set freely, in either order: `auto_save_above` from 0 to 2, `ignore_below` from
  0 to 1. `ignore_below` 0 ignores nothing. A validator sees one setting at a time, so the
  pair is read together where it is used (`mining.thresholds`). If `ignore_below` is set
  higher than `auto_save_above` there is nothing in between: a fact at or above
  `auto_save_above` is saved, every other one is ignored, nothing is sent to the Bench, and
  `GET /sources` says so in `mining.thresholds_note` with what to change. Nothing the owner
  asked to have ignored is saved because of it.
- A change applies from the next pass. Candidates already waiting or on the Bench stay
  where they are.

**Bench cards.** One card per candidate: kind `memory.candidate`, source "Memory", filed by
`system:memory`, the statement as its title, a body with the confidence in words and as a
number and the quotes it rests on with where each came from, subject
`memory_candidate:<id>`, and the Bench's own Approve and No (No needs a reason, as on every
card). Approve saves the fact as confirmed. No records the decline with its reason.
`memory.mine_max_open_cards` (5) caps how many are open at once; the rest wait in the order
they were proposed and are filed as cards are decided. A card is withdrawn when the same
fact is saved another way, or when everything it rests on has gone.

**Two processes.** Mining runs in the worker and the owner's click arrives in the web
process. Nothing is held in memory: `bench.on_decision` applies the decision at once where
the click lands, and the worker's job (every ten seconds) reconciles every candidate with
its card from the Tape, so a decision is applied even if the web process stops right after
recording it. Both paths check the candidate's state under the Library's lock, so a
decision is applied once.

**Taking it back.** A fact saved automatically is listed by `GET /candidates?state=review`
until the owner confirms it (`PATCH /facts/{id}` or `POST /candidates/{id}/keep`) or
forgets it. Forgetting it, any way at all, records the decline.

### What the owner can see

`GET /sources` gives, for each source, whether it is on, when it was last read, how many
items it has read and how many wait, the counts for the last pass and for all time, and
the last error; and overall, when mining last ran and will next run, which model it would
ask and whether that is local, and when mining is not happening the one reason and what to
change. `GET /candidates` lists proposed facts by state. `GET /activity` is a timeline built
from memory's own table: facts saved and by whom, edited, confirmed, forgotten; notes
changed by the owner in the app or by hand; mining passes; purges. It holds ids and counts
and looks the fact's words up when it is read, so a purged fact shows as empty. It keeps
the newest 20,000 rows; the Tape has the full record.

## HTTP API

Under `/api/memory`. Reading needs the owner or a read token. Anything that changes the
Library needs the **signed-in owner**: a token cannot rewrite the owner's memory in this
release. Errors are `{"detail": "<one sentence>"}`.

| Route | Does |
|---|---|
| `GET /tree` | `{folders: [path], notes: [{path, size, modified_ms, facts}]}` |
| `GET /notes/{path}` | `{path, text, revision, size, modified_ms, facts: [Fact]}` |
| `PUT /notes/{path}` `{text, revision?}` | Create or replace. Returns the note as stored: **facts typed without an id are given one, so use the returned text**. 409 if `revision` is not the note's current one. |
| `DELETE /notes/{path}` | `{ok: true}`. With `?history=true`, every stored version goes too: `{ok: true, purged: {versions, bodies}}`; this also works for a note already deleted, and is 404 only when there is neither a note nor any history. |
| `POST /notes/move` `{from, to}` | The note at its new path. 409 if something is there. |
| `GET /notes/{path}/history` | `{path, versions: [{version, ts, actor, size, facts, note, available}]}`, newest first, followed back through renames |
| `GET /notes/{path}/history/{version}` | `{version, ts, actor, note, size, text}`. 410 when the text is no longer stored. |
| `GET /facts?kind=&q=&confirmed=&note=&limit=&before=` | `{facts: [Fact], next_before}`, most recently changed first. `q` is in the URL, so the web server's access log records it like a GET search. |
| `GET /facts/{id}` | `{fact}`, retired ones included |
| `POST /facts` `{text, kind, note?}` | 201 `{fact, created}`; `created` false when it already existed |
| `PATCH /facts/{id}` `{text?, kind?, confirmed?}` | `{fact}` |
| `DELETE /facts/{id}` `{reason?}` | `{fact}` as retired. With `?everywhere=true`: `{fact, purged: {versions, bodies, notes}}`, and the fact's `text` comes back empty. Works on a fact already retired. |
| `GET /search?q=&scope=&k=`, `POST /search` `{q, scope?, k?}` | What recall returns, as JSON. Prints a receipt. Prefer POST: a GET's question is written to the web server's access log. |
| `GET /status` | Counts, index version, last scan, skipped files, meaning search state, and `library_path`: the absolute path for the signed-in owner, `null` for a token |
| `GET /sources` | `{sources: [Source], mining: Mining}` (below) |
| `GET /candidates?state=&source=&limit=&before=` | `{candidates: [Candidate], next_before, counts}`, newest first. `state` is `waiting`, `bench`, `saved`, `declined`, `ignored`, `withdrawn`, or `review` (saved automatically, not yet confirmed); anything else is 422. `counts` has one number per state and `review`. `limit` 1 to 200 (50). |
| `POST /candidates/{id}/keep` | `{candidate}`. Saves it as a confirmed fact from any state; on the Bench it approves the card, so the two agree; for one saved automatically it confirms the fact. 404 unknown id; 410 if its words were erased by "forget everywhere". |
| `POST /candidates/{id}/decline` `{reason}` | `{candidate}`. `reason` is required, 1 to 300 characters (422). On the Bench it answers the card with No; if the fact was saved it is forgotten. 409 if already declined; 404 unknown id. |
| `DELETE /candidates/{id}` | `{candidate}` as erased (above). The signed-in owner only. 404 unknown id. |
| `POST /mine` `{source?}` | Runs one bounded pass now, even when background mining is off: `{ran, reason, sources: {id: Counts}, totals, filed, unfinished: [source id]}`. When it cannot run (another pass is running, no model, every source off), 200 with `ran: false` and the `reason`. 404 unknown source; 409 if that source is switched off. |
| `GET /activity?limit=&before=` | `{activity: [{id, ts, type, actor, note, detail, fact}], next_before}`, newest first; `before` is the last `id` of the page before. |
| `POST /scan`, `POST /reindex` | `{scan: {...}}`: what changed |

`Fact` is `{id, text, note, line, kind, confidence, confirmed, saved_by, evidence,
created_ms, updated_ms, retired_ms, retired_reason}`.

`Candidate` is `{id, source, statement, kind, confidence, evidence: [ref], quotes: [{ref,
who, when_ms, where, quote}], proposed_ms, state, state_ms, reason, fact_id, card_id,
saved_automatically, needs_review}`. An erased one has `statement` `""`, `quotes` and
`evidence` `[]` and `reason` null. One put back from its Bench card after the store was
lost has `evidence` and `quotes` `[]`, and `source` `"unknown"` if its source is gone.

`Source` is `{id, label, description, on, setting, last_read_ms, items_read,
items_waiting, items_waiting_more, items_waiting_as_of_ms, last_run: Tally, all_time:
Tally, last_error: {message, at_ms} | null}`. `items_waiting` is counted by the worker
about once a minute and after each pass (`null` until then, or for a source that cannot
count); `items_waiting_more` means "at least that many".

`Tally` is `{read, skipped: {reason: n}, skipped_total, proposed, saved, sent, ignored,
dropped: {duplicate, declined, no_evidence, not_from_owner, secret, too_long, invalid,
too_many}, dropped_total}`. `Counts`, from `POST /mine`, is the same without the two totals and with
`error`.

`Mining` is `{on, working, reason, last_run_ms, next_run_ms, interval_s, model: {role,
name, is_local, local_only}, auto_save_above, ignore_below, thresholds_note, pass_seconds,
unfinished: [source id], open_cards, max_open_cards, waiting_for_bench}`. `auto_save_above`
and `ignore_below` are the values as applied; `thresholds_note` is null unless the two were
set so that they cross. `on` is the switch; `working` is whether a pass would do anything;
`reason` is null when working, otherwise one of: mining is switched off, every source is
switched off, no model is chosen for the role, the role's only model is hosted and
local-only is on, or the role's model cannot be used (and why).

Timeline `type`s: `fact_saved`, `fact_edited`, `fact_confirmed`, `fact_forgotten`,
`note_saved`, `note_removed`, `mined`, `candidate_kept`, `candidate_declined`,
`history_purged`. `fact` is `{id, text, kind, retired}` or null.

## Layout

| File | Holds |
|---|---|
| `__init__.py` | The Python API other modules use, `build` and `jobs` |
| `library.py` | The `Memory` service: the sync engine, notes, facts, locking |
| `notes.py` | Pure text: which lines are facts, chunking, near-duplicate comparison |
| `paths.py` | Path safety |
| `search.py` | Recall, fusion, the budget, receipts, vectors and the embedding job |
| `store.py` | `memory_store.db`: versions of notes, candidates, declines, cursors, the timeline; deleting for good |
| `sources.py` | The source registry and the chat, tasks and bench sources |
| `mining.py` | The miner, the prompt, the checks on its answer, candidates, Bench cards, status |
| `kinds.py`, `settings.py`, `routes.py`, `errors.py` | Event kinds, settings, the HTTP API, `LibraryError` |

## Limits worth knowing

- A scan holds the Library's lock while it works. The first scan of a few thousand new
  notes takes a couple of seconds, during which saves wait; later scans only stat files.
- A program that keeps a note open and writes it in pieces more than two seconds apart
  can still lose the later pieces if a scan gives the note ids in between, and so can one
  caught mid-save by `remember`, `forget` or a fact edit on the same note. Editors that
  save in one step, or through a temp file, are not affected.
- After a crash in the middle of a scan or a save, the files are whole and the next scan
  puts the index right, but the Tape can hold a second `memory.fact.saved` (marked
  `revived`) for facts whose first event was printed just before the crash.
- Keyword search splits words at spaces and punctuation. Text in a script written without
  spaces (Chinese, Japanese, Thai) is matched only as whole runs; meaning search is what
  finds it. A question made only of very common words still returns the passages that
  use them most.
- A bulk import prints one event per note and one per fact.
- Version bodies saved to the Vault before history moved to the Store are not read any
  more: such a version says its text is no longer stored.
- The duplicate rule compares words, not meaning or order: once "prefers tea to coffee"
  is declined, "prefers coffee to tea" is taken for the same statement and not proposed.
  What "never proposed again" catches: the same words in any case, punctuation or spacing,
  and the same statement with a few words added or left out (at least 85% of the words
  shared). It does not catch a word swapped ("prefers tea over coffee"), or a synonym; those come back, as a Bench card or,
  if the model is sure and the words are the owner's, saved for review.
- A mining pass holds the worker while the model answers, for at most
  `memory.mine_pass_seconds`; other jobs wait for it.
- If `memory_store.db` is lost: each source resumes from the place recorded in its last
  `memory.mined` event, so nothing is read twice (a store lost before those events carried
  a place is read again from the start, and what is already in memory is dropped as a
  duplicate); a No given on a Bench card is declined again from the card, and a fact the
  owner forgot is recognised from the index; an open memory card gets its proposed fact
  back without its evidence. Lost for good: note versions, proposed facts that had no
  card, ignored ones, the counts and the timeline.
- `GET /sources` counts only what mining itself did. A fact typed by hand is not in it.
- Saving through the app keeps the line endings it is sent; a browser sends `\n`.
- `GET /facts?q=` matches text without regard to case for ASCII letters only.
- `POST /reindex` cuts the passages again from the files. What is known about each fact is
  not touched by it.
