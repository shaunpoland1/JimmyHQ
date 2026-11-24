# Books

Books is your own bookkeeping: accounts, a register for each one with the balance after
every row, transfers between your accounts, bills and income reminders, debts with a
rough payoff order, reconciling against a statement, and spending by category.
Everything stays on this computer.

## What to expect

This is a first version. The register and reconcile are where the care went. Some things
are not there yet (see [Not in this version](#not-in-this-version)). Export everything
now and then: it is your backup.

## Set it up

1. Install Books from **Apps** (see [First steps](first-steps.md)). It has no roles and
   no keys.
2. Open **Books**. With no accounts yet it opens **Accounts** with the **Add an account**
   form:
   - **Name**, such as "Example Bank checking".
   - **Kind**: Checking, Savings, Credit card, Cash or Loan.
   - **Balance on that day** (for a card or a loan, **Owed on that day**, typed as a plain
     number) and **On**: a day you can check against a statement.
   - **Add account**.
3. To bring in what you have elsewhere, use **Categories and files**: a bank's CSV file
   into one account, or a whole Books file into an empty Books.

## The register

Each account's rows, newest first, with **Payment** and **Deposit** (on a card or a loan,
**Charge** and **Payment**), a cleared mark, and the **Balance** after each row. A balance
below zero is shown in red. Balances are always worked out afresh from the opening
balance, by date and then by the order rows were entered, so a row put in the past, a
change or a deletion moves every balance after it.

The top row enters a new one from the keyboard:

- **Tab** between the boxes, **Enter** saves, **Esc** clears. The date starts on the last
  date you used; **+** and **-** move it a day.
- Typing a payee you used before and choosing it fills in the category and the amount it
  had last time. You can change either.
- A category that is new is added to your list.
- Type **Transfer: ** and an account's name as the category (it is in the list) to move
  money between your accounts.
- **No.** is an optional check number (or any short reference, up to 20 characters). It is
  shown in the register, kept in the CSV file and the everything file, and **Search** finds it.

A click on a row (or **Enter** on it) opens it for changing: **Save**, **Cancel** or
**Delete** (which asks first). The small box in the **Clr** column clears a row with one
click and takes the mark off with another. **c** is cleared, **R** is reconciled.

**Search** finds rows by payee, memo, number or amount. Above the register: the balance **Today**,
the **Cleared** balance, and the balance after rows dated later when there are any. On a
phone the category and memo columns are left out.

## Transfers

**Transfer** above the register, or **Transfer: <account>** as the category, makes two
rows: out of one account, into the other. Changing the date or amount of one side changes
the other; deleting one deletes both. A transfer has no category and is never counted as
spending.

## Bills and income

Each reminder has a name, a payee, an amount (tick **The amount is about this much** when
it varies), an account, a category, whether it is a bill, income or a transfer, how often,
and the next due date. How often: every week, every two weeks (always the same weekday), twice a
month on two days, every month, every few months, every year, or once. A bill on the 31st
lands on the last day of a shorter month and goes back to the 31st; one on 29 February
lands on the 28th in other years.

Each reminder may also carry a **Note** (free text, up to 500 characters: an account number to
quote, a phone number to ring) and be marked **This happens by itself** (see
[Reminders that post themselves](#reminders-that-post-themselves)).

The list shows what is **Overdue**, **Due today**, due within **Show bills and income due
within (days)** (14), and **Later**. **Post** puts it in the register (you can change the
date and amount then) and moves it to its next date. **Skip** moves it without posting.
Pressing Post twice posts it once.

**Paid another way.** For a bill you pay in cash, or with a card Books does not keep,
choose **Paid another way** where the account goes. It has no account. **Post** then says
it is paid and moves it to its next date, and puts no row in any register, so no balance
changes. It is listed in Ahead once, in its own **Paid another way** group below the balances (it
belongs to no account, so it is not repeated under each one), with the date it is due, or was
due if it is overdue.

**A transfer between your accounts.** Choose **A transfer between my accounts**, then the
account the money leaves and the account it goes into. **Post** makes the two linked rows
a transfer always makes (you can change the date and amount first), and moves it on. Ahead
takes it out of one account's balance and puts it into the other's.

The number beside Books in the sidebar counts reminders due today or overdue.

### Reminders that post themselves

Tick **This happens by itself** on a reminder for a direct debit, a salary, a standing
transfer: something that goes through whether or not you press anything. Books then posts it
for you, in the same way pressing **Post** would: a row in the register (uncleared, dated
the day it was due, with the amount the reminder holds), the two linked rows of a transfer,
or, for one paid another way, just moving it on with no row. It is the scheduled look Books
makes every **Look for bills coming due every (minutes)** (60) that does it, so it happens
within that time of the due date, not at the stroke of midnight.

- **Exactly once for each due date.** Posting moves the reminder on in the same step, and if
  you press **Post** for that date first, Books finds it already done and does nothing.
- **Never ahead.** A date that has not come is never posted. A date in the past is posted
  with the date it was due.
- **Only a sure amount.** A reminder with **The amount is about this much** ticked is never
  posted by itself, because Books does not know what to post: the two boxes cancel each
  other, and a file that has both true is refused. Such a reminder still raises its card as
  before.
- **Catching up, then stopping.** If Books was not looking for a while (or you added a
  reminder with a due date weeks ago), it posts the missed dates one by one, up to **Most
  missed dates posted by themselves at once** (5), then stops. The reminder then says so
  where you see it on the Bills screen, raises its card like any other bill, and waits: the
  dates still overdue are yours to post or skip. Once none is on or before today, it posts
  by itself again.
- **When a date cannot be posted** (its account is closed, or the date is before the
  account's opening date), Books posts nothing for it, says why on the reminder, and tries
  again the next time it looks.
- **No card for one that is working.** A bill that posts itself puts nothing on your Bench.
- **It says so.** For **Show bills and income due within (days)** days after it posts,
  **Bills and income** lists it under **Posted by itself** and Ahead notes it, as "posted
  automatically". The register row is an ordinary one: change or delete it there if it was
  wrong.
- **One switch.** **Post reminders marked "This happens by itself"** (on at first), when off,
  makes every reminder wait for **Post** again.

Posting by itself writes nothing on your record, asks no model and uses no network, like the
rest of Books.

## Debts

**Debts** lists what you owe. Add a debt with its **Name**, **What you owe now**, **Rate
(percent a year)**, **Minimum payment each month**, a **Kind** (credit card, loan, mortgage, home equity line of credit, or other), an optional date
to **Pay it off by**, an optional **Linked account** (a card or a loan you keep in Books) and
a note.

- **Owed in total** adds the latest balance of every open debt. A debt whose balance you
  have not told Books is shown as **not known**, never as zero, and the total says it is
  not counting it. Zero is something you type: it means paid off.
- **Update balance** adds a new balance with its date. The old ones stay: **History**
  lists them, newest first. Two balances on one day keep the later one. A balance cannot
  be dated after today. With a linked credit card or loan, **Take it from** the account
  uses what that account owes today.
- **Kinds.** When your open debts are of more than one kind, the list is grouped by kind, each
  group with its own heading and what is owed in it; with one kind there is just the list.
- **A target date that has passed** is allowed, but said when you save it ("The date to pay
  it off by ... has passed"), and the page says so as you pick it.
- **Closed** (under **Change**) leaves a debt out of the total and the order and keeps its
  history. **Delete** removes the debt and its history for good (it asks first).

**Payoff order** shows roughly when each debt would be paid off. Every month each debt
grows by its interest (its yearly rate over twelve) and is paid its minimum; the **Extra
each month for debts** setting, and the minimum of any debt already paid off, go to the
first debt that is left. **Pay debts off in this order** chooses which is first: the
smallest balance, or the highest rate. A debt needs a balance above zero and a rate to be in
the order. If a debt's payments do not cover its interest it says so instead of giving a
date. A debt with a target date is marked **on track** or **behind**. The first payment is
taken as next month, and the figures are rough arithmetic, not advice.

## Ahead

One account's balance day by day from today, for **Look ahead (days)** (45): today's
balance, plus rows dated later, plus every reminder due for that account. The lowest point
and its day are called out (for a card or a loan, the most owed). An overdue reminder counts
today.

## Reconcile

Choose the account, type the statement's end date and ending balance (for a card, what it
says you owe), and press **Start**. Books lists every row up to that date not reconciled
yet. Tick each one on the statement; **Cleared balance**, **Statement** and **Difference**
change as you go. **Finish** works only when the difference is exactly zero: the ticked rows
become reconciled (**R**) and the reconciliation is kept under **Past reconciliations**.

Changing a reconciled row's amount, date or account, deleting it, or taking its mark off
asks first, because that reconciliation will no longer balance.

## Spending

By category for a month, with the month before beside it and a total. Income is shown
apart. **Uncategorised** shows whenever it is not zero: money out with no category counts
as spending, money in with none as income. A click on a category shows its rows. Transfers
are never counted.

Categories are under **Categories and files**: add one (tick **Income** for one that is
money in), rename one, or merge one into another (every row and reminder moves). Books
starts with a plain set.

## Files

**Bring in a bank's CSV file**: choose the account and the file. Books guesses which column
is which from the header (a check number column is found too); change any of them. The amount can be one column (money in above
zero; tick **Purchases are written above zero** for a card's file that writes them the
other way) or two columns for money out and money in. Dates in 2026-03-04, 3/4/2026 or
4/3/2026 form are detected; when every date reads both ways, choose under **Dates**.
Nothing is saved until you press **Import**, and the first rows are shown first. A row
already in the account (same date, amount and payee, however the payee is capitalised) is
skipped and counted; two real same-day rows both arrive the first time. New rows are marked
cleared unless you untick that. Your choice of columns is remembered for that account. An
import can be undone as a whole under **Past imports**.

A check number longer than 20 characters is cut to 20, and Books says how many it cut: in
the preview, and in the sentence after the import.

**Save ... as CSV** saves one account's register, or every account's, for a spreadsheet:
amounts are written plainly (-1234.50), and the last column, **Number**, is the check number.
Every cell of text (account, payee, category, memo, number) that starts with `=`, `+`, `-`, `@`,
a tab or a line break is written with an apostrophe in front, which a spreadsheet takes as "this
is text" and does not show, so a payee or memo from a bank cannot run as a formula where you open
the file. Amounts are never touched. Books takes that apostrophe off again when you bring its own
file back in, and only where it put one, so exporting and importing again changes nothing.

**Export everything** saves one JSON file with everything. **Import everything** brings
such a file into an empty Books, whole or not at all; an amount larger than the one you may type
in (a hundred billion) refuses the whole file, with a sentence that names it. **Erase everything** deletes every
record in Books for good (it asks first).

A file is sent to Books in pieces, so a long history (10,000 rows is about 2 MB tightly
packed, and a little under 3 MB as Books writes it) comes in without waiting on one long call.
The largest file Books takes, for a CSV or an everything file, is **Largest file to bring
in (MB)** under advanced settings (8).

**Export everything** is always one JSON file, however long the history. A file an app hands
you is limited by your own setting, **Largest file an app may offer you to keep (MB)** under
**Settings, Apps** (advanced, 8 MB at first). Books does not know that number, so it offers
the whole file and listens: if the platform says it is too large, Books tries once more with
the JSON packed onto one line (no line breaks, same content), and only if that is still
too large does it say so in a sentence that names the setting, and suggests raising it or
saving each account as CSV. Nothing is split across files and nothing is left out.

### The JSON shape

Version 2 is the file Books writes. A version 1 file (from Books 0.1) still comes in: it is
read the same way, and every field below that version 1 did not have is taken at its default
(`number` `""`, `to_account` `null`, `paid_other` `false`, `debts` empty). A version other than
1 or 2 is refused.

Books 0.3 added three optional fields to version 2 without changing its number: a reminder's
`auto` and `note`, and a debt's `kind`. Every field marked optional below may be left out or
`null` and is then read at its default, so a version 2 file made before 0.3 comes in as it
always did, with `auto` `false`, `note` `""` and `kind` `"other"`; and a field this Books
does not know is ignored, never an error. (So a file written by 0.3 and brought into an older
Books still comes in, but the older Books drops those three fields.) The whole file is checked before anything is saved; one thing wrong and
nothing is brought in. Import fills an empty Books only.

```json
{
  "format": "jimmyhq-books", "version": 2, "exported_ms": 1800000000000,
  "accounts": [{"id": 1, "name": "Example Bank", "kind": "checking",
                "opening_cents": 100000, "opening_date": "2027-01-01", "closed": false},
               {"id": 2, "name": "Example Savings", "kind": "savings",
                "opening_cents": 0, "opening_date": "2027-01-01", "closed": false}],
  "categories": [{"id": 1, "name": "Groceries", "income": false}],
  "transactions": [{"id": 1, "account": 1, "date": "2027-01-03", "payee": "Corner Shop",
                    "category": 1, "memo": "", "number": "1042", "amount_cents": -1250,
                    "state": "", "transfer": null}],
  "reminders": [
    {"id": 1, "name": "Water bill", "payee": "", "amount_cents": 4500, "about": true,
     "account": 1, "to_account": null, "paid_other": false, "auto": false, "category": null, "kind": "bill",
     "every": "monthly", "day": 31, "day2": 15, "n": 1, "next_due": "2027-01-31",
     "note": ""},
    {"id": 2, "name": "Cash rent", "payee": "", "amount_cents": 30000, "about": false,
     "account": null, "to_account": null, "paid_other": true, "auto": false,
     "category": null, "kind": "bill", "every": "monthly", "day": 20, "day2": 15, "n": 1,
     "next_due": "2027-01-20", "note": "Pay at the office"},
    {"id": 3, "name": "Save for tax", "payee": "", "amount_cents": 25000, "about": false,
     "account": 1, "to_account": 2, "paid_other": false, "auto": true,
     "category": null, "kind": "transfer", "every": "monthly", "day": 20, "day2": 15,
     "n": 1, "next_due": "2027-01-20", "note": ""}],
  "debts": [{"id": 1, "name": "Example Card", "kind": "card", "rate_percent": 19.99,
             "min_payment_cents": 2500, "target_date": "2028-01-01", "account": null,
             "note": "", "closed": false,
             "balance_history": [{"on": "2027-01-01", "balance_cents": 40000},
                                 {"on": "2027-01-10", "balance_cents": 38000}]}],
  "reconciliations": [{"account": 1, "through": "2026-12-31", "statement_cents": 100000,
                       "rows": 12, "done_ms": 1800000000000}]
}
```

**The whole file.** A JSON object. `format` is the string `"jimmyhq-books"`. `version` is the
whole number `1` or `2`. `exported_ms` is when it was made, unix milliseconds UTC (any
number, not checked on the way in). `accounts`, `categories`, `transactions`, `reminders`,
`debts` and `reconciliations` are lists of objects; a list left out is empty. An account, a
category and a transaction each has an `id`, a whole number unique within its list, and
other items name it by that number; the numbers need not match anything in Books, they only
link items inside this file. A reminder's and a debt's `id` is not read.

**Kinds of value.** *Money* is a whole number of cents (JSON integer, never a string or a
decimal). *Date* is a string `YYYY-MM-DD` that is a real calendar day. *Time* is unix
milliseconds UTC, a whole number. *Text* is a string; `null` is read as `""` for text, and
leading and trailing spaces are removed. *Flag* is `true` or `false`; where it is optional
a missing value or `null` is `false`, and any other value is refused.

**accounts**

| Field | Type | Rules |
|---|---|---|
| `id` | whole number | required |
| `name` | text | required, at most 60 characters, unique ignoring case |
| `kind` | text | one of `checking`, `savings`, `credit_card`, `cash`, `loan` |
| `opening_cents` | money | required. For a `credit_card` or `loan` a balance owed is below zero (`-25000` is 250.00 owed) |
| `opening_date` | date | required. No row may be dated before it when entered in Books |
| `closed` | flag | optional, default `false` |

**categories**: `id` (whole number); `name` (text, required, at most 60, unique ignoring
case); `income` (flag, optional, `true` for money in). A file with no categories has none:
Books's starter set is not added.

**transactions**: one register row.

| Field | Type | Rules |
|---|---|---|
| `id` | whole number | required |
| `account` | whole number | required, an `id` in `accounts` |
| `date` | date | required |
| `payee` | text | at most 200 characters, default `""` |
| `category` | whole number or `null` | an `id` in `categories`; `null` for none. Books writes `null` on a transfer's rows; one given there is never shown or counted |
| `memo` | text | at most 500 characters, default `""` |
| `number` | text | at most 20 characters, default `""`. A check number, or any short reference. New in version 2 |
| `amount_cents` | money | required. Above zero is money in, below zero is money out (on a card or loan, a charge is below zero) |
| `state` | text | `""` (not cleared), `"c"` (cleared) or `"R"` (reconciled); default `""` |
| `transfer` | whole number or `null` | for a transfer, the `id` of its other row; that row must name this one back, be in a different account and have the opposite `amount_cents`, or the file is refused. `null` for any other row |

**reminders**: bills, income and transfers that come round.

| Field | Type | Rules |
|---|---|---|
| `id` | any | not read; may be left out |
| `name` | text | required, at most 80 characters |
| `payee` | text | at most 200 characters, default `""` (the name is used when it posts) |
| `amount_cents` | money | the size of it, read without its sign (`kind` says the direction) |
| `about` | flag | `true` when the amount varies; default `false` |
| `account` | whole number or `null` | an `id` in `accounts`. For a bill or income it is `null` exactly when `paid_other` is `true`. For a transfer it is the account the money **leaves** and is required |
| `to_account` | whole number or `null` | for `kind` `transfer` only: the account the money goes **into**, an `id` in `accounts`, different from `account`. `null` for any other kind (a value there refuses the file). New in version 2 |
| `auto` | flag | `true` when it "happens by itself": Books posts it on or after each due date (see [Reminders that post themselves](#reminders-that-post-themselves)). Optional, default `false`. Must be `false` when `about` is `true`, or the file is refused. New in Books 0.3 (still version 2) |
| `paid_other` | flag | `true` for "paid another way" (cash, a card Books does not keep): `account` must then be `null`. Posting it moves it on and adds no row. Must be `false` for a transfer. Default `false`. New in version 2 |
| `category` | whole number or `null` | an `id` in `categories`; must be `null` for a transfer |
| `kind` | text | `bill` (money out), `income` (money in) or `transfer` (version 2) |
| `every` | text | `weekly`, `biweekly`, `twice_monthly`, `monthly`, `months`, `yearly` or `once` |
| `day` | whole number | the day of the month it belongs on, 1 to 31, used by `monthly`, `months`, `yearly` and `twice_monthly` (where it is the first of its two days). Books writes the day of `next_due` for the others, which ignore it. May be left out: 1 |
| `day2` | whole number | for `twice_monthly` only, the second day, 1 to 31 and not equal to `day`. May be left out: 15 |
| `n` | whole number | for `months` only, the number of months, 1 to 120. May be left out: 1 |
| `next_due` | date or `null` | the next date it is due; `null` for one that came once and is done |
| `note` | text | free text, at most 500 characters. Optional, default `""`. New in Books 0.3 (still version 2) |

**debts** (new in version 2)

| Field | Type | Rules |
|---|---|---|
| `id` | any | not read; may be left out |
| `name` | text | required, at most 80 characters, unique ignoring case |
| `kind` | text | `card`, `loan`, `mortgage`, `heloc` or `other`. Optional, default `other`; anything else refuses the file. New in Books 0.3 (still version 2) |
| `rate_percent` | number or `null` | the yearly rate as a percent, for example `19.99` (19.99%); from 0 to 100 with at most two decimals; `null` when not known. A JSON number, not a string |
| `min_payment_cents` | money | the minimum payment each month, 0 or more; default `0` |
| `target_date` | date or `null` | the date the owner wants it paid off by; default `null` |
| `account` | whole number or `null` | an `id` in `accounts` to link the debt to (usually a `credit_card` or `loan`); default `null` |
| `note` | text | at most 500 characters, default `""` |
| `closed` | flag | `true` for a debt that is no longer being paid off; default `false` |
| `balance_history` | list | the dated balances: objects `{"on": date, "balance_cents": money}` where `balance_cents` is what is **owed**, 0 or more (zero is paid off). At most one per date, in any order. The latest `on` is the balance now. An empty list or a missing field means the balance is **not known**, which is not the same as zero |

**reconciliations**: each is `account` (whole number, an `id` in `accounts`), `through`
(date), `statement_cents` (money), `rows` (whole number, how many rows it covered; default
0) and `done_ms` (time; default the time of the import).

- `day`, `day2` and `n` are checked together with `every`: `twice_monthly` needs two
  different days from 1 to 31; `months` needs `n` from 1 to 120. A bill on day 31 lands on
  the last day of a shorter month and goes back to the 31st.
- A debt's balance dates are not checked against today when a file is brought in.
- A reminder's `auto` is read on import like any other flag: a reminder that is `auto`, not
  paid another way or not, with a `next_due` on or before today, is posted by Books on its
  next look (up to the catch-up limit), exactly as if it had been in Books all along. What
  Books posted by itself is not in the file (a note of it is kept for a few weeks only).
- Export writes every field above in every item (`null`, `""`, `false` or `[]` where there
  is nothing), with the debts' `balance_history` oldest first.
- Whole numbers must be JSON integers and flags must be `true` or `false`: `"12"` for a
  number and `1` for a flag are refused.
- Ids and counts are not kept: Books numbers things afresh when it brings a file in. A
  Bench card is never in the file.

## What it will never do

- Write anything on your record (the Tape). Books is a private app.
- Ask any AI model, or use the network.
- Put an amount, an account, a payee or a category on a Bench card. With **Put a card on
  your Bench when a bill is coming due** on (it is, at first), a card says the bill's name
  and its due date, **Days before a bill is due to tell you** (3) before it. Posting or
  skipping the bill takes its card away. Income gets no card, and nor does a bill that
  posts itself while it is working (one that was stopped does). Keep amounts out of a bill's
  name if you do not want them on the Bench. The card is its title only (Books is a private
  app, so it adds no text of its own to your record); the Bench's **Open Books** button takes
  you to the app, where you post or skip the bill.

A deleted row, account or reminder is overwritten in Books' own file, not only unlinked.

## Settings worth knowing

Under **Settings, Apps, Books**:

- **How you write amounts**: 1,234.50 or 1.234,50. Books reads and shows every amount that
  way (the rate and the extra for debts too).
- **Show bills and income due within (days)** and **Look ahead (days)**.
- **Put a card on your Bench when a bill is coming due** and **Days before a bill is due to
  tell you**.
- **Post reminders marked "This happens by itself"** (on) and **Most missed dates posted by
  themselves at once** (5).
- **Pay debts off in this order** (smallest balance first, or highest rate first) and
  **Extra each month for debts** (an amount, written the way you write amounts).
- **Largest file to bring in (MB)** (advanced), 8.

## Not in this version

- Interest worked out on a loan account itself. (A debt's payoff arithmetic is in
  **Debts**.)
- Split transactions (one row in several categories).
- Investments, and more than one currency.
- Summaries written by a model, and category suggestions by a model.

## Questions

**Why is there a number beside Books?** It counts bills and income due today or overdue.

**Why does a card show a positive balance?** For a card or a loan Books shows what you owe
as a plain number, marked "owed". A charge makes it larger; a payment to it, smaller.

**My reconcile will not balance.** Check the end date: rows after it are not listed. A row
missing from Books is added in the register; the reconcile picks it up when you start again.
