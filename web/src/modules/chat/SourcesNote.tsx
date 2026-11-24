import { ChevronRight, Globe } from "lucide-react";
import { useId, useState } from "react";
import type { WebUsed } from "./api";
import { webNote } from "./lib/web";
import type { SourceLine } from "./lib/web";

function Source({ line }: { line: SourceLine }) {
  return (
    <li className="flex min-w-0 flex-wrap items-baseline gap-x-2">
      {line.href ? (
        <a href={line.href} target="_blank" rel="noopener noreferrer" className="min-w-0 break-words text-text-muted underline underline-offset-2 hover:text-text [overflow-wrap:anywhere]">
          {line.title}
        </a>
      ) : (
        <span className="min-w-0 break-words text-text-muted [overflow-wrap:anywhere]">{line.title}</span>
      )}
      {line.host ? <span className="font-mono text-[10px]">{line.host}</span> : null}
    </li>
  );
}

/**
 * Under a reply: the pages it read and the search results it was shown, or
 * why it looked nothing up. The reply keeps only titles and addresses.
 */
export function SourcesNote({ web }: { web: WebUsed | null | undefined }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const note = webNote(web);
  if (!note) return null;
  if (note.kind === "skipped") return <p className="break-words text-[11px] text-text-faint">{note.reason}</p>;
  return (
    <div className="flex min-w-0 flex-col gap-1 text-[11px] text-text-faint">
      <button
        type="button"
        className="-ml-1 inline-flex w-fit max-w-full items-center gap-1 rounded px-1 py-0.5 text-text-faint hover:text-text-muted"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((was) => !was)}
      >
        <ChevronRight size={12} className={`flex-none transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
        <Globe size={12} className="flex-none" aria-hidden="true" />
        {note.label}
      </button>
      {open ? (
        <div id={listId} className="flex min-w-0 flex-col gap-1.5 border-l border-border pl-3">
          {note.read.length > 0 ? (
            <ul aria-label="Pages read" className="flex min-w-0 flex-col gap-1">
              {note.read.map((line, at) => (
                <Source key={`r${at}`} line={line} />
              ))}
            </ul>
          ) : null}
          {note.seen.length > 0 ? (
            <>
              {note.read.length > 0 ? <p>Also in the search results, not read:</p> : null}
              <ul aria-label="Search results not read" className="flex min-w-0 flex-col gap-1">
                {note.seen.map((line, at) => (
                  <Source key={`s${at}`} line={line} />
                ))}
              </ul>
            </>
          ) : null}
          {note.detail ? <p>{note.detail}.</p> : null}
        </div>
      ) : null}
    </div>
  );
}
