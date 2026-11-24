# Profiles

A profile is a small JSON file of preferred settings that anyone can apply. These
three show the idea; copy one and change it, or make your own.

| File | For |
| --- | --- |
| `private-by-default.json` | Keeping conversations and memory on your own machines |
| `quiet.json` | Fewer nudges from agents and less on screen |
| `hands-off-agents.json` | Letting agents take on more without asking |

## Use one

In the app, open Settings, choose **Import…**, pick the file and read the preview. Nothing
changes until you press Apply, and the confirmation has an Undo.

From a terminal:

```bash
uv run jimmyhq settings import profiles/quiet.json --dry-run   # see what would change
uv run jimmyhq settings import profiles/quiet.json             # apply it
```

A profile only changes the settings it lists. Each change is recorded on the Tape as
yours, like any other change, and any setting can be put back with **Reset** or
`jimmyhq settings reset <key>`.

## Make and share one

1. Set JimmyHQ up the way you like it.
2. Export it: **Export, as a shareable profile** in Settings, or
   `uv run jimmyhq settings export --profile --changed --name "My profile" -o my-profile.json`.
   `--changed` keeps only what differs from the defaults, which makes a short, readable file.
3. Open the file and give it a `name` and a one-sentence `description`: they are shown to
   whoever imports it.
4. Share the file.

A profile leaves out what is personal or belongs to one computer: your name, time zone,
server names, speech server and its address, and folders. It never holds passwords,
keys or tokens, which are kept apart from settings. Read a profile before you apply it:
the preview shows every change, and anything the file names that this install does not
know is listed as skipped.

## The format

```json
{
  "jimmyhq_settings": 1,
  "name": "Short name",
  "description": "One sentence about who it is for.",
  "settings": {
    "chat.local_only": true,
    "tasks.stalled_after": 5
  }
}
```

`name` and `description` are optional. Use a setting's key exactly as the Settings screen
and `jimmyhq settings list` show it. A value of `null` puts that setting back to its default.
