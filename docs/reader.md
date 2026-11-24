# Reader

Reader looks for articles on topics you choose, says in a line why each was suggested, and
puts them where you want: a reading list, your Bench, your Memory, or other apps such as
Posts. It reads and suggests. It never posts or sends anything of yours anywhere.

## Set it up

1. Install Reader from **Apps** (see [First steps](first-steps.md)). It lists a
   **Researcher** role an agent could hold; you can leave it empty.
2. Switch on **Settings, Web lookup** (**Let models look things up on the web**) and choose
   a **Search provider**. Reader searches through it. With it off, Reader cannot look for
   anything, and says so.
3. Make sure a model is set up ([Models](models.md)). Reader uses one to rank what it finds.
4. Open **Reader**, then **Topics**. It says it has nothing to look for yet. Press **Add a
   topic** and fill in:
   - **Name**. On a new topic, **Suggest topics** offers ideas as chips; choose one to put it
     in the Name box. Nothing is saved until you press **Save**.
   - **Search phrasings**, one per line: what you would type into a search box. **Suggest
     phrasings** adds some. Reader uses each in turn.
   - **Who it is for**: **Me**, or **Others, described in a sentence**.
   - **What happens to an article found for it**: any of **Kept in the list**, **On your
     Bench to read**, **Mined into Memory**, **Offered to other apps**. A new topic starts with
     the choice in **Settings, Apps, Reader, What a new topic does with an article**.
   - **Let the researcher look for it, when an agent holds that role**.
   - **Save**.

## The everyday loop

1. About once an hour (**Look for articles every (minutes)**) Reader searches one phrasing of
   each topic and reads each new result itself. A page too short or unreadable is dropped,
   with a sentence saying so. It checks whether it has seen the story before, then a model
   picks the best by number and writes a line on why.
2. What it keeps waits in **Triage**, one at a time, with its reason, its source and an
   excerpt. Choose **Keep**, **Read later**, **Skip**, **Send on** or **Mine**. Your choices
   teach Reader what you like. **Articles** lists everything it suggested or passed over,
   with filters and a search.
3. If a topic puts articles on your Bench, a card appears with **Keep**, **Read later**,
   **Skip** and **Send on**, up to **Articles put on your Bench each day**. The rest wait in
   Triage.
4. **Send on**, or a topic set to **Offered to other apps**, puts the article under
   **Offered**. Only its address, title, topic and reason are offered, never its text. An app
   you have let read Reader's articles takes it from there and reads the article itself.
   **Take it back** removes it from the list, and an app that also reads "Offers taken
   back" is told.

## How Reader hands articles to Posts

Install both. Under Posts, **New** then shows each offered article as an idea with **Start** and
**Dismiss**. Posts then reads the article itself and checks any draft against it. See the
[Posts guide](posts.md). In the other direction, Reader reads what Posts has posted and marks
an article **close to a post** in **Articles**. Nothing is hidden because of it. The Apps screen,
under **Who reads what**, shows which apps read which.

## What it will never do without you

- Offer an article to another app, unless you press **Send on** or the topic says to.
- Mine an article into Memory. **Mine articles into Memory** is off until you switch it on,
  and Memory also asks you to switch on the source **Facts from articles**. Only
  sentences taken word for word, with the article as their source, are kept.
- Search more than **Searches a day** (30 by default).

What leaves the computer: your search phrasings go to the search provider you chose, and
pages are fetched from public web servers. Article text is not sent to other apps. Text of
articles you did not keep is deleted after **Delete an article's text after (days)**; its
address, title and reason stay, so it is not suggested again.

## Settings worth knowing

Under **Settings, Apps, Reader**:

- **Look for articles every (minutes)** and **Searches a day**.
- **Articles kept each time Reader looks**.
- **Articles put on your Bench each day**.
- **What a new topic does with an article**.
- **Mine articles into Memory**.
- **Delete an article's text after (days)**.
- **Single-letter keys in Triage**: `y` keep, `l` read later, `x` skip, `o` send on, `m` mine.

## Questions

**I installed one app and cannot see the others.** Each app is installed on its own, from
**Made by the JimmyHQ project** on the Apps screen.

**Why is there a number beside Reader?** It is how many articles are in Triage.

**Why is nothing showing up?** You need a topic, **Web lookup** on and a **Search provider**.
Reader looks on a schedule, so the first articles take up to an hour.

**Where do the articles go after I press Keep?** Into **Articles**, marked kept. Keep and
**Read later** are your votes; they do not send anything.

**Why is a topic marked quiet?** It found nothing for **Call a topic quiet after (days)**. Pause
it, or change its phrasings.
