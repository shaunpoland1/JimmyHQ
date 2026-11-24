# Packages

Packages keeps your parcels in one place: each one's status and last event. It checks them
for you on a schedule and puts a card on your Bench when one arrives or runs into trouble.

## What to expect

The **17TRACK** connection is built from 17TRACK's own documentation and is not yet proven
against the real service. Start in **Practice**, where nothing is looked up and a package you
add moves along by itself (waiting, on its way, out for delivery, delivered) over about
**Practice: minutes until a package is delivered** (15), ending delivered with its card on your
Bench. The page says it is Practice. A package whose status you set yourself is left alone. Then
try 17TRACK with one real parcel.

## Set it up

1. Install Packages from **Apps** (see [First steps](first-steps.md)). It has no roles.
2. Open **Packages**. With no packages yet it opens the **Add a package** form:
   - **Name**: something to tell it apart by, such as "Garden hose". This is what a Bench card
     says.
   - **Tracking number**.
   - **Carrier**: leave **Detect it** (with Practice it reads **Any (none chosen)**), choose one
     you saved, or choose **Another carrier code…** and type a **Carrier code**. **Save it as**
     gives that code a name for next time.
   - **Add package**.
3. To have parcels looked up, open **Settings, Apps, Packages**:
   - Set **Tracking service** to **17TRACK**. It starts on **Practice (nothing is looked up)**.
   - Under **Keys and tokens**, paste your **17TRACK security key** from your 17TRACK API
     account's Settings page, and press **Save**. It is never shown again.
   - If the key is missing, Packages shows a banner with **Open settings**.

## The everyday loop

- Packages checks every **Check every (hours)** (4 by default) and once a day at **Also check
  every day at** (06:00), so news is waiting in the morning. **Refresh now** checks at once,
  but waits at least **Least time between checks from Refresh now (minutes)** after the last
  check. With 17TRACK it asks the service; with Practice it moves along whatever is due.
- A package sits under **On the way** until it is delivered, then under **Delivered**. Problems
  and parcels out for delivery sort to the top. Each row shows the status, the last event with
  its time, the carrier, and when it was last checked.
- A check that gets no clear answer changes nothing and is tried again after a wait that
  grows with each failure.
- The Bench card says "<Name> was delivered", "<Name> has a problem" and so on, once for each
  change. **Tell me when a package** chooses which changes: never, delivered, delivered or a
  problem (the default), or any change. A package you mark delivered yourself has its card too.
- Each row has **Rename**, **Change carrier**, **Remove**, and a way to set status by hand:
  a status list with Practice, and **Mark delivered** with 17TRACK.

## What it will never do without you

- Look anything up on Practice. Only the tracking number and carrier code go to 17TRACK
  (`api.17track.net`), never your name for the parcel, and only with 17TRACK chosen.
- Check more often than your schedule, except when you press **Refresh now**.
- Put a tracking number on a Bench card.

**Remove** deletes the package from Packages for good and asks 17TRACK to stop tracking it.
A package you only want out of the way can stay in **Delivered**, where it is checked for
**Stop checking a delivered package after (days)** in case the carrier corrects itself.

## Settings worth knowing

Under **Settings, Apps, Packages**:

- **Tracking service**.
- **Check every (hours)** and **Also check every day at**.
- **Tell me when a package**.
- **Practice: minutes until a package is delivered**: how long Practice takes to deliver a package.
- **Least time between checks from Refresh now (minutes)**.
- **Stop checking a delivered package after (days)**.
- **Call a new package "not found" after (hours)**: carriers often do not know a number for a
  while.

## Questions

**Why is there a number beside Packages?** It counts parcels out for delivery or with a
problem.

**Why does nothing update?** On Practice nothing is looked up: each package moves along by
itself, and each row says "Practice: it moves along by itself" (or "Set by you" when you set
it). Choose 17TRACK and add the key to have real parcels looked up.

**Why is a new parcel "Waiting for the carrier"?** The carrier often does not know a label
yet. After **Call a new package "not found" after (hours)** it reads "Not found".

**I installed one app and cannot see the others.** Install each one from **Made by the
JimmyHQ project** on the Apps screen.

**What does 17TRACK's quota line mean?** Under 17TRACK, Packages shows how many places are
left. A number uses one place, once.
