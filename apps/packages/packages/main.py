"""The Packages program JimmyHQ starts: ``python -m jimmyhq_app packages.main:app``."""

from packages.program import build

app = build()

if __name__ == "__main__":
    app.run()
