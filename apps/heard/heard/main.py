"""The Heard program JimmyHQ starts: ``python -m jimmyhq_app heard.main:app``."""

from heard.program import build

app = build()

if __name__ == "__main__":
    app.run()
