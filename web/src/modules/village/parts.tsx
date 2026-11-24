import "./village.css";
import type { ReactNode } from "react";
import type { AgentSummary, Needs } from "./api";
import { NEED_HELP, needSentence, readNeeds } from "./lib/needs";
import { initials, untilText } from "./lib/status";
import type { Colour, Doing } from "./lib/status";

// Times are stored as unix milliseconds in UTC; these turn them into the owner's local words.

export function clockTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(ms));
}

export function dayLabel(ms: number, now: number = Date.now()): string {
  const day = (value: number) => new Date(value).toDateString();
  if (day(ms) === day(now)) return "Today";
  if (day(ms) === day(now - 86_400_000)) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", year: new Date(ms).getFullYear() === new Date(now).getFullYear() ? undefined : "numeric" }).format(new Date(ms));
}

export function dateTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(ms));
}

/** A YYYY-MM-DD day as words, without moving it across a time zone. */
export function plainDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  return new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date(year, month - 1, date));
}

/** "Wakes in 20 minutes, at 6:30 AM", when the server says when. */
export function untilSentence(doing: Doing, now: number): string | null {
  if (doing.untilMs === null) return null;
  const verb = doing.state === "asleep" ? "Wakes" : "Back";
  if (doing.untilMs <= now) return `${verb} ${untilText(doing.untilMs, now)}.`;
  return `${verb} ${untilText(doing.untilMs, now)}, at ${clockTime(doing.untilMs)}.`;
}

export function Sigil({ agent, size }: { agent: Pick<AgentSummary, "id" | "name" | "role">; size?: "lg" }) {
  return (
    <span className="village-sigil" data-size={size} aria-hidden="true">
      {initials(agent)}
    </span>
  );
}

export function colourClass(colour: Colour): string {
  return `village-ag-${colour}`;
}

export function Pulse({ state }: { state: Doing["state"] }) {
  return <span className="village-pulse" data-state={state} aria-hidden="true" />;
}

/**
 * The five needs as bars with a plain word each. The bar is the honest number;
 * the word and the colour say whether that number is good, since for frustration
 * and curiosity a full bar is the bad end.
 */
export function Vitals({ needs, detail = "glance" }: { needs: Needs | undefined; detail?: "glance" | "full" }) {
  const readings = readNeeds(needs);
  if (readings.length === 0) return <p className="text-xs text-text-faint">No vitals yet. They appear after its first turn.</p>;
  return (
    <dl className="village-vitals" data-detail={detail}>
      {readings.map((reading) => (
        <div key={reading.need} className="village-vital" data-tone={reading.tone} title={`${needSentence(reading)} ${NEED_HELP[reading.need]}`}>
          <dt>{reading.label}</dt>
          <div className="village-track" aria-hidden="true">
            <i style={{ width: `${reading.value}%` }} />
          </div>
          {detail === "full" ? (
            <span className="village-num" aria-hidden="true">
              {reading.value}
            </span>
          ) : null}
          <dd>
            <span className="sr-only">{`${reading.value} of 100, `}</span>
            {reading.word}
            {reading.highIsBad ? <span className="sr-only">. Lower is better.</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Flag({ children, tone }: { children: ReactNode; tone?: "quiet" | "dream" }) {
  return (
    <span className="village-flag" data-tone={tone}>
      {children}
    </span>
  );
}

export function Note({ children, tone, live }: { children: ReactNode; tone?: "warn" | "bad" | "good"; live?: boolean }) {
  return (
    <p className="village-note" data-tone={tone} role={tone === "bad" ? "alert" : live ? "status" : undefined}>
      {children}
    </p>
  );
}

export function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="village-section">
      <h2 className="flex items-center justify-between gap-2">
        <span>{title}</span>
        {action}
      </h2>
      {children}
    </section>
  );
}

export function DialogButtons({ onCancel, cancel = "Cancel", children }: { onCancel: () => void; cancel?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3">
      <button type="button" className="btn" onClick={onCancel}>
        {cancel}
      </button>
      {children}
    </div>
  );
}
