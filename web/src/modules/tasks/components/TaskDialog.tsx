import { useId, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../../api";
import { Field } from "../../../components/Field";
import { Modal } from "../../../components/Modal";
import { useSettingsQuery } from "../../../hooks/useSettings";
import { useAgentNames } from "../../../lib/useAgentNames";
import { useToast } from "../../../hooks/useToast";
import { tasksApi } from "../api";
import type { Task, TaskBoard } from "../api";
import { formatDay } from "../lib/dates";
import { AREA_MAX, NOTES_MAX, RULE_MAX, TITLE_MAX, changesBody, createBody, formFor, hasChanges, validateForm } from "../lib/form";
import type { FormErrors, TaskForm } from "../lib/form";
import { assigneeLabel } from "../lib/group";
import { REPEAT_MODES, toggleDay, weekdayName, weekdaysFrom } from "../lib/repeat";
import type { RepeatChoice, RepeatMode } from "../lib/repeat";

type TaskDialogProps = {
  /** The task to edit, or null to add one. */
  task: Task | null;
  board: TaskBoard;
  /** Every assignee known so far: "owner", then agents. */
  assignees: readonly string[];
  onClose: () => void;
  /** Called with the task as the server stored it. */
  onSaved: (task: Task) => void;
};

/** The owner.week_starts setting as a day number (1 = Monday … 7 = Sunday). */
const WEEK_START_DAY: Record<string, number> = { monday: 1, saturday: 6, sunday: 7 };

/** The first day of the week where the reader lives (1 = Monday … 7 = Sunday). */
function localeFirstDay(): number {
  try {
    const locale = new Intl.Locale(navigator.language) as Intl.Locale & {
      getWeekInfo?: () => { firstDay?: number };
      weekInfo?: { firstDay?: number };
    };
    return locale.getWeekInfo?.().firstDay ?? locale.weekInfo?.firstDay ?? 1;
  } catch {
    return 1;
  }
}

function savedMessage(saved: Task, adding: boolean, askedDue: string, today: string): string {
  const parts = [adding ? `Added “${saved.title}”.` : `Saved “${saved.title}”.`];
  if (saved.repeat_text) parts.push(`Repeats ${saved.repeat_text}.`);
  if (saved.due && saved.due !== askedDue && saved.repeat) parts.push(`Next due ${formatDay(saved.due, today)}.`);
  return parts.join(" ");
}

/** Add or edit a task. Stays open, showing the server's sentence, when a save is refused. */
export function TaskDialog({ task, board, assignees, onClose, onSaved }: TaskDialogProps) {
  const toast = useToast();
  const [form, setForm] = useState<TaskForm>(() => formFor(task, { area: board.default_area, today: board.date }));
  const [errors, setErrors] = useState<FormErrors>({});
  const [refused, setRefused] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const names = useAgentNames();
  const baseId = useId();
  // The owner's week start (a setting shared with the Bench) wins over the
  // browser's guess, so "this week" means the same thing on every screen.
  const settings = useSettingsQuery();
  const weekStart = settings.data?.settings.find((setting) => setting.key === "owner.week_starts")?.value;
  const firstDay = typeof weekStart === "string" ? WEEK_START_DAY[weekStart] : undefined;
  const weekdays = useMemo(() => weekdaysFrom(firstDay ?? localeFirstDay()), [firstDay]);
  const people = useMemo(() => (assignees.includes(form.assignee) ? assignees : [...assignees, form.assignee]), [assignees, form.assignee]);

  const change = (patch: Partial<TaskForm>) => {
    setForm((current) => ({ ...current, ...patch }));
    setRefused(null);
  };
  const changeRepeat = (patch: Partial<RepeatChoice>) => change({ repeat: { ...form.repeat, ...patch } });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (saving) return;
    const problems = validateForm(form);
    setErrors(problems);
    if (Object.keys(problems).length > 0) return;
    setSaving(true);
    setRefused(null);
    try {
      let saved: Task;
      if (task) {
        const changes = changesBody(form, task);
        if (!hasChanges(changes)) {
          onClose();
          return;
        }
        saved = await tasksApi.edit(task.id, changes);
      } else {
        saved = await tasksApi.create(createBody(form));
      }
      toast.success(savedMessage(saved, !task, form.due, board.date));
      onSaved(saved);
      onClose();
    } catch (error) {
      setRefused(errorDetail(error));
    } finally {
      setSaving(false);
    }
  };

  const repeatId = `${baseId}-repeat`;
  const notesId = `${baseId}-notes`;
  const assigneeId = `${baseId}-assignee`;
  const areasId = `${baseId}-areas`;
  const repeating = form.repeat.mode !== "none";

  return (
    <Modal title={task ? "Edit task" : "New task"} onClose={onClose} widthClass="max-w-lg">
      <form onSubmit={(event) => void submit(event)} noValidate className="flex min-h-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto px-4 py-4">
          <Field
            label="Title"
            value={form.title}
            onChange={(event) => change({ title: event.target.value })}
            maxLength={TITLE_MAX}
            error={errors.title}
            autoComplete="off"
            data-autofocus
          />

          <div className="flex flex-col gap-1.5">
            <label htmlFor={notesId} className="text-xs font-medium text-text">
              Notes
            </label>
            <textarea
              id={notesId}
              className="input h-auto min-h-[4.5rem] resize-y py-2 leading-normal"
              rows={3}
              value={form.notes}
              maxLength={NOTES_MAX}
              onChange={(event) => change({ notes: event.target.value })}
              aria-invalid={errors.notes ? true : undefined}
            />
            {errors.notes ? (
              <p className="text-xs text-danger" role="alert">
                {errors.notes}
              </p>
            ) : null}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="Area"
              value={form.area}
              onChange={(event) => change({ area: event.target.value })}
              list={areasId}
              maxLength={AREA_MAX}
              placeholder="none"
              error={errors.area}
              autoComplete="off"
              autoCapitalize="none"
            />
            <datalist id={areasId}>
              {board.areas.map((area) => (
                <option key={area} value={area} />
              ))}
            </datalist>
            <div className="flex flex-col gap-1.5">
              <label htmlFor={assigneeId} className="text-xs font-medium text-text">
                Assigned to
              </label>
              <select id={assigneeId} className="input pr-8" value={form.assignee} onChange={(event) => change({ assignee: event.target.value })}>
                {people.map((person) => (
                  <option key={person} value={person}>
                    {assigneeLabel(person, names)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 min-[400px]:grid-cols-2">
            <Field
              label={repeating ? "Starting" : "Due"}
              type="date"
              value={form.due}
              onChange={(event) => change({ due: event.target.value })}
              error={errors.due}
              hint={repeating && !form.due ? "Empty starts from today." : undefined}
            />
            <Field label="Time (optional)" type="time" value={form.dueTime} onChange={(event) => change({ dueTime: event.target.value })} error={errors.dueTime} />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={repeatId} className="text-xs font-medium text-text">
              Repeat
            </label>
            <select id={repeatId} className="input pr-8" value={form.repeat.mode} onChange={(event) => changeRepeat({ mode: event.target.value as RepeatMode })}>
              {REPEAT_MODES.map((item) => (
                <option key={item.mode} value={item.mode}>
                  {item.label}
                </option>
              ))}
            </select>

            {form.repeat.mode === "weekly" ? (
              <div role="group" aria-label="Days of the week" className="flex flex-wrap gap-1 pt-0.5">
                {weekdays.map((day) => {
                  const on = form.repeat.days.includes(day);
                  return (
                    <button
                      key={day}
                      type="button"
                      aria-pressed={on}
                      aria-label={weekdayName(day, undefined, "long")}
                      onClick={() => changeRepeat({ days: toggleDay(form.repeat.days, day) })}
                      className={`h-7 min-w-[2.6rem] rounded-md border px-1.5 text-xs ${
                        on ? "border-accent bg-accent font-medium text-accent-fg" : "border-border bg-surface text-text-muted hover:bg-surface-2"
                      }`}
                    >
                      {weekdayName(day)}
                    </button>
                  );
                })}
              </div>
            ) : null}
            {form.repeat.mode === "weekly" && form.repeat.days.length === 0 ? (
              <p className="text-xs text-text-faint">No day chosen: it repeats on the weekday of its start date.</p>
            ) : null}

            {form.repeat.mode === "monthly" ? (
              <label className="flex items-center gap-2 pt-0.5 text-xs text-text-muted">
                On day
                <input
                  type="number"
                  className="input w-20"
                  min={1}
                  max={31}
                  step={1}
                  value={Number.isFinite(form.repeat.monthDay) ? form.repeat.monthDay : ""}
                  onChange={(event) => changeRepeat({ monthDay: event.target.value === "" ? Number.NaN : Number(event.target.value) })}
                  aria-label="Day of the month"
                />
                of the month
              </label>
            ) : null}

            {form.repeat.mode === "advanced" ? (
              <>
                <input
                  className="input font-mono"
                  value={form.repeat.raw}
                  maxLength={RULE_MAX}
                  onChange={(event) => changeRepeat({ raw: event.target.value })}
                  placeholder="FREQ=WEEKLY;BYDAY=TH"
                  aria-label="Repeat rule"
                  autoComplete="off"
                  autoCapitalize="characters"
                  spellCheck={false}
                />
                <p className="text-xs text-text-faint">A calendar repeat rule (RRULE), at most once a day.</p>
              </>
            ) : null}

            {errors.repeat ? (
              <p className="text-xs text-danger" role="alert">
                {errors.repeat}
              </p>
            ) : null}
            {task?.repeat_text ? <p className="text-xs text-text-faint">Now repeats {task.repeat_text}.</p> : null}
          </div>

          {refused ? (
            <p role="alert" className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
              {refused}
            </p>
          ) : null}
        </div>
        <div className="flex flex-none justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : task ? "Save" : "Add task"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

type PushDialogProps = { task: Task; min: string; initial: string; onClose: () => void; onPush: (to: string) => void };

/** Pick the date to push a task to. */
export function PushDialog({ task, min, initial, onClose, onPush }: PushDialogProps) {
  const [to, setTo] = useState(initial);
  const problem = !to ? "Choose a date." : to < min ? "Choose a later date." : undefined;
  const [shown, setShown] = useState(false);
  return (
    <Modal title="Push to a date" onClose={onClose} widthClass="max-w-sm">
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          if (problem) {
            setShown(true);
            return;
          }
          onPush(to);
          onClose();
        }}
      >
        <div className="flex flex-col gap-3 px-4 py-4">
          <p className="break-words text-sm text-text-muted">{task.title}</p>
          <Field label="New due date" type="date" min={min} value={to} onChange={(event) => setTo(event.target.value)} error={shown ? problem : undefined} data-autofocus />
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary">
            Push
          </button>
        </div>
      </form>
    </Modal>
  );
}
