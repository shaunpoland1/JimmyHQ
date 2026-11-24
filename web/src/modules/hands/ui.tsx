// Small presentational pieces shared by the integrations screens.

import { useId } from "react";
import type { ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { errorDetail } from "../../api";
import type { Tone } from "./lib/wording";

export const SMALL_BTN = "btn h-7 px-2 text-xs";
export const CARD = "@container rounded-xl border border-border bg-surface";

const PILL_TONES: Record<Tone, string> = {
  ok: "border-success/40 bg-success/10 text-success",
  warn: "border-warning/40 bg-warning/10 text-warning",
  bad: "border-danger/40 bg-danger/10 text-danger",
  none: "border-sage/40 bg-sage/10 text-text-muted",
};
const DOT_TONES: Record<Tone, string> = { ok: "bg-success", warn: "bg-warning", bad: "bg-danger", none: "bg-sage" };

/** A rounded status pill with a coloured dot. The words carry the meaning; the colour only helps. */
export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex flex-none items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-[3px] font-mono text-[10.5px] leading-none ${PILL_TONES[tone]}`}>
      <span className={`dot size-[5px] ${DOT_TONES[tone]}`} aria-hidden="true" />
      {children}
    </span>
  );
}

/** A small square-cornered mono label. */
export function Tag({ children, title, tone }: { children: ReactNode; title?: string; tone?: "warn" }) {
  return (
    <span
      title={title}
      className={`inline-block flex-none whitespace-nowrap rounded border px-1.5 font-mono text-[10.5px] leading-4 ${tone === "warn" ? "border-warning/40 bg-warning/10 text-warning" : "border-border bg-surface text-text-muted"}`}
    >
      {children}
    </span>
  );
}

export function Loading({ children }: { children: ReactNode }) {
  return (
    <p className={`${CARD} px-4 py-5 text-sm text-text-faint`} role="status">
      {children}
    </p>
  );
}

/** What a query shows when it fails: the server's sentence and a way to try again. */
export function Failed({ what, error, onRetry }: { what: string; error: unknown; onRetry: () => void }) {
  return (
    <div className={`${CARD} flex flex-wrap items-center gap-3 px-4 py-4`} role="alert">
      <p className="min-w-0 flex-1 basis-48 text-sm text-text-muted">
        <span className="font-medium text-text">{what} could not be loaded.</span> {errorDetail(error)}
      </p>
      <button type="button" className={SMALL_BTN} onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

export function Notice({ tone, children, live }: { tone?: "warn" | "bad" | "good"; children: ReactNode; live?: boolean }) {
  const colour = tone === "bad" ? "border-danger/40 text-danger" : tone === "warn" ? "border-warning/40 text-text" : tone === "good" ? "border-success/40 text-text" : "border-border text-text-muted";
  return (
    <p className={`break-words rounded-lg border bg-surface-2 px-3 py-2 text-xs ${colour}`} role={tone === "bad" ? "alert" : live ? "status" : undefined}>
      {children}
    </p>
  );
}

type SelectProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className"> & { label: string; hint?: string; error?: string; children: ReactNode };

/** A labelled select, matching the shared `Field`. */
export function SelectField({ label, hint, error, children, ...select }: SelectProps) {
  const id = useId();
  const described = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-text">
        {label}
      </label>
      <select id={id} className="input pr-8" aria-invalid={error ? true : undefined} aria-describedby={described} {...select}>
        {children}
      </select>
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-text-faint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

type AreaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "className"> & { label: string; hint?: string; error?: string };

export function AreaField({ label, hint, error, ...area }: AreaProps) {
  const id = useId();
  const described = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-text">
        {label}
      </label>
      <textarea id={id} className="input h-auto min-h-16 py-2 font-mono text-xs" aria-invalid={error ? true : undefined} aria-describedby={described} {...area} />
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-text-faint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Times are stored as unix milliseconds in UTC and shown in the owner's time zone, here only. */
export function whenText(ms: number, now: number = Date.now()): string {
  const date = new Date(ms);
  const sameDay = new Date(now).toDateString() === date.toDateString();
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return sameDay ? `today ${time}` : `${date.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${time}`;
}
