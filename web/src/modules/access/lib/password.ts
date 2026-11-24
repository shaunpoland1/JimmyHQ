// What the change-password form says, kept apart from the screen so it can be tested.

import { MIN_PASSWORD_LENGTH } from "../../../lib/auth.ts";

export type ChangeInput = { current: string; next: string; confirm: string };
export type ChangeErrors = { current?: string; next?: string; confirm?: string };

/** What is wrong with the form before anything is sent. Empty when it can be sent. */
export function validateChange(input: ChangeInput): ChangeErrors {
  const errors: ChangeErrors = {};
  if (!input.current) errors.current = "Enter your current password.";
  if (input.next.length < MIN_PASSWORD_LENGTH) errors.next = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  else if (input.current && input.next === input.current) errors.next = "Choose a password that differs from the current one.";
  if (input.confirm !== input.next) errors.confirm = "The passwords do not match.";
  return errors;
}

/** Which field a refusal from the server belongs under: 403 is a wrong current password, 422 a new one that breaks a rule. */
export function refusalField(status: number): "current" | "next" | null {
  if (status === 403) return "current";
  if (status === 422) return "next";
  return null;
}

function others(count: number): string {
  if (count <= 0) return "No other browser was signed in.";
  return count === 1 ? "1 other browser was signed out." : `${count} other browsers were signed out.`;
}

export function changedLine(sessionsEnded: number): string {
  return `Your password was changed. ${others(sessionsEnded)} This one stays signed in.`;
}

export function endedLine(sessionsEnded: number): string {
  return sessionsEnded <= 0 ? "No other browser was signed in. Nothing needed to change." : `${others(sessionsEnded)} This one stays signed in.`;
}
