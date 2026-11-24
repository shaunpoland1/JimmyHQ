import { ChevronRight, Globe, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { isWorking, resultLines, rowLabel, settleOpen, summaryLine } from "./lib/web";
import type { StepRow } from "./lib/web";

type ActivityLineProps = {
  rows: readonly StepRow[];
  /** The answer has started: the steps fold into one line. */
  answering: boolean;
  /** Stop was pressed: nothing still open is going on any more. */
  stopping: boolean;
};

/** A mark for how one step stands. */
function Mark({ row }: { row: StepRow }) {
  if (row.state === "failed") return <TriangleAlert size={11} className="mt-0.5 flex-none text-warning" aria-hidden="true" />;
  const tone = row.state === "open" ? "bg-accent chat-writing" : row.state === "done" ? "bg-success" : "bg-sage";
  return <span className={`dot !size-1.5 mt-1 flex-none ${tone}`} aria-hidden="true" />;
}

function Results({ row }: { row: Extract<StepRow, { kind: "search" }> }) {
  const [shown, setShown] = useState(false);
  const listId = useId();
  if (row.state !== "done" || row.results.length === 0) return null;
  const lines = resultLines(row.results);
  return (
    <>
      <button
        type="button"
        className="ml-1 rounded px-1 underline underline-offset-2 hover:text-text-muted"
        aria-expanded={shown}
        aria-controls={listId}
        onClick={() => setShown((was) => !was)}
      >
        {shown ? "Hide titles" : "Show titles"}
      </button>
      {shown ? (
        <ul id={listId} className="mt-1 flex w-full min-w-0 basis-full flex-col gap-0.5 pl-1">
          {lines.map((line, at) => (
            <li key={at} className="flex min-w-0 flex-wrap items-baseline gap-x-2">
              {line.href ? (
                <a href={line.href} target="_blank" rel="noopener noreferrer" className="min-w-0 break-words text-text-muted underline underline-offset-2 hover:text-text [overflow-wrap:anywhere]">
                  {line.title}
                </a>
              ) : (
                <span className="min-w-0 break-words text-text-muted [overflow-wrap:anywhere]">{line.title}</span>
              )}
              {line.host ? <span className="font-mono text-[10px]">{line.host}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

/**
 * Above a reply that is being written: what web lookup is doing, a step at a
 * time. Once the answer starts it folds into one line that can be opened.
 */
export function ActivityLine({ rows, answering, stopping }: ActivityLineProps) {
  const listId = useId();
  const shown = stopping ? settleOpen(rows) : rows;
  const summary = summaryLine(shown);
  const working = isWorking(shown);
  // The owner's own choice of open or shut holds until the answer starts, then the line folds again.
  const [chosen, setChosen] = useState<boolean | null>(null);
  useEffect(() => setChosen(null), [answering]);
  if (summary === null) return null;
  const open = chosen ?? !answering;
  return (
    <div role="group" aria-label="Web lookup" className="flex min-w-0 flex-col gap-1 text-[11px] text-text-faint">
      <button
        type="button"
        className="-ml-1 inline-flex min-w-0 max-w-full items-center gap-1 self-start rounded px-1 py-0.5 text-text-faint hover:text-text-muted"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setChosen(!open)}
      >
        <ChevronRight size={12} className={`flex-none transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
        <Globe size={12} className="flex-none" aria-hidden="true" />
        <span className="min-w-0 truncate">{summary}</span>
        {working ? <span className="dot chat-writing !size-1.5 flex-none bg-accent" aria-hidden="true" /> : null}
      </button>
      <span className="sr-only" role="status">
        {working ? summary : ""}
      </span>
      {open ? (
        <ol id={listId} className="flex min-w-0 flex-col gap-1 border-l border-border pl-3">
          {shown.map((row, at) => (
            <li key={`${row.kind}-${row.id ?? "x"}-${at}`} className="flex min-w-0 flex-wrap items-start gap-x-1.5 gap-y-0.5">
              <Mark row={row} />
              <span className="min-w-0 max-w-full break-words [overflow-wrap:anywhere]">{rowLabel(row)}</span>
              {row.kind === "search" ? <Results row={row} /> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
