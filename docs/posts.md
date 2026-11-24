# Posts

Posts turns an article you link, a topic you type, or pictures you upload into a short post
for one of your brands. It checks every number and quotation in the draft against the
source, and books the post only when you approve it.

## What to expect

Posts books through **Practice** (nothing is sent), **By hand** (a list for you to post
from), **SocialBu** or **Buffer**. SocialBu and Buffer are built from each service's
published documentation and are not yet proven against the real services. That includes
the list of your accounts that **Add an account** shows. Start in Practice, then try one real
post you can afford to get wrong. Buffer takes text only for now. If an account list does not
load, you can type the account's id in instead.

## Set it up

1. Install Posts from **Apps** (see [First steps](first-steps.md)). It lists the roles an
   agent could hold; you can leave them all empty.
2. Make sure a model is set up ([Models](models.md)) and that **Settings, Web lookup** is on,
   because Posts reads the article behind a link itself. For pictures you also need a model
   that can see images.
3. Open **Posts**. **Today** says to set up a brand. Go to **Brands** and press **Add a brand**.
4. Fill in the form, in this order:
   - **Name**.
   - **How it sounds**: one sentence.
   - **Writing samples**: a few posts the brand has published. They teach the voice; their
     words are not reused.
   - **Time zone**, and **Posting times**, such as `08:00, 17:30`.
   - **Book through**: leave it on Posts' setting, or choose one tool for this brand.
   - **This brand's accounts**: see below. Practice and By hand need none.
   - **Tags every post carries**, separated by spaces.
   - **Save**.
5. Optional: **Fill in from its posts** reads what the brand's accounts have published and
   proposes the sentence, samples and tags. It fills only what is empty, and nothing is
   kept until you press **Save**. It needs an account on a tool that can list what it
   published, so add the account first.
6. Optional: under **Your rules** on the Brands screen, add a phrase to flag, a tag every
   post must carry, or a question to ask on every draft.

## Where each outside service is connected

The default for every brand is **Settings, Apps, Posts, Book through (the default)**. A brand
can choose its own under **Brands**, **Book through**.

| Choice | What it does | What it needs |
|---|---|---|
| Practice (books nothing) | Records what it would send. Nothing leaves. | Nothing. |
| By hand | Lists each post with its time. You post it yourself, then press **Posted**. | Nothing. |
| SocialBu | Books on the accounts you list. | The **SocialBu key**, from SocialBu's settings under API for Developers. |
| Buffer (text only) | Books text on the channels you list. | The **Buffer key**, from Buffer's API settings page. |

Keys go on the same Posts settings page, under **Keys and tokens**. They are never shown
again.

**Add an account** (on a brand's form) opens a window. Choose a **Tool**, tick the accounts
this brand posts to, and press **Add selected**. If the tool cannot list them, or gives
none, the window says why, and **Type it in instead** adds a row where you type the
**Label**, the **Account id** as the scheduler shows it, and the **Scheduler**. Nothing is saved
until you press **Save** on the brand.

## The everyday loop

1. **Start something.** Under **New** choose **A link**, **A topic** or **Pictures**, pick the
   brand, add **Your notes**, tick **This post is sponsored** if it is, and press **Start**.
   Posts reads and checks the source first. It also checks that the brand has not covered it
   lately, and holds it with a card if it is too close.
2. **Or let Reader feed it.** With Reader installed, the articles it offers appear under
   **New** as **Ideas from Reader**, each with **Start** and **Dismiss**. See the
   [Reader guide](reader.md). Nothing is drafted until you press **Start**, unless you set
   **Articles Reader offers** to start by itself (this works only when you have exactly one
   active brand, and only up to **Pieces drafted a day**).
3. **Decide.** A finished draft arrives under **Drafts**, beside its source, and as a card
   on your Bench. Numbers and quotations that rest on a source sentence light it up. A check
   that could not find support is a question you answer **Yes**, **No** or **Fixed**.
   Choose **Approve**, **Edit**, **Send back** (with a note) or **Decline** (with a reason,
   which becomes a lesson for later drafts).
4. **Queue.** An approved post takes the brand's next free posting time and appears under
   **Queue**. With Practice or By hand it is booked at once. With SocialBu or Buffer it waits
   for you to press **Book**, which shows the time and lets you change it. Here you can also
   **Move**, **Edit**, **Posted by hand** or **Cancel**.
5. **Afterwards.** **Went out** and **Dropped** list what has gone and what was declined or
   dropped, each searchable.

## The Scheduled view

**Scheduled** lists everything SocialBu has scheduled on your account, whether Posts booked
it or you made it on SocialBu yourself. It is read only: nothing here books, moves or
cancels anything.

- Posts reads SocialBu when you open the view and when you press **Refresh**, never by
  itself on a timer. The last good list is kept, with the time it was read ("Read 5 minutes
  ago"), and is shown at once while the new read happens.
- Posts are grouped by day, soonest first, in your own time zone. Each shows its time, the
  account (its name and network, or the network and number when SocialBu gave no name), the
  first lines of its text with **Show all**, and how many pictures it has.
- A post Posts booked itself says **Booked by Posts**, with a button to open it in the Queue.
- If SocialBu cannot be reached, or answers in a way Posts does not understand, the view says
  so and keeps showing the last list. A list is never shown half read.
- Posts reads up to **Most batches of SocialBu's schedule to read** (10 by default, about
  200 posts). If SocialBu has more, the view says the list was cut short. Raise the setting
  under **Settings, Apps, Posts** to read further.
- It needs the **SocialBu key**. Without one the view says where to type it in. If Posts books
  through Buffer, the view says "This list is for SocialBu".

The text of each post comes from SocialBu and is shown as plain text only. It is never sent
to a model.

## What it will never do without you

- Book on SocialBu or Buffer unless you press **Approve** and then **Book**. (The one
  exception is if you have let Posts book alone on the permission ladder.)
- Try a booking again by itself. If the scheduler gives no clear answer, the post is marked
  **unsure** and stays so until you look there and say **I checked, it is not there**.
- Break the limits you set: posts a day for each brand, and booking at least a set time
  ahead.
- Send your notes, or a draft, to other apps. The feed Posts offers shows only a title, the
  source's address and the opening of what went out.

What leaves the computer: the post text, its time and its pictures go to `socialbu.com` or
`api.buffer.com`, and only when you book through them. The Scheduled view also asks
`socialbu.com` for its list, only when you open it or press **Refresh**, and sends nothing
but your key. Articles you link are read through
JimmyHQ's web lookup. The text goes to the model your roles use, which is local unless you
chose a hosted one.

## Settings worth knowing

Under **Settings, Apps, Posts**:

- **Book through (the default)**.
- **Posts a day for each brand**, and **Book at least this far ahead (minutes)**.
- **Articles Reader offers**, and **Pieces drafted a day**.
- **Revisions before you see it**: how many times a draft is revised after its check.
- **Sponsor tag**: added to a sponsored post when it is booked.
- **Model role that checks drafts**: choose a different model from the one that drafts.
- **Single-letter keys**: on the Drafts view `j` and `k` move, `a` approves, `e` edits, `s`
  sends back, `d` declines. Switch off if letters get in your way.

## Questions

**I installed one app and cannot see the others.** Each app is installed on its own. Open
**Apps**; the rest are under **Made by the JimmyHQ project**.

**I added Posts, where is Reader?** Reader is a separate app. Install it from the same box.
Posts then shows its articles under **New**.

**Why is there a number beside Posts?** It counts what waits for you: drafts to decide,
bookings with no answer, posts out of step with the scheduler, pieces that stopped and Reader's
ideas.

**Why does nothing get sent?** Check **Book through**. On Practice the Queue says "Practice:
nothing is really being booked", and on By hand it says to post each one yourself. With
SocialBu or Buffer, check that the post is booked and not just waiting in the Queue.

**Why does the form say "Brands" and not "Pages"?** A brand is who Posts writes for.

**Where do I add my SocialBu or Buffer key?** **Settings, Apps, Posts, Keys and tokens.**
