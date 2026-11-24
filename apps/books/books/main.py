"""The Books program JimmyHQ starts: ``python -m jimmyhq_app books.main:app``."""

from books.program import build

app = build()

if __name__ == "__main__":
    app.run()
