"""The Watering example: a job asks on the Bench, the owner's answer is kept, the page shows it.

It uses one of each plug a small full app needs:

- a job, on a schedule the owner sets (``ask_every_minutes``);
- a card type of its own (``round``) with its own buttons, updated rather than filed
  again while the last one is still waiting;
- the owner's decision, handed back and kept in the app's own database;
- a screen that asks the program for the log, and hears when it changes.
"""

import logging
import time

from jimmyhq_app import App

log = logging.getLogger("watering")
app = App()

BUTTONS = [
    {"id": "watered", "label": "Watered", "style": "primary", "closes": True},
    {"id": "skipped", "label": "Not this time", "closes": True},
]


def db():
    conn = app.db()
    conn.execute("CREATE TABLE IF NOT EXISTS round (card TEXT PRIMARY KEY, asked INTEGER, "
                 "answer TEXT, answered INTEGER)")  # times in unix milliseconds, UTC
    return conn


def now_ms() -> int:
    return int(time.time() * 1000)


@app.job("ask", every_setting="ask_every_minutes")
def ask():
    conn = db()
    waiting = conn.execute("SELECT card FROM round WHERE answer IS NULL").fetchone()
    if waiting:  # still on the Bench: say it again rather than file a second card
        app.bench.update(waiting[0], note="Still waiting for an answer.")
        return waiting[0]
    card = app.bench.file("round", "Were the plants watered?", actions=BUTTONS,
                          body="Press Watered once they have had their water.")
    conn.execute("INSERT OR IGNORE INTO round (card, asked) VALUES (?, ?)", (card, now_ms()))
    conn.commit()
    app.emit("rounds.changed")
    return card


@app.on_decision("round")
def decided(decision):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO round (card, asked) VALUES (?, ?)",
                 (decision.card_id, now_ms()))
    conn.execute("UPDATE round SET answer=?, answered=? WHERE card=?",
                 (decision.action, decision.ts, decision.card_id))
    conn.commit()
    log.info("round %s answered %s", decision.card_id, decision.action)
    app.emit("rounds.changed")


@app.call("rounds")
def rounds(args):
    rows = db().execute("SELECT card, answer, answered FROM round ORDER BY rowid DESC "
                        "LIMIT 50").fetchall()
    return [{"card": card, "answer": answer, "answered": answered}
            for card, answer, answered in rows]


if __name__ == "__main__":
    app.run()
