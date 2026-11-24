// The add/edit dialog's form: its state, validation and the request bodies.

import type { NewTask, Task, TaskChanges } from "../api.ts";
import { dayToUtc } from "./dates.ts";
import { choiceFor, emptyRepeat, ruleFor } from "./repeat.ts";
import type { RepeatChoice } from "./repeat.ts";

export const TITLE_MAX = 300;
export const NOTES_MAX = 10_000;
export const AREA_MAX = 40;
export const RULE_MAX = 200;

export type TaskForm = {
  title: string;
  notes: string;
  area: string;
  /** "YYYY-MM-DD" or "". */
  due: string;
  /** "HH:MM" or "". */
  dueTime: string;
  repeat: RepeatChoice;
  assignee: string;
};

export type FormErrors = Partial<Record<"title" | "notes" | "area" | "due" | "dueTime" | "repeat", string>>;

function monthDayOf(day: string | null, fallback: string): number {
  const source = day && dayToUtc(day) !== null ? day : fallback;
  const value = Number(source.slice(8, 10));
  return value >= 1 && value <= 31 ? value : 1;
}

/** The form for a new task (`task` null) or for editing one. */
export function formFor(task: Task | null, defaults: { area: string; today: string }): TaskForm {
  if (!task) {
    return {
      title: "",
      notes: "",
      area: defaults.area,
      due: "",
      dueTime: "",
      repeat: emptyRepeat(monthDayOf(null, defaults.today)),
      assignee: "owner",
    };
  }
  return {
    title: task.title,
    notes: task.notes,
    area: task.area,
    due: task.due ?? "",
    dueTime: task.due_time ?? "",
    repeat: choiceFor(task.repeat, monthDayOf(task.due, defaults.today)),
    assignee: task.assignee,
  };
}

export function cleanTitle(title: string): string {
  return title.trim().replace(/\s+/g, " ");
}

export function cleanArea(area: string): string {
  return area.trim().toLowerCase();
}

/** Problems the browser can see before asking the server. Empty when the form may be sent. */
export function validateForm(form: TaskForm): FormErrors {
  const errors: FormErrors = {};
  const title = cleanTitle(form.title);
  if (!title) errors.title = "Give the task a title.";
  else if (title.length > TITLE_MAX) errors.title = `Keep the title to ${TITLE_MAX} characters or fewer.`;
  if (form.notes.length > NOTES_MAX) errors.notes = `Keep the notes to ${NOTES_MAX.toLocaleString("en-US")} characters or fewer.`;
  if (cleanArea(form.area).length > AREA_MAX) errors.area = `Keep the area to ${AREA_MAX} characters or fewer.`;
  if (form.due && dayToUtc(form.due) === null) errors.due = "Choose a real date.";
  const rule = ruleFor(form.repeat);
  if (form.dueTime && !/^\d{2}:\d{2}$/.test(form.dueTime)) errors.dueTime = "Enter the time as hours and minutes.";
  else if (form.dueTime && !form.due && !rule) errors.dueTime = "A time needs a due date. Choose a date or clear the time.";
  if (form.repeat.mode === "advanced" && !rule) errors.repeat = "Type a rule, or choose another way to repeat.";
  else if (rule && rule.length > RULE_MAX) errors.repeat = `Keep the rule to ${RULE_MAX} characters or fewer.`;
  if (form.repeat.mode === "monthly" && !(Number.isInteger(form.repeat.monthDay) && form.repeat.monthDay >= 1 && form.repeat.monthDay <= 31)) {
    errors.repeat = "Choose a day of the month from 1 to 31.";
  }
  return errors;
}

/** The body for POST /api/tasks. */
export function createBody(form: TaskForm): NewTask {
  return {
    title: cleanTitle(form.title),
    notes: form.notes,
    area: cleanArea(form.area),
    due: form.due || null,
    due_time: form.dueTime || null,
    repeat: ruleFor(form.repeat),
    assignee: form.assignee,
  };
}

/** The body for PATCH: only what differs from the task, so an untouched schedule is not re-anchored. */
export function changesBody(form: TaskForm, task: Task): TaskChanges {
  const changes: TaskChanges = {};
  const title = cleanTitle(form.title);
  if (title !== task.title) changes.title = title;
  if (form.notes !== task.notes) changes.notes = form.notes;
  const area = cleanArea(form.area);
  if (area !== task.area) changes.area = area;
  const due = form.due || null;
  if (due !== task.due) changes.due = due;
  const time = form.dueTime || null;
  if (time !== task.due_time) changes.due_time = time;
  const rule = ruleFor(form.repeat);
  const before = ruleFor(choiceFor(task.repeat, form.repeat.monthDay));
  if (rule !== before) changes.repeat = rule;
  if (form.assignee !== task.assignee) changes.assignee = form.assignee;
  return changes;
}

export function hasChanges(changes: TaskChanges): boolean {
  return Object.keys(changes).length > 0;
}
