// Small presentational pieces shared by the models settings section.

import { useId } from "react";
import type { ReactNode, SelectHTMLAttributes } from "react";
import { errorDetail } from "../../api";
import type { StatusTone } from "./lib/providers";

const PILL_TONES: Record<StatusTone, string> = {
  ok: "border-success/40 bg-success/10 text-success",
  warn: "border-warning/40 bg-warning/10 text-warning",
  bad: "border-danger/40 bg-danger/10 text-danger",
  none: "border-sage/40 bg-sage/10 text-text-muted",
};
const DOT_TONES: Record<StatusTone, string> = { ok: "bg-success", warn: "bg-warning", bad: "bg-danger", none: "" };

/** A rounded status pill with an optional coloured dot. */
export function Pill({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  return (
    <span className={`inline-flex flex-none items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-[3px] font-mono text-[10.5px] leading-none ${PILL_TONES[tone]}`}>
      {tone === "none" ? null : <span className={`dot size-[5px] ${DOT_TONES[tone]}`} aria-hidden="true" />}
      {children}
    </span>
  );
}

/** A small square-cornered mono label. */
export function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span title={title} className="inline-block flex-none whitespace-nowrap rounded border border-border bg-surface px-1.5 font-mono text-[10.5px] leading-4 text-text-muted">
      {children}
    </span>
  );
}

/** Says whether something runs on the owner's machines or on someone else's. */
export function LocalityTag({ isLocal }: { isLocal: boolean | null }) {
  if (isLocal === null) return null;
  return isLocal ? (
    <Tag title="Prompts stay on machines you control">local</Tag>
  ) : (
    <span title="Prompts are sent to someone else's machines" className="inline-block flex-none whitespace-nowrap rounded border border-warning/40 bg-warning/10 px-1.5 font-mono text-[10.5px] leading-4 text-warning">
      hosted
    </span>
  );
}

export function SectionHeading({ id, children, aside }: { id: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2 mt-6 flex flex-wrap items-center gap-2">
      <h3 id={id} className="flex min-w-0 items-center gap-2 text-[13px] font-semibold">
        {children}
      </h3>
      {aside ? <div className="ml-auto flex items-center gap-2">{aside}</div> : null}
    </div>
  );
}

/** What a query shows while it loads. */
export function LoadingCard({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-xl border border-border bg-surface px-4 py-6 text-sm text-text-faint" role="status">
      {children}
    </p>
  );
}

/** What a query shows when it fails: the server's sentence and a way to try again. */
export function ErrorCard({ what, error, onRetry }: { what: string; error: unknown; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface px-4 py-4" role="alert">
      <p className="min-w-0 flex-1 basis-48 text-sm text-text-muted">
        <span className="font-medium text-text">{what} could not be loaded.</span> {errorDetail(error)}
      </p>
      <button type="button" className="btn h-7 px-2 text-xs" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

type SelectFieldProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className"> & {
  label: string;
  hint?: string;
  children: ReactNode;
};

/** A labelled select, matching the shared `Field`. */
export function SelectField({ label, hint, children, ...select }: SelectFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-text">
        {label}
      </label>
      <select id={id} className="input pr-8" aria-describedby={hint ? hintId : undefined} {...select}>
        {children}
      </select>
      {hint ? (
        <p id={hintId} className="text-xs text-text-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export const SMALL_BTN = "btn h-7 px-2 text-xs";
