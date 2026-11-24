// Small presentational pieces shared by the Apps screen.

import type { ReactNode } from "react";
import { errorDetail } from "../../api";
import type { PermissionLine } from "./api";
import type { LineGroup } from "./lib/permissions";
import type { AppState, Tone } from "./lib/status";
import { tierWords } from "./lib/status";
import type { TierKey } from "./lib/status";

const PILL_TONES: Record<Tone, string> = {
  ok: "border-success/40 bg-success/10 text-success",
  warn: "border-warning/40 bg-warning/10 text-warning",
  bad: "border-danger/40 bg-danger/10 text-danger",
  none: "border-sage/40 bg-sage/10 text-text-muted",
};
const DOT_TONES: Record<Tone, string> = { ok: "bg-success", warn: "bg-warning", bad: "bg-danger", none: "" };

export const SMALL_BTN = "btn h-7 gap-1.5 px-2 text-xs";

/** A rounded status pill with an optional coloured dot. */
export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex flex-none items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-[3px] font-mono text-[10.5px] leading-none ${PILL_TONES[tone]}`}>
      {tone === "none" ? null : <span className={`dot size-[5px] ${DOT_TONES[tone]}`} aria-hidden="true" />}
      {children}
    </span>
  );
}

/** A small square-cornered mono label. */
export function Tag({ children, tone = "plain", title }: { children: ReactNode; tone?: "plain" | "warn" | "bad" | "accent"; title?: string }) {
  const colour =
    tone === "bad"
      ? "border-danger/50 bg-danger/10 text-danger"
      : tone === "warn"
        ? "border-warning/40 bg-warning/10 text-warning"
        : tone === "accent"
          ? "border-accent/50 bg-accent/10 text-accent"
          : "border-border bg-surface text-text-muted";
  return (
    <span title={title} className={`inline-block flex-none whitespace-nowrap rounded border px-1.5 font-mono text-[10.5px] leading-4 ${colour}`}>
      {children}
    </span>
  );
}

const TIER_TONES: Record<TierKey, "plain" | "warn" | "bad"> = { described: "plain", sandboxed: "plain", full: "warn" };

/** The tier in two or three words; the sentence is its tooltip. */
export function TierBadge({ tier, bundled = false }: { tier: TierKey; bundled?: boolean }) {
  const words = tierWords({ tier: tier === "full" ? "full" : "sandboxed", described: tier === "described", provenance: bundled ? "bundled" : undefined });
  return (
    <Tag tone={TIER_TONES[tier]} title={words.sentence}>
      {words.label}
    </Tag>
  );
}

export function StatePill({ state }: { state: AppState }) {
  return <Pill tone={state.tone}>{state.label}</Pill>;
}

export function SectionHeading({ id, children, aside }: { id: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2 mt-6 flex flex-wrap items-center gap-2">
      <h2 id={id} className="min-w-0 text-[13px] font-semibold">
        {children}
      </h2>
      {aside ? <div className="ml-auto flex items-center gap-2">{aside}</div> : null}
    </div>
  );
}

/** A boxed message. A bad one is announced at once. */
export function Notice({ tone, title, children }: { tone?: "warn" | "bad" | "calm"; title?: string; children: ReactNode }) {
  const colour =
    tone === "bad"
      ? "border-danger/50 bg-danger/10 text-text"
      : tone === "warn"
        ? "border-warning/50 bg-warning/10 text-text"
        : "border-border bg-surface-2 text-text-muted";
  return (
    <div className={`break-words rounded-lg border px-3 py-2 text-xs ${colour}`} role={tone === "bad" ? "alert" : undefined}>
      {title ? <p className={`mb-0.5 text-[13px] font-semibold ${tone === "bad" ? "text-danger" : "text-text"}`}>{title}</p> : null}
      <div>{children}</div>
    </div>
  );
}

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

/** One permission sentence. The runs-as-you line is set apart as a warning. */
export function PermissionSentence({ line }: { line: PermissionLine }) {
  return (
    <li
      className={
        line.strong
          ? "rounded-lg border border-danger/50 bg-danger/10 px-2.5 py-2 text-[13px] font-semibold text-text"
          : "flex gap-2 text-[13px] text-text"
      }
    >
      {line.strong ? null : <span aria-hidden="true" className="mt-[7px] size-1 flex-none rounded-full bg-text-faint" />}
      <span className="min-w-0 break-words">{line.sentence}</span>
    </li>
  );
}

/** The permission lines in groups, the most sensitive group first. */
export function PermissionGroups({ groups }: { groups: readonly LineGroup[] }) {
  if (groups.length === 0) return <p className="text-sm text-text-faint">The server did not list what this app may do.</p>;
  return (
    <div className="flex flex-col gap-3">
      {groups.map((group) => (
        <div key={group.key}>
          {group.key === "warning" ? null : <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-faint">{group.heading}</h3>}
          <ul className="flex flex-col gap-1.5">
            {group.lines.map((line) => (
              <PermissionSentence key={line.id} line={line} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
