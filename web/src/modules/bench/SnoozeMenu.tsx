import { Clock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { Modal } from "../../components/Modal";
import type { SnoozeChoice } from "./api";
import { formatWhen, fromLocalInput, offeredSnoozes, snoozeProblem, toLocalInput } from "./lib/format";

type SnoozeMenuProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Current time, unix milliseconds. */
  now: number;
  /** The server's default snooze length in hours, when settings have loaded. */
  defaultHours: number | undefined;
  /** The quick choices the server offers, with their times. */
  choices: readonly SnoozeChoice[] | undefined;
  /** The owner's time zone ("local" for the server's own), which the times are shown in. */
  timezone: string | undefined;
  /** True when the card is already snoozed, so choosing moves the time. */
  snoozed: boolean;
  disabled?: boolean;
  /** Snooze until a time; undefined uses the server's default length. */
  onSnooze: (until?: number) => void;
  onPickTime: () => void;
};

function lengthLabel(hours: number): string {
  return hours >= 48 && hours % 24 === 0 ? `${hours / 24} d` : `${hours} h`;
}

/** The Snooze button and its menu of times. */
export function SnoozeMenu({ open, onOpenChange, now, defaultHours, choices, timezone, snoozed, disabled, onSnooze, onPickTime }: SnoozeMenuProps) {
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointer = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) onOpenChange(false);
    };
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open, onOpenChange]);

  const close = (refocus: boolean) => {
    onOpenChange(false);
    if (refocus) trigger.current?.focus();
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLElement);
    const focus = (target: number) => {
      event.preventDefault();
      items[(target + items.length) % items.length]?.focus();
    };
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === "ArrowDown") focus(index + 1);
    else if (event.key === "ArrowUp") focus(index - 1);
    else if (event.key === "Home") focus(0);
    else if (event.key === "End") focus(items.length - 1);
    else if (event.key === "Tab") onOpenChange(false);
  };

  const choose = (until?: number) => {
    close(true);
    onSnooze(until);
  };

  const itemClass =
    "flex w-full items-center justify-between gap-3 rounded-[5px] px-2 py-1.5 text-left text-[13px] hover:bg-surface-2 focus-visible:bg-surface-2";
  const hintClass = "font-mono text-[11px] text-text-faint";

  return (
    <div ref={root} className="relative flex-none">
      <button
        ref={trigger}
        type="button"
        className="btn h-7 px-2 text-xs"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => onOpenChange(!open)}
      >
        <Clock size={14} aria-hidden="true" />
        {snoozed ? "Snooze again" : "Snooze"}
      </button>
      {open ? (
        <div
          ref={menu}
          role="menu"
          aria-label="Snooze until"
          onKeyDown={onKeyDown}
          className="absolute right-0 top-8 z-20 min-w-[210px] rounded-lg border border-border bg-surface p-1 shadow-pop"
        >
          <button type="button" role="menuitem" className={itemClass} onClick={() => choose()}>
            Default
            {defaultHours !== undefined ? <span className={hintClass}>{lengthLabel(defaultHours)}</span> : null}
          </button>
          {offeredSnoozes(choices, now).map((preset) => (
            <button key={preset.id} type="button" role="menuitem" className={itemClass} onClick={() => choose(preset.until)}>
              {preset.label}
              <span className={hintClass}>{formatWhen(preset.until, now, undefined, timezone)}</span>
            </button>
          ))}
          <button
            type="button"
            role="menuitem"
            className={itemClass}
            onClick={() => {
              onOpenChange(false);
              onPickTime();
            }}
          >
            Pick a time…
          </button>
        </div>
      ) : null}
    </div>
  );
}

type PickTimeDialogProps = {
  title: string;
  now: number;
  /** The quick choices the server offers; the field starts at "tomorrow morning". */
  choices: readonly SnoozeChoice[] | undefined;
  onCancel: () => void;
  onSnooze: (until: number) => void;
};

/** Dialog for choosing any snooze time. */
export function PickTimeDialog({ title, now, choices, onCancel, onSnooze }: PickTimeDialogProps) {
  const [value, setValue] = useState(() => {
    const tomorrow = offeredSnoozes(choices, now).find((preset) => preset.id === "tomorrow");
    return toLocalInput(tomorrow ? tomorrow.until : now + 24 * 60 * 60 * 1000);
  });
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const until = fromLocalInput(value);
    const problem = snoozeProblem(until, Date.now());
    if (problem || until === null) {
      setError(problem ?? "Pick a date and a time.");
      return;
    }
    onSnooze(until);
  };

  return (
    <Modal title="Snooze until" onClose={onCancel} widthClass="max-w-sm">
      <form onSubmit={submit} noValidate>
        <div className="flex flex-col gap-1.5 px-4 py-4">
          <p className="mb-1 truncate text-xs text-text-muted">{title}</p>
          <label htmlFor="bench-snooze-time" className="text-xs font-medium text-text">
            Bring it back at
          </label>
          <input
            id="bench-snooze-time"
            type="datetime-local"
            className="input"
            value={value}
            min={toLocalInput(now)}
            onChange={(event) => {
              setValue(event.target.value);
              setError(null);
            }}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "bench-snooze-time-error" : undefined}
            data-autofocus
          />
          {error ? (
            <p id="bench-snooze-time-error" className="text-xs text-danger" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary">
            Snooze
          </button>
        </div>
      </form>
    </Modal>
  );
}
