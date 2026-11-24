import { SearchX } from "lucide-react";
import type { ReactNode } from "react";
import { markPieces } from "../lib/settingsSearch";
import type { Range, SearchOutcome, SearchResult } from "../lib/settingsSearch";

/** The id of the list the search box controls. */
export const RESULTS_ID = "settings-search-results";

/** The id of one result, for the search box to point at as the arrow keys move. */
export function resultId(index: number): string {
  return `settings-search-result-${index}`;
}

function Marked({ text, ranges }: { text: string; ranges: readonly Range[] }): ReactNode {
  return markPieces(text, ranges).map((piece, index) =>
    piece.marked ? (
      <mark key={index} className="rounded-sm bg-accent/20 text-inherit">
        {piece.text}
      </mark>
    ) : (
      <span key={index}>{piece.text}</span>
    ),
  );
}

const SNIPPET_LEAD = { help: "", choice: "Choice: ", key: "Key: ", keyword: "Also found as: " } as const;

function where(result: SearchResult): string {
  const { entry } = result;
  if (entry.kind === "page") return entry.groupLabel ? `Page, under ${entry.groupLabel}` : "Page";
  if (entry.kind === "part") return `Part of ${entry.pageLabel}`;
  return entry.partLabel ? `${entry.pageLabel}, ${entry.partLabel}` : entry.pageLabel;
}

type SearchResultsProps = {
  query: string;
  outcome: SearchOutcome;
  /** The result the arrow keys point at; -1 for none. */
  active: number;
  onActive: (index: number) => void;
  /** Go to a result. The link's own href is the same place, for a new tab or a copied address. */
  onGo: (result: SearchResult) => void;
};

/** What the search box found, grouped by page, in place of the page itself. */
export function SearchResults({ query, outcome, active, onActive, onGo }: SearchResultsProps) {
  if (outcome.total === 0) {
    return (
      <div id={RESULTS_ID} className="flex flex-col items-center gap-2 rounded-xl border border-border bg-surface px-6 py-10 text-center">
        <SearchX size={22} strokeWidth={1.5} className="text-text-faint" aria-hidden="true" />
        <p className="text-sm font-medium text-text">No settings match “{query.trim()}”.</p>
        <p className="max-w-sm text-xs text-text-muted">
          Try fewer words, or a plainer one: “time zone”, “delete”, “limit”. The name of a page works too, such as Memory or Voice. Press Escape to clear the search.
        </p>
      </div>
    );
  }
  let index = -1;
  return (
    <div id={RESULTS_ID} role="listbox" aria-label="Search results" className="flex flex-col gap-5">
      {outcome.groups.map((group) => {
        const headingId = `settings-search-group-${group.pageId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
        return (
          <div key={group.pageId} role="group" aria-labelledby={headingId}>
            <h3 id={headingId} className="mb-2 flex flex-wrap items-baseline gap-x-2 text-sm font-semibold">
              {group.pageLabel}
              {group.groupLabel ? <span className="font-mono text-[10px] font-normal uppercase tracking-widest text-text-faint">{group.groupLabel}</span> : null}
            </h3>
            <div className="divide-y divide-border rounded-xl border border-border bg-surface">
              {group.results.map((result) => {
                index += 1;
                const at = index;
                const { entry, snippet } = result;
                return (
                  <a
                    key={entry.id}
                    id={resultId(at)}
                    role="option"
                    aria-selected={at === active}
                    href={entry.href}
                    onMouseMove={() => {
                      if (at !== active) onActive(at);
                    }}
                    onClick={(event) => {
                      // A modified click opens the link the browser's own way.
                      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
                      event.preventDefault();
                      onGo(result);
                    }}
                    className="block scroll-my-4 px-4 py-2.5 outline-none first:rounded-t-xl last:rounded-b-xl aria-selected:bg-accent/10 aria-selected:shadow-[inset_3px_0_0_var(--accent)]"
                  >
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <span className="text-[13px] font-medium text-text">
                        <Marked text={entry.label} ranges={result.labelRanges} />
                      </span>
                      {entry.advanced ? <span className="rounded bg-surface-2 px-1.5 py-px font-mono text-[10px] uppercase tracking-wide text-text-faint">Advanced</span> : null}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-text-faint">{where(result)}</span>
                    {snippet ? (
                      <span className={`mt-0.5 block break-words text-xs text-text-muted ${snippet.from === "key" ? "font-mono text-[11px]" : ""}`}>
                        {SNIPPET_LEAD[snippet.from]}
                        <Marked text={snippet.text} ranges={snippet.ranges} />
                      </span>
                    ) : null}
                  </a>
                );
              })}
            </div>
          </div>
        );
      })}
      {outcome.flat.length < outcome.total ? (
        <p className="text-xs text-text-muted">
          The best {outcome.flat.length} of {outcome.total} results are shown. Add a word to narrow them down.
        </p>
      ) : null}
    </div>
  );
}
