# Articles

Articles researches a topic, writes a full article from numbered sources, checks every
number and claim against them, and publishes only when you approve. It can send the
finished article to your own WordPress site, always as a draft.

## What to expect

The WordPress connection follows WordPress's documented REST interface and is not yet
proven against a real site. Try it on a test site or with one throwaway article first, and
look at the draft it makes. **Practice** and **By hand** do not depend on it. Header images
are only placeholders for now: the one choice under **What makes header images** is a plain
picture made on your computer.

## Set it up

1. Install Articles from **Apps** (see [First steps](first-steps.md)). Its roles
   (researcher, writer, checker) are optional.
2. Set up a model ([Models](models.md)), and switch on **Settings, Web lookup** with a
   **Search provider**. Articles searches and reads pages through it.
3. Open **Articles**, then **Sites**, and press **Add a site**. The form, in order:
   - **Start from the website**: type a **Website address** and press **Fill in from the
     website**. Articles reads the home page and a few recent articles on that site (same
     host only) and proposes values for the empty fields. The address is not kept, and
     nothing is saved until you press **Save**.
   - **Name**, **Who it writes for**, **How it sounds**.
   - **Writing samples**: **Add a sample** for each.
   - **The shape an article usually takes**: **Length**, **Headings**, **How it ends**, in
     your own words. They are guidance only.
   - **Where it publishes**: **Practice**, **By hand** or **WordPress**.
   - **Time zone**, then **Save**.

## Where WordPress is connected

You give Articles your site's address itself, not on the Sites page:

1. In WordPress, make an application password under **Users, Profile, Application
   passwords** for a user who can write posts.
2. In JimmyHQ, open **Settings, Apps, Articles**. On the card **Your own addresses**, type your
   site's address (for example `https://blog.example.org`) and press **Add address**, then
   confirm. You can add up to three.
3. Beside that address, fill in **WordPress user name** and **WordPress application
   password**, and press **Save** on each. They are never shown again.
4. Back in Articles, **Sites**, edit the site, set **Where it publishes** to **WordPress** and
   choose the address under **WordPress site address**.

If you are already on the Sites form with no address yet, **Open Articles settings** opens
Settings over the page without losing what you typed. Add the address there, close Settings,
and press **Check again**.

## The everyday loop

1. Under **New**, choose the **Site**, type the **Topic**, and optionally an **Angle** and
   **Notes**, then **Start**. Your notes are not sources: a fact in them must still be cited
   from a real one.
2. Articles first compares the topic with the site's earlier articles. If it is close, it is
   held: **Write anyway**, or drop it. Nothing has been spent yet.
3. It searches, reads pages, and keeps the best sources, numbered S1, S2 and so on. With
   fewer than **Fewest good sources to write from** it stops, and offers **Retry**.
4. It writes an outline and the article, then runs its own checks on every number and
   quotation, then a review. The article comes to **Articles**, and to your Bench, with its
   sources beside it. Choose **Approve**, **Edit**, **Send back** (with a note) or **Decline**
   (with a reason).
5. Approved articles wait under **Publish**:
   - **Practice**: nothing is sent. Articles keeps what it would have sent.
   - **By hand**: **Copy as HTML**, **Copy as Markdown** or **Save as a file**, put it on your
     site, then press **Published**.
   - **WordPress**: **Publish to WordPress** asks first, then sends the article as a draft. You
     publish it on WordPress, then press **Published** here. An edit you make afterwards
     updates the draft only while it is still a draft there.

**Ideas from readers** is optional. Paste readers' replies, press **Keep these replies**, then
**Find ideas**. Each idea can be started for a site or dismissed. Replies are kept off your
record, and **Delete every reply** erases them. They go only to a model on your own computer
unless you turn on **Let a hosted model read readers' replies**.

## What it will never do without you

- Publish. Sending to WordPress waits for your **Publish** click, and it is always a draft.
- Send twice. If the site gives no answer, the article is marked unsure and nothing is sent
  again until you look and press **I checked, it is not there**.
- Use your notes as evidence, or write advice about health, the law or money as fact
  without asking you first.

What leaves the computer: searches go to your search provider and pages are read through
JimmyHQ's web lookup. The article text goes to the model for each role. With WordPress, the
title and text go only to the address you gave, using the user name and password you saved
for it.

## Settings worth knowing

Under **Settings, Apps, Articles**:

- **Articles started a day**.
- **Searches for one article** and **Fewest good sources to write from**.
- **Sources to prefer** and **Sources to treat as weak**: web hosts, one per line.
- **Model role that writes** and **Model role that reviews**: choose different models.
- **Repair passes before you see it**.
- **How a published article cites**.
- **Header images**: off by default.

## Questions

**Where do I add WordPress?** Under **Settings, Apps, Articles**, on the card **Your own
addresses**, then choose it on the site under **Sites**. See above.

**I installed one app and cannot see the others.** Install each from **Made by the JimmyHQ
project** on the Apps screen.

**Why is there a number beside Articles?** It counts articles to decide, held or stuck
ones, publishes with no answer, and approved or sent ones waiting for you.

**Why does nothing get published?** The site is probably on **Practice**. Change **Where it
publishes** under **Sites**. **Publish** also waits for your click.

**Why will it not save my site as WordPress?** It needs one of your own addresses first.
