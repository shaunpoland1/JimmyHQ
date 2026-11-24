import "./wall.css";

import type { UseQueryResult } from "@tanstack/react-query";
import { Check, RotateCw } from "lucide-react";
import { memo, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { errorDetail } from "../../api";
import type { TapeEvent } from "../../api";
import { formatBytes, formatCount } from "../../lib/format";
import { plainActor, plainKind } from "../../lib/plainWords";
import { formatCompact } from "../models/lib/format";
import { lateText } from "../tasks/lib/dates";
import { useBench, useProviders, useRecentEvents, useTapeStats, useToday, useUsage } from "./hooks";
import { clockFace, clockWidthEm, formatWait, shortTime, timeOfDay } from "./lib/clock";
import { fitLines } from "./lib/layout";
import type { TileId } from "./lib/settings";
import { benchSummary, moreText, progressText, providerLines, providersText, todaySummary, usageRows, waitedMs } from "./lib/tiles";
import type { Tally } from "./lib/tiles";

type Query = Pick<UseQueryResult<unknown>, "isError" | "error" | "refetch">;

type TileProps = {
  id: TileId;
  title: string;
  /** A short note at the right of the heading. */
  meta?: string;
  /** Queries whose last read failed while older data is still on show. */
  stale?: Query[];
  children: ReactNode;
};

/** One tile: a small heading, an optional note, and the body. */
function Tile({ id, title, meta, stale = [], children }: TileProps) {
  const failed = stale.filter((query) => query.isError);
  const first = failed[0];
  return (
    <section className="wall-tile" data-tile={id} aria-labelledby={`wall-${id}`}>
      <header className="wall-lab">
        <h2 id={`wall-${id}`}>{title}</h2>
        <div className="wall-lab-meta">
          {meta ? <span>{meta}</span> : null}
          {first ? (
            <button
              type="button"
              className="wall-retry"
              aria-label={`${title} could not refresh: ${errorDetail(first.error)} Try again.`}
              title={`Could not refresh: ${errorDetail(first.error)}`}
              onClick={() => failed.forEach((query) => void query.refetch())}
            >
              <RotateCw size="1.2em" aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </header>
      <div className="wall-body">{children}</div>
    </section>
  );
}

function Waiting() {
  return (
    <div className="wall-note" role="status">
      <p>Loading…</p>
    </div>
  );
}

/** A read that failed with nothing older to show: the server's own words, and a way to retry. */
function Problem({ what, query }: { what: string; query: Query }) {
  return (
    <div className="wall-note" data-tone="bad" role="alert">
      <strong>{what} unavailable</strong>
      <p className="wall-note-detail">{errorDetail(query.error)}</p>
      <button type="button" className="wall-btn" onClick={() => void query.refetch()}>
        Try again
      </button>
    </div>
  );
}

function Quiet({ children, calm = false }: { children: ReactNode; calm?: boolean }) {
  return (
    <div className="wall-note" data-tone={calm ? "calm" : undefined}>
      {calm ? <Check size="1.8em" strokeWidth={2.2} aria-hidden="true" /> : null}
      <p>{children}</p>
    </div>
  );
}

/** A line's height in em: `.wall-line` in wall.css. */
const LINE_EM = 1.8;

type FitListProps<T> = {
  label: string;
  items: readonly T[];
  /** The most lines wanted, however much room there is. */
  cap: number;
  /** Say how many were left out. Off for a list with no end, such as the feed. */
  countRest?: boolean;
  /** Draws one line: an `<li className="wall-line">`. */
  line: (item: T, index: number) => ReactNode;
};

/**
 * A list that never scrolls: it draws as many lines as fit in the space it is
 * given, and says how many it left out.
 */
function FitList<T>({ label, items, cap, countRest = true, line }: FitListProps<T>) {
  const box = useRef<HTMLUListElement>(null);
  const [room, setRoom] = useState<number | null>(null);

  // Lines are all one height, so the first one says how many fit. With no line
  // drawn (there was no room for one), the height comes from the type size, or
  // the list would stay empty after the tile grew again.
  const measure = () => {
    const list = box.current;
    if (!list) return;
    const first = list.firstElementChild;
    const height = first ? first.getBoundingClientRect().height : parseFloat(getComputedStyle(list).fontSize) * LINE_EM;
    if (!(height > 0)) return;
    const next = Math.floor((list.getBoundingClientRect().height + 0.5) / height);
    setRoom((current) => (current === next ? current : next));
  };
  const measureNow = useRef(measure);
  measureNow.current = measure;

  useLayoutEffect(() => {
    measureNow.current();
  });
  useLayoutEffect(() => {
    const list = box.current;
    if (!list || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => measureNow.current());
    observer.observe(list);
    return () => observer.disconnect();
  }, []);

  const plan = fitLines(items.length, cap, room, countRest);
  return (
    <ul ref={box} className="wall-list" aria-label={label}>
      {items.slice(0, plan.shown).map(line)}
      {plan.more > 0 ? (
        <li className="wall-line" key="wall-more">
          <span className="wall-more">{moreText(plan.more)}</span>
        </li>
      ) : null}
    </ul>
  );
}

// ---- clock ----

type ClockTileProps = { now: number; hour24: boolean; seconds: boolean };

export const ClockTile = memo(function ClockTile({ now, hour24, seconds }: ClockTileProps) {
  const face = clockFace(now, { hour24, seconds });
  const date = new Date(now);
  return (
    <section className="wall-tile" data-tile="clock" aria-label="Clock">
      <div className="wall-clock-box">
        <time
          className="wall-clock"
          dateTime={date.toISOString()}
          style={{ "--clock-em": clockWidthEm(face) } as CSSProperties}
        >
          {face.time}
          {face.period ? <small>{face.period}</small> : null}
        </time>
        <div className="wall-date">{face.date}</div>
      </div>
    </section>
  );
});

// ---- usage ----

export const UsageTile = memo(function UsageTile() {
  const usage = useUsage();
  const tape = useTapeStats();
  const data = usage.data;
  const rows = data ? usageRows(data) : [];
  const calls = data?.all_time.calls ?? 0;
  const localShare = data && calls > 0 ? Math.round((data.all_time.local_calls / calls) * 100) : null;

  return (
    <Tile id="usage" title="Usage" stale={[...(data ? [usage] : []), ...(tape.data ? [tape] : [])]}>
      {!data && usage.isError ? (
        <Problem what="Model usage" query={usage} />
      ) : !data ? (
        <Waiting />
      ) : (
        <>
          <div className="wall-usage" role="table" aria-label="Model usage and Tape size">
            <div role="row">
              <span role="columnheader">
                <span className="sr-only">Measure</span>
              </span>
              <span role="columnheader">24 h</span>
              <span role="columnheader">all time</span>
            </div>
            {rows.map((row) => (
              <div role="row" key={row.id}>
                <span role="rowheader" aria-label={row.name} title={row.name}>
                  {row.label}
                </span>
                <span role="cell">{row.day}</span>
                <span role="cell">{row.all}</span>
              </div>
            ))}
            <div data-rule aria-hidden="true" />
            <div role="row">
              <span role="rowheader" aria-label="Tape: events and size" title="Tape: events and size">
                tape
              </span>
              <span role="cell" data-wide>
                {tape.data ? (
                  <span aria-label={`${formatCount(tape.data.events)} events, ${formatBytes(tape.data.bytes)}`}>
                    {formatCompact(tape.data.events)} · {formatBytes(tape.data.bytes)}
                  </span>
                ) : tape.isError ? (
                  <button
                    type="button"
                    className="wall-btn"
                    aria-label={`Tape size unavailable: ${errorDetail(tape.error)} Try again.`}
                    title={errorDetail(tape.error)}
                    onClick={() => void tape.refetch()}
                  >
                    Try again
                  </button>
                ) : (
                  "…"
                )}
              </span>
            </div>
          </div>
          {localShare !== null ? (
            <div className="wall-share">
              <div className="wall-bar" aria-hidden="true">
                <i style={{ width: `${localShare}%` }} />
              </div>
              {localShare}% of calls ran locally
            </div>
          ) : null}
        </>
      )}
    </Tile>
  );
});

// ---- bench ----

type BenchLine = Tally & { group: "kind" | "source"; lead: boolean };

function benchLines(kinds: readonly Tally[], sources: readonly Tally[]): BenchLine[] {
  return [
    ...kinds.map((tally, index): BenchLine => ({ ...tally, group: "kind", lead: index === 0 })),
    ...sources.map((tally, index): BenchLine => ({ ...tally, group: "source", lead: index === 0 })),
  ];
}

/** A kind in words, where a source is already a name. */
function shownTally(item: BenchLine): string {
  return item.group === "kind" ? plainKind(item.name, true) : item.name;
}

/** `minute` is the time now, to the minute: the wait is counted from it. */
export const BenchTile = memo(function BenchTile({ minute }: { minute: number }) {
  const bench = useBench();
  const summary = bench.data ? benchSummary(bench.data) : null;
  const waited = summary ? waitedMs(summary.oldestSince, Math.max(minute, Date.now())) : null;

  return (
    <Tile id="bench" title="Bench" meta={waited !== null ? `oldest ${formatWait(waited)}` : undefined} stale={summary ? [bench] : []}>
      {!summary && bench.isError ? (
        <Problem what="The Bench" query={bench} />
      ) : !summary ? (
        <Waiting />
      ) : summary.needs === 0 ? (
        <Quiet calm>Nothing needs you</Quiet>
      ) : (
        <>
          <p className="wall-headline">
            <span className="wall-big">{formatCount(summary.needs)}</span>
            <span>{summary.needs === 1 ? "card needs you" : "cards need you"}</span>
          </p>
          <FitList
            label="Cards that need you, by kind and by source"
            items={benchLines(summary.kinds, summary.sources)}
            cap={Number.MAX_SAFE_INTEGER}
            line={(item) => (
              <li className="wall-line" key={`${item.group}:${item.name}`} aria-label={`${item.group} ${shownTally(item)}: ${item.count}`}>
                <span className="wall-tag" aria-hidden="true">
                  {item.lead ? item.group : ""}
                </span>
                <span className="wall-grow" title={item.name}>
                  {shownTally(item)}
                </span>
                <span className="wall-count">{formatCount(item.count)}</span>
              </li>
            )}
          />
        </>
      )}
    </Tile>
  );
});

// ---- today ----

export const TodayTile = memo(function TodayTile({ lines, hour24 }: { lines: number; hour24: boolean }) {
  const today = useToday();
  const summary = today.data ? todaySummary(today.data) : null;

  return (
    <Tile
      id="today"
      title="Today"
      meta={summary && summary.total > 0 ? progressText(summary.done, summary.total) : undefined}
      stale={summary ? [today] : []}
    >
      {!summary && today.isError ? (
        <Problem what="Today" query={today} />
      ) : !summary ? (
        <Waiting />
      ) : summary.total === 0 ? (
        <Quiet>Nothing is due today</Quiet>
      ) : (
        <FitList
          label="Today's tasks"
          items={summary.lines}
          cap={lines}
          line={(task) => {
            const note = task.lapsedDays > 0 ? lateText(task.lapsedDays) : task.time ? timeOfDay(task.time, hour24) : "";
            return (
              <li
                className="wall-line"
                key={task.id}
                data-done={task.done}
                aria-label={`${task.title}, ${task.done ? "done" : note || "to do"}`}
              >
                <span className="wall-tick" aria-hidden="true">
                  <Check strokeWidth={3.5} />
                </span>
                <span className="wall-grow">{task.title}</span>
                {note ? (
                  <span className="wall-side" data-tone={task.lapsedDays > 0 ? "bad" : undefined}>
                    {note}
                  </span>
                ) : null}
              </li>
            );
          }}
        />
      )}
    </Tile>
  );
});

// ---- models ----

export const ModelsTile = memo(function ModelsTile() {
  const health = useProviders();
  const lines = health.data ? providerLines(health.data) : null;

  return (
    <Tile id="models" title="Models" meta={lines ? providersText(lines) : undefined} stale={lines ? [health] : []}>
      {!lines && health.isError ? (
        <Problem what="Model status" query={health} />
      ) : !lines ? (
        <Waiting />
      ) : lines.length === 0 ? (
        <Quiet>No model providers are switched on</Quiet>
      ) : (
        <FitList
          label="Model providers"
          items={lines}
          cap={Number.MAX_SAFE_INTEGER}
          line={(provider) => (
            <li className="wall-line" key={provider.id} aria-label={`${provider.label}: ${provider.text}`}>
              <span className="wall-dot" data-state={provider.state} aria-hidden="true" />
              <span className="wall-grow">{provider.label}</span>
              <span className="wall-side" data-tone={provider.state === "offline" ? "bad" : undefined}>
                {provider.text}
              </span>
            </li>
          )}
        />
      )}
    </Tile>
  );
});

// ---- feed ----

type FeedTileProps = { lines: number; hour24: boolean; live: boolean };

export const FeedTile = memo(function FeedTile({ lines, hour24, live }: FeedTileProps) {
  const { page, events } = useRecentEvents(lines);

  return (
    <Tile id="feed" title="Tape" meta={live ? "live" : "reconnecting"} stale={page.data ? [page] : []}>
      {events.length === 0 && page.isError ? (
        <Problem what="The Tape" query={page} />
      ) : events.length === 0 && page.isPending ? (
        <Waiting />
      ) : events.length === 0 ? (
        <Quiet>No events yet</Quiet>
      ) : (
        <FitList
          label="Recent events, newest first"
          items={events}
          cap={lines}
          countRest={false}
          line={(event: TapeEvent) => (
            <li className="wall-line" key={event.seq}>
              <time className="wall-time" dateTime={new Date(event.ts).toISOString()}>
                {shortTime(event.ts, hour24)}
              </time>
              <span className="wall-grow wall-kind" title={event.kind}>
                {plainKind(event.kind, true)}
              </span>
              <span className="wall-side" title={event.actor}>
                {plainActor(event.actor)}
              </span>
            </li>
          )}
        />
      )}
    </Tile>
  );
});
