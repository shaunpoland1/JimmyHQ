# Heard

Heard keeps what your wearable recorder transcribed, as conversations you can browse by day
and search. Your recorder (such as an Omi pendant) or its app sends Heard the words it
heard; Heard keeps them on this computer, shows who spoke, and lets you search every word.
A model on your own machine can summarise a conversation and pull out things you said you
would do, when you ask.

## What it promises

- **Audio is never kept.** Heard takes text only. If a message carries audio, or the
  address of audio, that part is ignored: it is not stored, and the address is
  never fetched. Heard has no permission to reach the network at all.
- **It is off your record.** Heard is a private app: it writes nothing to your record,
  puts nothing on your Bench, offers nothing to Memory, gives your agents no tools and
  offers no feed. A model reads a conversation only when you press a button, and only a
  model on your own computer may answer. Without one, everything else still works.
- **A delete is a real delete.** Deleted lines are overwritten in the file, and removed
  from the search index too.
- JimmyHQ keeps its usual count of model calls (when, how long, how many tokens). It never
  holds what was asked or answered.

Because it is private, **your assistant and agents cannot search Heard**. A private app
cannot offer Memory a source or agents a tool. If you want them to, that is a decision to
make on purpose (see Questions).

## Connect your recorder

You do this once. Heard takes the messages straight from an Omi pendant's own webhooks:
there is no relay to run.

1. Install Heard from **Apps**, under **Made by the JimmyHQ project**.
2. Open Heard's page in **Apps**. Under **Messages from your devices**, make a key, give it a
   name such as "Pendant", and tick **For a sender that can only be given an address**
   (Omi's webhook fields take an address and nothing else). The page then shows, once and with
   a copy button, one address for each kind of message. Copy them now: the key is in them and
   cannot be shown again.
3. In the Omi app, switch on Developer Mode and open **Developer Settings**. Paste the address
   labelled **Omi: Memory Creation Webhook** (it ends `/api/in/heard/conversation/k/...`)
   into **Memory Creation Webhook**. If you also want words as they are said, paste the one
   labelled **Omi: Real-Time Transcript Webhook** (it ends `/api/in/heard/live/k/...`) into
   **Real-Time Transcript Webhook**. Leave **Audio Bytes Webhook** empty: Heard takes words
   only, and has no address for audio.
4. Heard's page says how large its messages get. A finished conversation can be several
   hundred KB, and Heard is allowed that much without any setting changed. If you have
   lowered **Most any device may send in one message (KB)** under **Settings, Apps** (it is
   under the advanced settings), the page says so and what to raise it to.
5. Say something near the pendant, then open **Conversations**. A finished conversation arrives
   when Omi has closed it, after a quiet spell; live lines arrive as they are said.

Until the first message arrives, the page shows only these steps.

Anyone who sees an address can send to it, so treat each as the key it holds: paste it only
into the Omi app, and remove the key on Heard's page if anyone else has seen it. JimmyHQ
never writes the key down; its access log shows `/api/in/heard/conversation/...`.

Where Omi's pages are silent, this guide says so (see **What Omi's pages confirm** below).
Two things to know now:

- **The address must be one Omi can reach.** Omi's pages do not say whether the phone or Omi's
  servers make the call. Their own test tool refuses private and loopback addresses, which
  suggests Omi's servers do. If so, the address must be reachable from the internet and, since
  it holds a key, use `https`. JimmyHQ must also answer to that name (Settings, Access).
  Making JimmyHQ reachable is outside Heard and is a decision about exposing your computer.
- **Use one of the two, not both.** Omi's real-time lines carry no id that links them to the
  finished conversation, so with both on, the same talk is kept twice. The conversation
  webhook brings Omi's own title, overview and to-dos and is kept once however often it is
  sent. Use the real-time one only if you want words as they are said.

### The three addresses

| Address ends in | Takes | Largest message it expects |
|---|---|---|
| `/api/in/heard/conversation` | One JSON object: a finished conversation, as Omi's memory-creation webhook sends it. `?uid=` is kept. | 512 KB |
| `/api/in/heard/live` | A bare JSON list of lines (or the object form below), as Omi's real-time webhook sends it. `?session_id=` says which session; `?uid=` is kept. | 128 KB |
| `/api/in/heard/transcript` | Any of the shapes below, from any other recorder or app. Unchanged from 0.1: it also reads `?session_id=` and `?uid=` when sent. | 512 KB |

A sender that can set headers uses the same addresses with `Authorization: Bearer <your key>`
(a key made without the tick), as before. Heard's own part is the same for all three: what is
read, kept, ignored and refused is described next.

### What to send

One POST for each message, a JSON object (a bare list at `/live`). Heard reads three shapes,
and ignores every field it does not know: audio, an audio address, pictures, places, and
any parameter in the address other than `uid` and `session_id`.

**1. A finished conversation, as Omi sends it.** Omi posts it to `/conversation`, with
`?uid=` in the address; the example below shows the header form at `/transcript`.

```bash
curl -X POST https://jimmyhq.example.com/api/in/heard/transcript \
  -H "Authorization: Bearer <your key>" \
  -H "Content-Type: application/json" \
  -d '{"id": "c-100", "started_at": "2027-01-15T07:30:00+00:00",
       "finished_at": "2027-01-15T07:31:00+00:00", "discarded": false,
       "structured": {"title": "Garden plan", "overview": "Talk about the garden.",
                      "category": "personal",
                      "action_items": [{"description": "Order bulbs", "completed": false}]},
       "transcript_segments": [
         {"text": "Good morning.", "speaker": "SPEAKER_01", "speaker_id": 1,
          "is_user": false, "start": 0.0, "end": 3.5},
         {"text": "Hello Sam.", "speaker": "SPEAKER_00", "speaker_id": 0,
          "is_user": true, "start": 4.0, "end": 6.0}]}'
```

| Field | Meaning |
|---|---|
| `transcript_segments` | **Required.** The lines. Each has `text` (required), `speaker`, `speaker_id` (or `speakerId`), `speaker_name`, `is_user`, `start` and `end` (seconds from the start of the conversation). |
| `id` | The sender's id. The same id sent twice **by the same key** is kept once; another key sending the same id is a different conversation (see **Each key has its own conversations** below). With no id, one is made from the start time and the first words. |
| `started_at`, `finished_at` | When it began and ended: an ISO time, unix seconds or unix milliseconds. With no offset an ISO time is read in your own time zone. If the start is missing it is worked out from the finish, or from when the message arrived. |
| `structured.title`, `.overview`, `.category` | Kept as the title, the recorder's own overview and a category. |
| `structured.action_items` | Each `description` waits as a suggested to-do (at most 20), marked "from your recorder, not checked against what was said": Heard cannot tell whether the conversation says it. It is kept only while **Keep the to-dos the recorder itself lists** is on (it is by default). Nothing is yours until you accept it, and nothing the recorder lists is ever accepted or added to your tasks for you. |
| `discarded` | When true, and **Skip what the recorder marked as discarded** is on (it is by default), the conversation is not stored. |

Who spoke is shown as a name when the sender gave one (`speaker_name`), "You" for the
wearer (`is_user`), otherwise "Speaker 1", "Speaker 2" from the speaker number. You can
rename any of them.

**2. A batch of live transcript, stitched.** Omi posts it to `/live` as a bare list, with
`?session_id=` and `?uid=` in the address:

```bash
curl -X POST "https://jimmyhq.example.com/api/in/heard/live?session_id=s-77&uid=abc" \
  -H "Authorization: Bearer <your key>" -H "Content-Type: application/json" \
  -d '[{"text": "Shall we start?", "speaker": "SPEAKER_00", "speakerId": 0,
        "is_user": true, "start": 0.0, "end": 2.0}]'
```

Any other sender can post the object form, `{"session_id": "s-77", "segments": [...]}`, to
`/live` or `/transcript`. A `session_id` in the body wins over one in the address.

`segments` are as above, with `start` and `end` in seconds from the start of the **session**.
Batches with the same `session_id` join one conversation until nothing has arrived for
**A conversation ends after this much quiet** (5 minutes by default); words after that start
a new conversation. With no `session_id`, the key's own session stands in for it. A
batch sent twice adds nothing, even after the conversation closed, and a longer batch that
repeats earlier lines keeps only the new ones. Lines are placed in time
from when the batch arrived, so send each batch soon after it was spoken.

**3. A single line from any device.**

```bash
curl -X POST https://jimmyhq.example.com/api/in/heard/transcript \
  -H "Authorization: Bearer <your key>" -H "Content-Type: application/json" \
  -d '{"text": "Remember the milk", "started_ms": 1800000000000, "speaker": "Sam",
       "id": "note-1"}'
```

`text` is required. `started_ms` (or `started_at`) says when it was said, `speaker` who said
it, `is_user` whether it was you, `session_id` which stream it belongs to, and `id` makes a
resend harmless. Without an `id`, two lines with the same words and the same `started_ms`
are one. Lines from one key (or session) arriving within the quiet gap join one conversation.

**Each key has its own conversations.** A session id, a conversation id and a line's `id` are
the sender's own words, so Heard keeps them apart by the key that sent them: two keys that send
the same session id make two conversations, and a key cannot join another's live conversation
or make another's real conversation look like a repeat by sending its id first. A key is known
by the name you gave it (an app's keys cannot share a name). The limit: if you remove a key
and make another with the same name, the new one shares what the old one sent. Conversations
kept before 0.2.1 keep their old ids and work as before; a session still going when you update
carries on as a new conversation.

**Times a recorder states.** A time the recorder gives (when a conversation began, when a line
was said) is kept no later than the moment the message arrived plus **Clock slack for a
recorder** (5 minutes by default), whether it came from the door or from an imported file. A
clock set far ahead cannot make a conversation look newer than it is. **Keep conversations
for** goes by the earlier of the time the recorder says the conversation ended and the time
Heard last heard it, which only Heard sets.

**The recorder id.** Omi adds `?uid=` to both webhooks: the id it gives your account. It is
read from the address only (a `uid` in the body is ignored). By default Heard keeps it with the
conversation and shows it on the conversation's page. If you fill in **Only accept this
recorder id**, a message whose `uid` is another, or missing, is refused and not kept. The
refusal says which, and never shows the id it saw.

**Refused.** A message with none of these shapes, no words in it, a list that is not lines of
speech, more than 2,000 lines, or a recorder id other than the one you chose, is refused with
a plain reason, counted by kind under **Settings** (never kept), and the door still answers
`202`: it took the message, Heard decided it was not a transcript. A message larger than the
size you allow is refused by JimmyHQ before Heard sees it, with `413`.

## What Omi's pages confirm

Read on 2026-11-23, from Omi's developer documentation as it stood then. Pages read:

- <https://docs.omi.me/doc/developer/apps/Integrations> (its `.md` form too): the integration
  apps, the memory-creation webhook, the real-time transcript webhook, the audio bytes
  webhook, testing, and the `omi-cli` tool.
- <https://docs.omi.me/doc/how-omi-works/capture-and-transcription.md>: what a stored
  transcript segment is.
- <https://docs.omi.me/llms.txt>: the index, to look for other pages on these webhooks.

**Confirmed there**

- Memory-creation webhook: `POST`; only `?uid=` in the address; the body is **one JSON
  object**, the whole memory: `id`, `created_at`, `started_at`, `finished_at`,
  `transcript_segments`, `structured` (`title`, `overview`, `emoji`, `category`,
  `action_items`, `events`), `apps_response`, `discarded`, `folder_id`, `folder_name`. A
  segment has `text`, `speaker`, `speakerId`, `speaker_name`, `is_user`, `start`, `end`.
- Real-time transcript webhook: `POST`; `?session_id=` and `?uid=`; the body is a **bare JSON
  array** of segments (`text`, `speaker`, `speakerId`, `is_user`, `start`, `end`), sent in
  several calls as the conversation goes on, and only during a live-listen session.
- Audio bytes webhook: `POST` of raw 16-bit PCM bytes (`application/octet-stream`) with
  `?sample_rate=` and `?uid=`, at an interval you set. Heard has no name for it and takes
  none: leave that field empty.
- The addresses are typed into the Omi app under Developer Settings (with Developer Mode on),
  into fields called Memory Creation Webhook, Real-Time Transcript Webhook and Audio Bytes
  Webhook. The pages describe no header, API key or signature for these webhooks, so the key
  has to be in the address.
- "Return 200 OK quickly". JimmyHQ answers `202`, at once, whatever Heard later decides.
- On a stored conversation, `start` and `end` are seconds from the start of the conversation
  (the capture page).

**Not confirmed: Heard assumes, and says so**

- The units of `start` and `end` in the two webhooks: the examples show `10.0` and `15.0` with
  no unit. Heard reads them as seconds from the start of the conversation or session, as for a
  stored conversation. A wrong guess would only mis-place lines in time, never lose them.
- Whether real-time `start` and `end` count from the session or from the conversation, and
  whether a later call repeats earlier lines. Heard copes with repeats: a line already kept is
  not kept again.
- **Retries.** Nothing says whether Omi resends a webhook that fails or is slow. Heard keeps a
  resent conversation or line once, so a retry is harmless either way.
- Who makes the call, the phone or Omi's servers, and whether `http` is taken or only
  `https`. The testing tool refuses a private or loopback address, never follows a redirect
  and counts a response slower than 30 seconds as failed; that is the only hint.
- Whether a key in the address is acceptable to Omi. The pages are silent, so this guide
  asks you to treat the address as the key it holds.
- `uid`: the pages call it the user's id. Whether it ever names the pendant rather than
  the account is not stated; Heard treats it as an opaque id and only compares it.
- Any size limit on a message, and how large a long conversation gets. The sizes above are
  Heard's own estimate: 2,000 lines of ordinary speech come to about 400 KB.
- An `Idempotency-Key` header is mentioned for the test tool, with no format. Heard never sees
  headers, so it does not use it.
- That the Omi app's field names are the ones the pages use. If yours differ, paste the
  conversation address where the app asks for the webhook for a created memory or
  conversation, and the live one where it asks for real-time transcript.

**Not confirmed** is also the one thing nobody can check from here: that a real pendant's
messages reach Heard. Heard's tests post the shapes the pages show, with the fields and the
addresses the pages name.

## The screens

**Conversations.** Newest first, grouped by day: the title (or the first words), the time,
how long it ran, how many words and who spoke. "Still listening" marks a live conversation
the quiet gap has not closed yet. **Earlier** loads older ones.

**One conversation.** The lines with who said each and when; the recorder's own overview; a
summary; to-dos; and who spoke.

- **Summarise** asks a model on this computer to read the conversation and write a short
  summary (at most **Longest summary** words). The summary is kept; **Summarise again** or
  **Clear the summary** change it.
- **Pull out to-dos** asks for things the speakers committed to, each with the words it rests
  on. A suggestion whose words were not really said, or that shares fewer than half of its
  content words with the quote it gives, is thrown away. You **Accept**
  (optionally **Edit** first) or **Decline**. A declined one is not suggested again for that
  conversation. An accepted one is kept in the conversation's list; with **Also add accepted
  to-dos to my tasks** on, it is also added to your task list, with its title only (a
  task is part of your record, so that title goes there; on by default, since accepting is
  your own click).
- **Rename** next to a speaker changes that name on every line of this conversation.
- **Save as a file** gives you the conversation as Markdown.
- **Delete** asks first, then deletes for good.

A model takes as long as it takes, so the page asks every moment whether it has answered, and
you can carry on meanwhile. If a model cannot be used the page says so and nothing is sent
anywhere else.

**Search.** Every word you type must be in the line, in any order; the last word may be only
the start of a word. The matched words are marked. A result opens its conversation at that
line. Only what was said is searched. It uses SQLite's full-text index; on a computer
without it, lines are scanned one by one and the page says so.

**Settings.** The steps above, what has arrived and what was refused, how it is set, and
Your data.

## Your data

**Export everything** saves your conversations as JSON files (a very large set comes in
parts: save each). **Import** reads those files back: choose each file you saved. Importing
is safe to repeat: a conversation already kept gets only the lines it lacks. Imported text is
checked as strictly as text from the door. **Delete everything** removes every conversation
for good after you type DELETE.

## Settings worth knowing

Under **Settings, Apps, Heard**:

- **A conversation ends after this much quiet (minutes).** 5 by default.
- **Keep conversations for (days).** 0, the default, keeps everything. Above 0, conversations
  that ended (or that Heard last heard) longer ago than that are deleted for good, once a day.
- **Only accept this recorder id.** Empty by default: every message is kept, with its id. A
  value keeps only messages whose `?uid=` is exactly it.
- **Skip what the recorder marked as discarded.** On by default.
- **Let a model read my conversations.** On by default. Off, no model ever reads anything.
- **Also add accepted to-dos to my tasks.** On by default. Off, an accepted to-do stays in Heard only.
- **Keep the to-dos the recorder itself lists.** On by default. They are marked "from your
  recorder, not checked"; off, they are left out.
- **Most to-dos suggested at once.**
- Under advanced: **Clock slack for a recorder (minutes)**, **Conversations shown at a time**, **Most search results**, **Most
  characters a model reads** (a longer conversation is read only up to that point, and the page
  says so), **Longest summary**, **Shortest supporting quote**.

## Questions

**Why can my assistant not search Heard?** Heard is private. The platform does not let a
private app offer Memory a source or agents a tool, because that would put what you said
in front of other programs. Read-only tools exist for other apps (Posts, Reader, Articles),
but only for apps that are not private. Making Heard offer them would be a separate decision
about what you are willing to share, and it is not part of version 0.2.0.

**I sent Omi's webhooks through a relay before 0.2.0. What now?** Nothing breaks: the
`/transcript` address and the shapes it read are unchanged, so a relay keeps working. To drop
it, make a key for an address as in **Connect your recorder** and paste the new addresses
into the Omi app. Conversations already kept stay as they are; one sent again by both routes
is still kept once.

**Is the audio kept by the recorder's own app?** That is outside Heard. Heard only sees the
text the recorder sends.

**What is not in 0.2.0?** Naming speakers from their voice (there is no audio), merging or
splitting conversations by hand, a to-do list across all conversations, and offering
conversations to Memory or agents.
