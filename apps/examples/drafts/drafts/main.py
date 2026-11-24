"""The Drafts example: a job drafts a post, a Bench card asks, the decision is kept."""

from jimmyhq_app import App

app = App()


@app.job("draft", every_setting="draft_minutes")
def draft():
    text = app.model.ask("Draft a short post about this week's work.").text
    db = app.db()
    db.execute("CREATE TABLE IF NOT EXISTS post (id INTEGER PRIMARY KEY, body TEXT, state TEXT)")
    post = db.execute("INSERT INTO post (body, state) VALUES (?, 'waiting')", (text,)).lastrowid
    db.commit()
    app.bench.file("draft", "Draft ready", body=text, subject=f"post:{post}")


@app.on_decision("draft")
def decided(d):
    state = "queued" if d.action == "approve" else "dropped"
    db = app.db()
    db.execute("UPDATE post SET state=? WHERE id=?", (state, d.subject.split(":")[1]))
    db.commit()


@app.call("board")
def board(args):
    return [list(row) for row in app.db().execute("SELECT id, body, state FROM post")]


app.run()
