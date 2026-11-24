"""The Reader program JimmyHQ starts: ``python -m jimmyhq_app reader.main:app``."""

from reader.program import build

app = build()

if __name__ == "__main__":
    app.run()
