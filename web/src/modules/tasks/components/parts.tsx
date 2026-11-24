import "../tasks.css";

import { User } from "lucide-react";
import type { ReactNode } from "react";
import { useAgentNames } from "../../../lib/useAgentNames";
import { assigneeLabel, tileFor } from "../lib/group";

/** A small mono area tag. */
export function Tag({ children, className = "inline-block" }: { children: ReactNode; className?: string }) {
  return (
    <span className={`max-w-[9rem] flex-none truncate rounded border border-border bg-surface px-1.5 font-mono text-[10.5px] leading-4 text-text-muted ${className}`}>
      {children}
    </span>
  );
}

const PILL_TONES = {
  plain: "border-sage/40 bg-sage/10 text-text-muted",
  accent: "border-accent/40 bg-accent/10 text-accent",
  ok: "border-success/40 bg-success/10 text-success",
  warn: "border-warning/40 bg-warning/10 text-warning",
  bad: "border-danger/40 bg-danger/10 text-danger",
} as const;

export function Pill({ tone = "plain", children, title }: { tone?: keyof typeof PILL_TONES; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex flex-none items-center gap-1 whitespace-nowrap rounded-full border px-1.5 py-[3px] font-mono text-[10.5px] leading-none ${PILL_TONES[tone]}`}>
      {children}
    </span>
  );
}

/** Who a task belongs to: a round accent tile for the owner, a tinted letter tile for an agent. */
export function AssigneeTile({ assignee, known }: { assignee: string; known: readonly string[] }) {
  const names = useAgentNames();
  const tile = tileFor(assignee, known, names);
  const label = assigneeLabel(assignee, names);
  if (tile.kind === "owner") {
    return (
      <span role="img" aria-label={`Assigned to you`} title={label} className="grid size-5 flex-none place-items-center rounded-full bg-accent text-accent-fg">
        <User size={11} strokeWidth={2.4} aria-hidden="true" />
      </span>
    );
  }
  return (
    <span
      role="img"
      aria-label={`Assigned to ${label}`}
      title={label}
      data-tone={tile.tone}
      className="task-tile-agent grid size-5 flex-none place-items-center rounded-[5px] font-mono text-[10px] font-semibold"
    >
      {tile.letter}
    </span>
  );
}

type SegOption<T extends string> = { value: T; label: string; count?: string };

/** A segmented choice, as in the design's List / Board and open / done switches. */
export function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: readonly SegOption<T>[]; onChange: (value: T) => void }) {
  return (
    <div role="group" aria-label={label} className="inline-flex max-w-full gap-0.5 rounded-[7px] border border-border bg-surface p-0.5">
      {options.map((option) => {
        const pressed = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={pressed}
            onClick={() => onChange(option.value)}
            className={`flex h-6 items-center gap-1.5 whitespace-nowrap rounded-[5px] px-2.5 text-[12.5px] ${
              pressed ? "bg-surface-2 font-semibold text-text ring-1 ring-inset ring-border" : "text-text-muted hover:text-text"
            }`}
          >
            {option.label}
            {option.count !== undefined ? <span className="font-mono text-[11px] font-normal text-text-faint">{option.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
