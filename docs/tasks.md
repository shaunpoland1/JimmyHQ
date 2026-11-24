# Tasks

One list of work for the owner and their agents (an agent's side of it is in
[village](village.md)). The code is
`server/jimmyhq/tasks/`; the tests are `tests/test_tasks*.py`.

| File | Holds |
|---|---|
| `kinds.py` | The event kinds, `task.*` |
| `settings.py` | The settings, section "Tasks" |
| `recur.py` | Repeat rules: checking an RRULE, finding the next round, saying it in words |
| `lens.py` | The fold: open tasks, their one manual order, a capped tail of finished ones |
| `service.py` | `TaskService`: every rule. Other modules call this |
| `routes.py` | `/api/tasks`: thin wrappers over the service |

## Using it from another module

```python
from jimmyhq.tasks import TaskError, service

tasks = service(core)
task = tasks.create("Put the bins out", actor="agent:scout", due="2026-01-08",
                    repeat="FREQ=WEEKLY;BYDAY=TH")
tasks.complete(task["id"], actor="agent:scout")
tasks.list_today()
```

Every method returns the same task dictionary the API returns, and raises
`TaskError(status, detail)` with a sentence fit to show or to hand back to an agent.

## How it works

- **Events.** `task.filed`, `task.refiled` (an edit; carries every value), `task.completed`,
  `task.reopened`, `task.kept`, `task.unkept`, `task.pushed`, `task.unpushed`,
  `task.dropped`, `task.restored`, `task.ranked`. Each has `subject = "task:<id>"`.
- **A repeating task is one template.** Rounds are computed from the rule and the date
  the rule was anchored to (the due date given when the rule or the date was last set).
  Keeping a round prints `task.kept` with the next due date already worked out: the first
  round strictly after both the round kept and today. The fold refuses a `task.kept`
  whose next date is not later than both, so a round kept today can never be due again
  today. Rounds missed while a task lay lapsed are skipped, not queued.
- **Undo.** The lens remembers the last ten rounds of each task, with the due date and
  push count to put back.
  A done task is unkept only if a round is what ended it; a one-off that once
  repeated is reopened instead.
- **Pushes** are counted since the task was last done or kept. `tasks.stalled_after`
  pushes make it stalled.
- **Taking a push back.** `unpush(task_id, actor=...)` (`POST /api/tasks/{id}/unpush`, no
  body) prints `task.unpushed {task_id, was, to}`: the due date goes back to where the
  latest push moved it from and the push count drops by one. The rule's anchor is not
  touched, so a repeating task's COUNT and INTERVAL carry on as before; this is what the
  screen's Undo uses, never an edit of the date. The lens remembers where the last ten
  pushes came from (`pushed_from`), cleared whenever the count is reset and when an edit
  changes the due date or the rule. A task says whether it has one to take back with
  `can_unpush`; with none the answer is 409 and a sentence saying why.
- **Order.** `order` in the lens is the one manual order. New, reopened and restored tasks
  join the end. Every view filters this list and never re-sorts it.
- **Today** is the owner's calendar date (`owner.timezone`). All date arithmetic is on
  whole dates, so clock changes cannot move a round.
- **Days are the server's to say.** Every task carries `done_on`, the owner's calendar
  day it was done on (null while it is not done), and the board carries `date` (today)
  and `done_days` (how many days of finished and dropped tasks it covers, the
  `tasks.done_days` setting). A screen compares `done_on` with `date` and never works a
  day out from `done_at`: when `owner.timezone` is empty the board's `timezone` is
  `"local"`, meaning the server computer's zone, which a browser cannot know.
- **Two processes.** The service checks against the lens and then prints, so two
  processes can both print for the same task. Events carry what they expected
  (`kept_for`, `was`) and the fold ignores one that no longer fits. An edit
  (`task.refiled`) carries the due date it was made against as `was`; if a round was kept
  or the task pushed in between, the edit's other values are taken and the schedule (due
  date and rule) stays as it stands, so a kept round cannot come due again. After printing,
  the service reads the lens again, so the answer shows the task as it really is even when
  the change lost the race.
- **Bounded state.** The lens keeps the newest 500 finished tasks. An older one is still
  read, reopened or restored: the service rebuilds it from its own events
  (`Tape.by_subject`) and the event that revives it carries that snapshot back into the lens.

## Limits worth knowing

- A rule may use FREQ (DAILY, WEEKLY, MONTHLY, YEARLY), INTERVAL, COUNT, UNTIL, BYDAY,
  BYMONTHDAY, BYMONTH, BYSETPOS, BYYEARDAY, BYWEEKNO and WKST. Anything that could repeat
  within a day is refused, and so is a rule with both COUNT and UNTIL.
- A rule that can never land on a day (daily, in February, on the 30th) is refused, but
  only after dateutil has searched to its last year, which takes about two seconds.
- Changing a task's rule or due date re-anchors the rule, so a COUNT starts again.
- `done_at` and the other timestamps are the Tape's clock. The service's `clock` is
  replaceable for tests and only decides what "today" is.
