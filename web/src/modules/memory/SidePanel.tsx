import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, CircleCheck, Plus, Search } from "lucide-react";
import { useEffect, useId, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, Ref } from "react";
import { useSettingsQuery } from "../../hooks/useSettings";
import { formatHash } from "../../lib/route";
import { ActivityPanel } from "./ActivityPanel";
import { memoryApi } from "./api";
import type { SearchResult } from "./api";
import { CandidatesPanel } from "./CandidatesPanel";
import { AddFactDialog } from "./dialogs";
import { FactItem } from "./FactItem";
import { useAllFacts, useFactActions, useNote } from "./hooks";
import { NO_FILTERS, filtersActive, flattenFacts, kindChoices } from "./lib/facts";
import type { FactFilters } from "./lib/facts";
import { nameOf } from "./lib/paths";
import { describeMethods, searchText, splitLine, splitPassages, trimmedLine, whyLabel, type PassageGroup } from "./lib/search";
import { Loading, QueryError } from "./parts";
import { StatusPanel } from "./StatusPanel";

export type SideTab = "facts" | "search" | "status";
const TABS: readonly { id: SideTab; label: string }[] = [
  { id: "facts", label: "Facts" },
  { id: "search", label: "Search" },
  { id: "status", label: "Status" },
];

export type SidePanelHandle = { focusSearch: () => void };

type SidePanelProps = {
  ref?: Ref<SidePanelHandle>;
  openPath: string | null;
  onOpenNote: (path: string) => void;
  /** Back to the note, below the width where this column sits beside it. */
  onBack: () => void;
};

const SETTINGS_MODELS = formatHash({ screen: "settings", sub: "models" });
const SETTINGS_MEMORY = formatHash({ screen: "settings", sub: "memory/memory.embed_local_only" });
const FILTER_DELAY_MS = 300;

/** The right column: search, the facts of the open note or of the whole Library, and status. */
export function SidePanel({ ref, openPath, onOpenNote, onBack }: SidePanelProps) {
  const [tab, setTab] = useState<SideTab>("facts");
  const [typed, setTyped] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const tabsId = useId();

  const search = useMutation({ mutationFn: (q: string) => memoryApi.search(q) });

  useImperativeHandle(ref, () => ({ focusSearch: () => searchBox.current?.focus() }), []);

  const runSearch = () => {
    const q = searchText(typed);
    if (q === null) return;
    setAsked(q);
    setTab("search");
    search.mutate(q);
  };

  const onTabKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const index = TABS.findIndex((item) => item.id === tab);
    const target = event.key === "ArrowRight" ? index + 1 : event.key === "ArrowLeft" ? index - 1 : event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : null;
    if (target === null) return;
    event.preventDefault();
    const next = TABS[(target + TABS.length) % TABS.length];
    if (!next) return;
    setTab(next.id);
    document.getElementById(`${tabsId}-${next.id}`)?.focus();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none flex-col gap-2 border-b border-border px-2.5 py-2">
        <form
          role="search"
          className="flex items-center gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            runSearch();
          }}
        >
          <button type="button" className="icon-btn @5xl:hidden" aria-label="Back to the note" title="Back" onClick={onBack}>
            <ArrowLeft size={16} aria-hidden="true" />
          </button>
          <div className="relative min-w-0 flex-1">
            <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-faint" aria-hidden="true" />
            <input
              ref={searchBox}
              type="search"
              className="input h-7 pl-7 pr-8 text-xs"
              placeholder="Search memory"
              aria-label="Search memory"
              aria-keyshortcuts="/"
              maxLength={4000}
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
                // Emptied, by hand or with the box's own clear button: the old results go too.
                if (event.target.value === "" && asked !== null) {
                  setAsked(null);
                  search.reset();
                }
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape" && typed === "") event.currentTarget.blur();
              }}
            />
            <kbd className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 rounded border border-border bg-bg px-1 font-mono text-[10px] text-text-faint" aria-hidden="true">
              /
            </kbd>
          </div>
          <button type="submit" className="btn h-7 px-2.5 text-xs" disabled={searchText(typed) === null || search.isPending}>
            Search
          </button>
        </form>
        <div role="tablist" aria-label="Memory panels" onKeyDown={onTabKey} className="inline-flex gap-0.5 self-start rounded-[7px] border border-border bg-surface p-0.5">
          {TABS.map((item) => {
            const selected = item.id === tab;
            return (
              <button
                key={item.id}
                id={`${tabsId}-${item.id}`}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`${tabsId}-panel`}
                tabIndex={selected ? 0 : -1}
                onClick={() => setTab(item.id)}
                className={`flex h-6 items-center whitespace-nowrap rounded-[5px] px-2.5 text-[12.5px] -outline-offset-1 ${
                  selected ? "bg-surface-2 font-semibold text-text shadow-[inset_0_0_0_1px_var(--border)]" : "text-text-muted hover:text-text"
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </div>
      </div>

      <div id={`${tabsId}-panel`} role="tabpanel" aria-labelledby={`${tabsId}-${tab}`} className="min-h-0 flex-1 overflow-y-auto">
        {tab === "facts" ? <FactsTab openPath={openPath} onOpenNote={onOpenNote} /> : null}
        {tab === "search" ? (
          <SearchResults
            asked={asked}
            pending={search.isPending}
            error={search.isError ? search.error : null}
            result={search.data}
            onRetry={() => (asked ? search.mutate(asked) : undefined)}
            onOpenNote={onOpenNote}
          />
        ) : null}
        {tab === "status" ? <StatusPanel /> : null}
      </div>
    </div>
  );
}

// ---- facts ----

function FactsTab({ openPath, onOpenNote }: { openPath: string | null; onOpenNote: (path: string) => void }) {
  const actions = useFactActions();
  const settings = useSettingsQuery();
  const note = useNote(openPath);
  // Until the owner chooses, a note's own facts are listed while one is open.
  const [chosen, setScope] = useState<"note" | "all" | null>(null);
  const scope = chosen ?? (openPath ? "note" : "all");
  const [filters, setFilters] = useState<FactFilters>(NO_FILTERS);
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const showAll = scope === "all" || openPath === null;
  const all = useAllFacts(filters, showAll);

  // The text filter is sent once typing pauses.
  useEffect(() => {
    const timer = setTimeout(() => setFilters((current) => (current.q === q ? current : { ...current, q })), FILTER_DELAY_MS);
    return () => clearTimeout(timer);
  }, [q]);

  const facts = useMemo(() => flattenFacts(all.data), [all.data]);
  const kindSetting = settings.data?.settings.find((setting) => setting.key === "memory.kind_notes")?.value;
  const inUse = useMemo(() => [...(note.data?.facts ?? []), ...facts].map((fact) => fact.kind), [note.data, facts]);
  const kinds = useMemo(() => kindChoices(kindSetting, inUse), [kindSetting, inUse]);

  return (
    <div className="flex flex-col gap-3 p-2.5">
      <CandidatesPanel />

      <section aria-label="Facts" className="rounded-lg border border-border bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
          <div className="inline-flex gap-0.5 rounded-[7px] border border-border bg-bg p-0.5" role="group" aria-label="Which facts to list">
            {(
              [
                { id: "note", label: "This note" },
                { id: "all", label: "Whole library" },
              ] as const
            ).map((item) => {
              const pressed = item.id === "all" ? showAll : !showAll;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={pressed}
                  disabled={item.id === "note" && openPath === null}
                  onClick={() => setScope(item.id)}
                  className={`h-6 rounded-[5px] px-2 text-xs -outline-offset-1 ${pressed ? "bg-surface font-semibold text-text shadow-[inset_0_0_0_1px_var(--border)]" : "text-text-muted hover:text-text"}`}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
          <button type="button" className="btn ml-auto h-7 px-2 text-xs" onClick={() => setAdding(true)}>
            <Plus size={13} aria-hidden="true" />
            Add a fact
          </button>
        </div>

        {showAll ? (
          <>
            <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2">
              <input
                type="search"
                className="input h-7 min-w-[7rem] flex-1 text-xs"
                placeholder="Text contains"
                aria-label="Only facts whose text contains"
                maxLength={200}
                value={q}
                onChange={(event) => setQ(event.target.value)}
              />
              <select
                className="input h-7 w-auto max-w-[8rem] py-0 pl-2 pr-1 text-xs"
                aria-label="Only facts of this kind"
                value={filters.kind}
                onChange={(event) => setFilters({ ...filters, kind: event.target.value })}
              >
                <option value="">Any kind</option>
                {kinds.map((kind) => (
                  <option key={kind} value={kind}>
                    {kind}
                  </option>
                ))}
              </select>
              <select
                className="input h-7 w-auto py-0 pl-2 pr-1 text-xs"
                aria-label="Only confirmed or unconfirmed facts"
                value={filters.confirmed}
                onChange={(event) => setFilters({ ...filters, confirmed: event.target.value === "yes" ? "yes" : event.target.value === "no" ? "no" : "any" })}
              >
                <option value="any">Confirmed or not</option>
                <option value="yes">Confirmed</option>
                <option value="no">Not confirmed</option>
              </select>
            </div>
            {all.isError && !all.data ? (
              <QueryError error={all.error} onRetry={() => void all.refetch()} />
            ) : !all.data ? (
              <Loading what="facts" />
            ) : facts.length === 0 ? (
              <p className="px-3 py-4 text-[13px] text-text-faint">
                {filtersActive({ ...filters, q })
                  ? "No fact matches these filters."
                  : "Nothing is remembered yet. Add a fact, or type a line that starts with a dash in any note."}
              </p>
            ) : (
              <>
                {all.isError ? <QueryError error={all.error} onRetry={() => void all.refetch()} /> : null}
                <ul aria-label="Facts in the whole library" className={all.isPlaceholderData ? "opacity-60" : ""}>
                  {facts.map((fact) => (
                    <FactItem key={fact.id} fact={fact} kinds={kinds} actions={actions} onOpenNote={onOpenNote} />
                  ))}
                </ul>
                {all.hasNextPage ? (
                  <div className="border-t border-border px-3 py-2">
                    <button type="button" className="btn h-7 w-full text-xs" disabled={all.isFetchingNextPage} onClick={() => void all.fetchNextPage()}>
                      {all.isFetchingNextPage ? "Loading…" : "Show more"}
                    </button>
                  </div>
                ) : null}
              </>
            )}
          </>
        ) : note.isError && !note.data ? (
          <QueryError error={note.error} onRetry={() => void note.refetch()} />
        ) : !note.data ? (
          <Loading what="this note's facts" />
        ) : note.data.facts.length === 0 ? (
          <p className="px-3 py-4 text-[13px] text-text-faint">
            <span className="font-mono">{nameOf(note.data.path)}</span> holds no facts yet. A line that starts with a dash is one fact.
          </p>
        ) : (
          <ul aria-label={`Facts in ${note.data.path}`}>
            {note.data.facts.map((fact) => (
              <FactItem key={fact.id} fact={fact} kinds={kinds} actions={actions} />
            ))}
          </ul>
        )}
      </section>

      <ActivityPanel />

      {adding ? <AddFactDialog kinds={kinds} openPath={openPath} onClose={() => setAdding(false)} onOpenNote={onOpenNote} /> : null}
    </div>
  );
}

// ---- search ----

type SearchResultsProps = {
  asked: string | null;
  pending: boolean;
  error: unknown;
  result: SearchResult | undefined;
  onRetry: () => void;
  onOpenNote: (path: string) => void;
};

function SearchResults({ asked, pending, error, result, onRetry, onOpenNote }: SearchResultsProps) {
  if (asked === null) {
    return (
      <p className="px-4 py-4 text-[13px] text-text-faint">
        Type a question or a few words above and press Enter. Passages are found by their words and, when an embeddings model is set up, by their meaning.
      </p>
    );
  }
  if (pending) return <Loading what="matches" />;
  if (error) return <QueryError error={error} onRetry={onRetry} />;
  if (!result) return null;

  const split = splitPassages(result.passages);
  const methods = describeMethods(result);
  const trimmed = trimmedLine(result.trimmed.length);

  return (
    <div className="flex flex-col gap-3 p-2.5">
      <div className="rounded-lg border border-border bg-surface px-3 py-2.5 text-xs" role="status">
        <p className="font-medium text-text">{splitLine(split)}</p>
        <p className="mt-0.5 text-text-muted">
          {methods.line}
          {methods.model ? (
            <>
              {" "}
              Meaning by <span className="font-mono">{methods.model}</span>.
            </>
          ) : null}
        </p>
        {methods.reason ? (
          <p className="mt-1.5 border-t border-border pt-1.5 text-text-muted">
            {methods.reason}{" "}
            <a href={SETTINGS_MODELS} className="whitespace-nowrap text-accent underline underline-offset-2">
              Open Settings, Models
            </a>{" "}
            <a href={SETTINGS_MEMORY} className="whitespace-nowrap text-accent underline underline-offset-2">
              Settings, Memory
            </a>
          </p>
        ) : null}
        {trimmed ? <p className="mt-1 text-text-faint">{trimmed}</p> : null}
      </div>

      <PassageGroups groups={split.matches} label="Matches" onOpenNote={onOpenNote} />
      {split.related.length > 0 ? (
        <>
          <div className="px-1 pt-1">
            <h3 className="text-xs font-medium text-text">Related in meaning</h3>
            <p className="mt-0.5 text-[11.5px] leading-snug text-text-faint">
              These do not contain your words. They are the closest in meaning, so they may or may not be what you want.
            </p>
          </div>
          <PassageGroups groups={split.related} label="Related" onOpenNote={onOpenNote} />
        </>
      ) : null}
    </div>
  );
}

function PassageGroups({ groups, label, onOpenNote }: { groups: PassageGroup[]; label: string; onOpenNote: (path: string) => void }) {
  return (
    <>
      {groups.map((group) => (
        <section key={group.note} aria-label={`${label} in ${group.note}`} className="rounded-lg border border-border bg-surface">
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-t-lg border-b border-border px-3 py-1.5 text-left -outline-offset-2 hover:bg-surface-2"
            onClick={() => onOpenNote(group.note)}
            aria-label={`Open the note ${group.note}`}
          >
            <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium text-accent">{group.note}</span>
            <span className="flex-none text-[11px] text-text-faint">Open</span>
          </button>
          <ul>
            {group.passages.map((passage) => {
              const why = whyLabel(passage.why);
              return (
                <li key={passage.id} className="border-b border-border px-3 py-2 last:border-b-0">
                  <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-mono text-[10.5px] text-text-faint" title="Rank among all matches">
                      #{passage.rank}
                    </span>
                    {passage.heading ? <span className="min-w-0 truncate text-[11.5px] font-medium text-text-muted">{passage.heading}</span> : null}
                    <span className="chip ml-auto" title={why.title}>
                      {why.label}
                    </span>
                    {passage.confirmed ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-success" title="A fact you confirmed">
                        <CircleCheck size={12} aria-hidden="true" />
                        <span className="sr-only">Confirmed fact</span>
                      </span>
                    ) : null}
                  </div>
                  <p className="whitespace-pre-wrap text-[13px] leading-snug text-text [overflow-wrap:anywhere]">{passage.text}</p>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </>
  );
}
