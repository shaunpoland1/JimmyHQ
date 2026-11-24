import "./bench.css";
import type { ReactNode } from "react";
import { useAgentNames } from "../../lib/useAgentNames";
import type { Lane } from "./api";
import { describeFiler, kindTone } from "./lib/format";
import type { KindTone } from "./lib/format";
import { linkify, splitParagraphs } from "./lib/text";

/** The small tile beside a filer's name: a tinted letter for an agent, a round accent tile for the owner. */
export function FilerTile({ filedBy, source }: { filedBy: string; source?: string | null }) {
  const filer = describeFiler(filedBy, useAgentNames(), source);
  return (
    <span
      className="bench-tile"
      data-owner={filer.kind === "owner" ? "" : undefined}
      data-tone={filer.tone ?? undefined}
      aria-hidden="true"
    >
      {filer.letter}
    </span>
  );
}

const PILL_TONES: Record<KindTone, string> = {
  ok: "border-success/40 bg-success/10 text-success",
  warn: "border-warning/40 bg-warning/10 text-warning",
  accent: "border-accent/40 bg-accent/10 text-accent",
};

export function KindPill({ kind, lane }: { kind: string; lane: Lane }) {
  return (
    <span
      className={`inline-flex max-w-full items-center rounded-full border px-[7px] py-[3px] font-mono text-[10.5px] leading-none ${PILL_TONES[kindTone(kind, lane)]}`}
    >
      <span className="truncate">{kind}</span>
    </span>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-block max-w-full truncate rounded border border-border bg-surface px-[5px] align-middle font-mono text-[10.5px] leading-4 text-text-muted">
      {children}
    </span>
  );
}

/** The small upper-case heading above a section. */
export function Label({ children, className = "", htmlFor }: { children: ReactNode; className?: string; htmlFor?: string }) {
  const classes = `block font-mono text-[10.5px] uppercase tracking-[0.1em] text-text-faint ${className}`;
  return htmlFor ? (
    <label htmlFor={htmlFor} className={classes}>
      {children}
    </label>
  ) : (
    <div className={classes}>{children}</div>
  );
}

/**
 * Card text, shown as plain text: paragraphs and line breaks kept, and http or
 * https addresses made into links that open in a new tab. Nothing in a card is
 * ever treated as markup.
 */
export function BodyText({ text, className = "" }: { text: string; className?: string }) {
  const paragraphs = splitParagraphs(text);
  return (
    <div className={`flex max-w-[68ch] flex-col gap-2.5 text-[13px] leading-[1.6] ${className}`}>
      {paragraphs.map((paragraph, index) => (
        <p key={index} className="whitespace-pre-wrap [overflow-wrap:anywhere]">
          {linkify(paragraph).map((part, at) =>
            part.type === "link" ? (
              <a key={at} href={part.href} target="_blank" rel="noreferrer noopener" className="text-accent underline underline-offset-2">
                {part.value}
              </a>
            ) : (
              part.value
            ),
          )}
        </p>
      ))}
    </div>
  );
}
