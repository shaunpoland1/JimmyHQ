import { Activity, Check, ChevronRight, Copy } from "lucide-react";
import { Fragment, useState } from "react";
import type { TapeEvent } from "../api";
import type { TapeView } from "../hooks/useTape";
import { formatEventTime } from "../lib/format";
import { HIDDEN, HIDDEN_WHY, capText, formatLocalTime, formatRelative, hoverHint, isLongText, kindInWords, payloadRows, redactedJson } from "../lib/tapeDetail";
import type { DetailRow } from "../lib/tapeDetail";
import { ERASED_NOTE, ERASED_TAG, describeTapeEvent } from "../lib/tapeRow";
import { EmptyState } from "./EmptyState";

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button type="button" className="ml-1 inline-flex flex-none items-center text-text-faint hover:text-text" aria-label={copied ? "Copied" : label} title={copied ? "Copied" : label} onClick={() => void copy()}>
      {copied ? <Check size={11} aria-hidden="true" /> : <Copy size={11} aria-hidden="true" />}
    </button>
  );
}

function DetailValue({ row }: { row: DetailRow }) {
  const [more, setMore] = useState(false);
  if (row.type === "hidden") return <span className="cursor-help italic text-text-faint underline decoration-dotted underline-offset-2" title={HIDDEN_WHY}>{HIDDEN}</span>;
  if (row.type === "id") {
    return (
      <span className="inline-flex items-center">
        <span className="font-mono" title={row.full}>{row.value}</span>
        <CopyButton text={row.full} label={`Copy ${row.label}`} />
      </span>
    );
  }
  const long = isLongText(row.value);
  return (
    <span className="whitespace-pre-wrap break-words">
      {long && !more ? capText(row.value) : row.value}
      {long ? (
        <button type="button" className="ml-1 text-accent underline-offset-2 hover:underline" aria-expanded={more} onClick={() => setMore((v) => !v)}>
          {more ? "show less" : "show more"}
        </button>
      ) : null}
    </span>
  );
}

function TapeDetail({ event, now }: { event: TapeEvent; now: number }) {
  const [raw, setRaw] = useState(false);
  const rows = payloadRows(event.payload);
  const erased = event.erased === true;
  return (
    <div className="px-3 pb-2 pl-8 text-[11px] text-text-muted">
      <dl className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-2 gap-y-0.5">
        <dt className="text-text-faint">When</dt>
        <dd className="break-words">
          {formatLocalTime(event.ts)} <span className="text-text-faint">({formatRelative(event.ts, now)})</span>
        </dd>
        <dt className="text-text-faint">Who</dt>
        <dd className="break-words">{event.actor}</dd>
        <dt className="text-text-faint">What</dt>
        <dd className="break-words">
          {kindInWords(event.kind)} <span className="font-mono text-[10px] text-text-faint">{event.kind}</span>
        </dd>
        <dt className="text-text-faint">Event</dt>
        <dd className="font-mono">#{event.seq}</dd>
        {event.subject ? (
          <>
            <dt className="text-text-faint">About</dt>
            <dd className="break-words">{event.subject}</dd>
          </>
        ) : null}
      </dl>
      {erased ? (
        <p className="mt-1.5">{ERASED_NOTE}</p>
      ) : (
        <>
          <div className="mt-2 border-t border-border/60 pt-1.5">
            {rows.length === 0 ? (
              <p className="text-text-faint">This event has no further details.</p>
            ) : (
              <dl className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-2 gap-y-0.5">
                {rows.map((row, i) => (
                  <Fragment key={i}>
                    <dt className="break-words text-text-faint" style={{ paddingLeft: `${row.depth * 0.75}rem` }}>{row.label}</dt>
                    <dd className="min-w-0">{row.type === "group" ? null : <DetailValue row={row} />}</dd>
                  </Fragment>
                ))}
              </dl>
            )}
          </div>
          {rows.length > 0 ? (
            <div className="mt-1.5">
              <button type="button" className="text-text-faint underline-offset-2 hover:text-text hover:underline" aria-expanded={raw} onClick={() => setRaw((v) => !v)}>
                {raw ? "Hide raw" : "Show raw"}
              </button>
              {raw ? <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-surface-2 p-1.5 font-mono text-[10px]">{redactedJson(event.payload)}</pre> : null}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function TapeRow({ event, now, open, onToggle }: { event: TapeEvent; now: number; open: boolean; onToggle: () => void }) {
  const view = describeTapeEvent(event);
  const title = hoverHint(event);
  return (
    <li className={`border-b border-border/60 text-xs ${view.erased ? "text-text-faint" : ""}`}>
      <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-surface-2/60" title={title} aria-expanded={open} onClick={onToggle}>
        <ChevronRight size={12} className={`flex-none transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
        <time dateTime={new Date(event.ts).toISOString()} className="flex-none font-mono text-[11px] tabular-nums text-text-faint">
          {formatEventTime(event.ts, now)}
        </time>
        <span className="chip min-w-0 flex-shrink truncate">{event.kind}</span>
        {view.erased ? <span className="flex-none rounded border border-dashed border-border px-1 font-mono text-[10px] lowercase tracking-wide text-text-faint">{ERASED_TAG}</span> : null}
        <span className="ml-auto min-w-0 truncate text-text-muted">{event.actor}</span>
      </button>
      {view.summary ? <p className="break-words px-3 pb-1.5 pl-8 text-[11px] text-text-muted">{view.summary}</p> : null}
      {open ? <TapeDetail event={event} now={now} /> : null}
    </li>
  );
}

/** Live list of recent tape events, newest first. */
export function TapePanel({ tape }: { tape: TapeView }) {
  const { events, loading, error, live } = tape;
  const [openSeq, setOpenSeq] = useState<number | null>(null);

  if (events.length === 0) {
    if (loading) return <p className="p-4 text-xs text-text-faint">Loading events…</p>;
    return (
      <EmptyState
        icon={Activity}
        title={error ? "Tape unavailable" : "No events yet"}
        description={
          error ? "The event log could not be loaded. It will retry on its own." : "Everything that happens in JimmyHQ is recorded here as it happens."
        }
        compact
      />
    );
  }

  const now = Date.now();
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-none items-center gap-1.5 border-b border-border px-3 py-1.5 text-[11px] text-text-faint">
        <span className={`dot ${live ? "bg-success" : "bg-sage"}`} aria-hidden="true" />
        <span>{live ? "Live" : "Reconnecting…"}</span>
        <span className="ml-auto font-mono">{events.length} shown</span>
      </div>
      <ol className="min-h-0 flex-1 overflow-y-auto" aria-label="Recent events, newest first">
        {events.map((event) => (
          <TapeRow key={event.seq} event={event} now={now} open={openSeq === event.seq} onToggle={() => setOpenSeq((cur) => (cur === event.seq ? null : event.seq))} />
        ))}
      </ol>
    </div>
  );
}
