// Where the keyboard starts in a question the shell asks.

export type ConfirmButton = "cancel" | "confirm";

/**
 * The button a confirm dialog focuses first. A destructive answer never has the
 * keyboard when the dialog opens: Enter or Space would delete at once.
 */
export function confirmStartsOn(danger: boolean): ConfirmButton {
  return danger ? "cancel" : "confirm";
}
