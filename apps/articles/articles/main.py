"""The Articles program JimmyHQ starts: ``python -m jimmyhq_app articles.main:app``."""

from articles.program import build

app = build()

if __name__ == "__main__":
    app.run()
