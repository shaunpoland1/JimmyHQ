import { useMemo, useState } from "react";
import { errorDetail } from "../../api";
import type { AgentCard, VillageEvent } from "./api";
import { useAgents, useEvents } from "./hooks";
import { GROUPS, GROUP_LABELS, describeEvent, groupByDayOf, rawData, refusalCount, refusalsToday } from "./lib/events";
import type { Group } from "./lib/events";
import { whoIs } from "./lib/room";
import { displayName, toMs } from "./lib/status";
import { Flag, Note, clockTime, dayLabel } from "./parts";

const EMPTY: Record<Group, string> = {
  everything: "Nothing has been logged for this agent yet. Events appear as it takes its first turns.",
  actions: "It has not done anything yet. Actions appear here once it starts work.",
  refusals: "No refusals. That is good: nothing it tried was turned down.",
  feelings: "No changes to how it feels yet.",
  talk: "It has not said anything yet, in chat, in the break room or on the work board.",
  model: "No model calls logged yet.",
  night: "It has not slept yet. Its first night shows up here.",
};

/** The agent's event log, newest first. Refusals stand out: they are how an agent shows it is struggling. */
export function ActivityTab({ agent }: { agent: AgentCard }) {
  const [group, setGroup] = useState<Group>("everything");
  const name = displayName(agent);
  const { head, events: raw, more, loadOlder, loadingOlder, olderError } = useEvents(agent.id, group);
  // The village clock counts seconds; the screen works in milliseconds.
  const events = useMemo(() => raw.map((event) => ({ ...event, ts: toMs(event.ts) ?? 0 })), [raw]);
  const everyone = useAgents().data ?? [];
  const nameOf = useMemo(() => (id: string | null | undefined) => whoIs(id, everyone), [everyone]);
  const days = groupByDayOf(events, (ts) => dayLabel(ts));
  const lately = refusalsToday(agent.counts?.events_today);
  const refusals = refusalCount(events);

  return (
    <div className="flex flex-col gap-3.5">
      <div className="village-seg" role="group" aria-label="Kinds of event">
        {GROUPS.map((item) => (
          <button key={item} type="button" aria-pressed={group === item} onClick={() => setGroup(item)}>
            {GROUP_LABELS[item]}
          </button>
        ))}
      </div>
      {lately ? <Note tone="warn">{lately}</Note> : null}

      <section className="village-section" aria-label="Events" aria-busy={head.isFetching && events.length === 0}>
        <h2>{GROUP_LABELS[group]}, newest first</h2>
        {head.isPending ? <p className="text-sm text-text-faint">Loading what {name} has been doing…</p> : null}
        {head.isError && events.length === 0 ? (
          <Note tone="bad">{errorDetail(head.error)} Only the owner can read an agent's log: sign in as the owner, then reload the page.</Note>
        ) : null}
        {head.isSuccess && events.length === 0 ? <p className="text-sm text-text-muted">{EMPTY[group]}</p> : null}
        {events.length > 0 ? (
          <p className="mb-3 text-xs text-text-faint">
            {events.length} {events.length === 1 ? "event" : "events"} shown{refusals > 0 && group !== "refusals" ? `, ${refusals} of them refusals` : ""}. Open "The data behind it" on any line to see exactly what was logged. Times are in your time zone.
          </p>
        ) : null}
        {head.isError && events.length > 0 ? <Note tone="warn">The latest read failed ({errorDetail(head.error)}), so this list may be behind. It tries again by itself.</Note> : null}
        {days.map((day) => (
          <div key={`${day.day}-${day.items[0]?.id}`} className="mt-2 first:mt-0">
            <h3 className="village-day-head">{day.day}</h3>
            <ol className="flex flex-col gap-1.5">
              {day.items.map((event) => (
                <EventRow key={event.id} event={event} nameOf={nameOf} />
              ))}
            </ol>
          </div>
        ))}
        {more && events.length > 0 ? (
          <div className="mt-3 flex flex-col items-start gap-2">
            <button type="button" className="btn h-7 px-2.5 text-xs" onClick={() => void loadOlder()} disabled={loadingOlder}>
              {loadingOlder ? "Loading…" : "Load earlier events"}
            </button>
            {olderError ? <Note tone="bad">{errorDetail(olderError)} Try again.</Note> : null}
          </div>
        ) : null}
        {!more && events.length > 0 ? <p className="mt-3 text-xs text-text-faint">That is the start of the log.</p> : null}
      </section>
    </div>
  );
}

function EventRow({ event, nameOf }: { event: VillageEvent; nameOf: (id: string | null | undefined) => string }) {
  const said = describeEvent(event, nameOf);
  return (
    <li className="village-event" data-tone={said.tone}>
      <time className="village-event-time" dateTime={new Date(event.ts).toISOString()}>
        {clockTime(event.ts)}
      </time>
      <div className="min-w-0">
        <p className="text-sm text-text [overflow-wrap:anywhere]">
          {said.tone === "refusal" ? (
            <>
              <Flag>Refused</Flag>{" "}
            </>
          ) : null}
          {said.headline}
        </p>
        {said.detail ? <p className="village-plain mt-0.5 text-xs text-text-muted [overflow-wrap:anywhere]">{said.detail}</p> : null}
        <details className="village-raw">
          <summary>The data behind it</summary>
          <pre className="village-pre" tabIndex={0}>
            {rawData(event)}
          </pre>
        </details>
      </div>
    </li>
  );
}
