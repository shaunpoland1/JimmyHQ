"""The Imagery program JimmyHQ starts: ``python -m jimmyhq_app imagery.main:app``."""

from imagery.program import build

app = build()

if __name__ == "__main__":
    app.run()
