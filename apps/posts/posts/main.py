"""The Posts program JimmyHQ starts: ``python -m jimmyhq_app posts.main:app``."""

from posts.program import build

app = build()

if __name__ == "__main__":
    app.run()
