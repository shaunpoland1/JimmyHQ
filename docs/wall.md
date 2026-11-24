# The Wall

The Wall is a full-screen status board at `/wall`, made to be left open on a small second
screen and read from across the room. It has no sidebar and no dock, never scrolls, and
needs no pointer to read. The web code is `web/src/modules/wall/`; the server side is only
its settings, in `server/jimmyhq/wall/settings.py`.

## Tiles

| Id | Shows |
|---|---|
| `clock` | The time and the date, in the browser's locale |
| `usage` | Model calls, tokens, estimated cost and what running locally saved, for the last 24 hours and all time; the Tape's size |
| `bench` | How many cards need you, by kind and by source, and how long the oldest has waited |
| `today` | Today's tasks, ticked or not, late ones marked, and "n of m done". Read-only |
| `models` | One line per enabled model provider: online, offline or not checked yet |
| `feed` | The newest Tape events as they happen: time, kind and actor |

The tiles fill the screen exactly. A wide, short panel gets one row of tiles; a squarer
screen gets two; an upright one stacks them. The type grows and shrinks with the screen. A
tile with more to say than fits shows what fits and "+n more".

## Settings

Under Settings, The Wall (section `wall`):

| Key | Default | Meaning |
|---|---|---|
| `wall.tiles` | all six, in the order above | Which tiles to show, in order. Remove one to hide it |
| `wall.clock_24h` | off | 14:05 rather than 2:05 PM, on the clock, the feed and task times |
| `wall.show_seconds` | off | The clock counts seconds |
| `wall.force_dark` | off | Keep the Wall dark whatever the app's theme is. Off follows `appearance.theme` |
| `wall.refresh_s` | 60 | Read everything again at least this often (10 to 3600) |
| `wall.pixel_shift` | off | Move the whole picture a few pixels now and then, against burn-in |
| `wall.pixel_shift_minutes` | 5 | How often it moves when the shift is on |
| `wall.feed_lines` | 8 | The most feed lines to show |
| `wall.task_lines` | 8 | The most tasks to show |

A change made anywhere shows on an open Wall within a moment: no reload is needed.

## How it stays current

- The Wall listens to the live feed. An event marks the tiles it touches stale and they are
  read again: a task event refreshes Today, a Bench event the Bench, a model call the usage
  and model tiles, a changed setting the settings.
- Behind that, everything is read again every `wall.refresh_s` seconds. This is also what
  moves Today on at midnight. Provider health is checked by the server when it
  is read, at most once per `models.health_interval_s`, so the Wall reads it that often.
- If the server restarts or the network drops, the Wall keeps showing what it last knew
  with a small "Reconnecting…" marker, and catches up by itself when the server is back.
- If the session has ended, the Wall shows the sign-in screen. Sign in and it returns. When
  the live feed drops, the Wall asks the server once straight away, so an ended session
  shows the sign-in screen within seconds instead of "Reconnecting…".

## Setting it up on a second screen

1. On the computer that drives the screen, open `http://<your server>/wall` in a browser
   and sign in. If the browser reaches JimmyHQ by a name other than the one it runs under,
   add that name to `access.allowed_hosts` first.
2. Move the window to the second screen and press `F` for fullscreen (`F` or `Escape`
   leaves it). Moving the pointer shows two buttons at the top right for a few seconds:
   Back to app, and Fullscreen. They can also be reached with the Tab key.
3. Stop the screen from sleeping in the operating system's display settings.

### Signing in, and how long it lasts

The browser must be signed in once. It then stays signed in for the number of days in
`access.session_days` (30 by default, 365 at most), counted from the moment of signing in;
using the Wall does not extend it. When the time is up the Wall shows the sign-in screen
until someone enters the password again. For a screen with no keyboard, raise
`access.session_days` before you sign in on it: the setting applies to sign-ins made after
it is changed. Signing out of that browser, or clearing its cookies, ends it early.

### A kiosk browser

A browser started in kiosk mode opens straight to the Wall with no window frame. With a
Chromium-based browser, for example:

```bash
chromium --kiosk --user-data-dir="$HOME/.wall-browser" "http://<your server>/wall"
```

- Keep `--user-data-dir` pointing at the same folder every time, and do not use a private
  or guest window: the sign-in is a cookie, and it has to survive a restart.
- Sign in once with a keyboard attached. After that the kiosk needs none until the session
  runs out.
- Start the browser when the computer starts, using whatever your system offers for that.
- In kiosk mode the page is already fullscreen, so `F` is not needed.

### Screens that are left on all day

Turn on `wall.pixel_shift` so the picture moves by up to three pixels every few minutes,
and consider `wall.force_dark`. Neither replaces the screen's own protection if it has any.
