import "./memory.css";
import { memo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import { errorDetail } from "../../api";
import { formatEventTime, formatFullTime } from "../../lib/format";
import { confidenceLabel, confidenceLevel, confidenceWords } from "./lib/facts";
import { hideFactIds, safeNoteHref } from "./lib/markdown";

/** The small upper-case heading above a section. */
export function Label({ children, className = "", id }: { children: ReactNode; className?: string; id?: string }) {
  return (
    <div id={id} className={`font-mono text-[10.5px] uppercase tracking-[0.1em] text-text-faint ${className}`}>
      {children}
    </div>
  );
}

/** A time in the browser's locale, with the full date and time on hover. */
export function When({ ts, className = "" }: { ts: number; className?: string }) {
  return (
    <time dateTime={new Date(ts).toISOString()} title={formatFullTime(ts)} className={className}>
      {formatEventTime(ts)}
    </time>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="px-4 py-4 text-[13px] text-text-faint" role="status">
      Loading {what}…
    </p>
  );
}

/** A failed read: the server's own sentence, and a way to try again. */
export function QueryError({ error, onRetry, className = "" }: { error: unknown; onRetry: () => void; className?: string }) {
  return (
    <div className={`flex flex-wrap items-center gap-2 px-4 py-3 text-[13px] text-danger ${className}`} role="alert">
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{errorDetail(error)}</span>
      <button type="button" className="btn h-7 px-2.5 text-xs" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

/** How sure a fact is: four quiet bars, and the same in words. */
export function Confidence({ value }: { value: number }) {
  const level = confidenceLevel(value);
  return (
    <span className="inline-flex items-center gap-1.5" title={confidenceLabel(value)}>
      <span className="memory-bars" aria-hidden="true">
        {[1, 2, 3, 4].map((bar) => (
          <span key={bar} data-on={bar <= level ? "" : undefined} />
        ))}
      </span>
      <span>
        <span className="sr-only">Confidence: </span>
        {confidenceWords(value)}
      </span>
    </span>
  );
}

// A note's text is untrusted: an agent, or a file dropped into the Library, can
// have written it. react-markdown never renders raw HTML; on top of that only
// web and mail links are kept, and a picture is shown as a link, so that
// opening a note never fetches an address the note chose.
const COMPONENTS: Components = {
  a: ({ children, href }) =>
    href ? (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  img: ({ src, alt }) =>
    typeof src === "string" && src ? (
      <a href={src} target="_blank" rel="noreferrer noopener">
        {alt || "image"}
      </a>
    ) : (
      <span>{alt}</span>
    ),
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
};

const noteUrl = (url: string) => safeNoteHref(url) ?? "";

/** A note, rendered for reading. Comments, the fact ids among them, are not shown. */
export const NoteMarkdown = memo(function NoteMarkdown({ text }: { text: string }) {
  return (
    <div className="memory-md">
      <ReactMarkdown components={COMPONENTS} skipHtml urlTransform={noteUrl}>
        {hideFactIds(text)}
      </ReactMarkdown>
    </div>
  );
});
