import { Search, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { errorDetail } from "../api";
import { EmptyState } from "../components/EmptyState";
import { useSettingActions, useSettingsQuery } from "../hooks/useSettings";
import { readAdvancedOpen, toggleAdvancedOpen, writeAdvancedOpen } from "../lib/settingsAdvanced";
import { NO_LAYOUT, blockHeadingId, buildNav, navPages, onThisPage, pageBlocks, resolveTarget, settingRowId, settingsHash } from "../lib/settingsNav";
import { buildSearchIndex, moveActive, queryWords, resultsAnnouncement, searchSettings } from "../lib/settingsSearch";
import type { SearchResult } from "../lib/settingsSearch";
import { RESULTS_ID, SearchResults, resultId } from "../settings/SearchResults";
import { SettingsBlock } from "../settings/SettingsBlock";
import { CUSTOM_SECTIONS, SEARCH_KEYWORDS, sectionExtra } from "../settings/customSections";

type SettingsScreenProps = {
  /**
   * What the URL hash names after `#/settings/`: a section (`appearance`), a
   * part of a page (`village_sleep`), or a section and one setting
   * (`memory/memory.ignore_below`).
   */
  sectionId: string | null;
};

/** How long a setting stays pointed out after a link brought the reader to it. */
const HIGHLIGHT_MS = 2600;

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The browser's own storage, or null where it is blocked (reading the property can itself throw). */
function browserStore(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** True when a key press belongs to what has the focus: a field, a list being edited, a menu. */
function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return target.closest("input, textarea, select, [role='textbox'], [role='combobox'], [role='listbox'], [role='menu']") !== null;
}

/** Settings, generated from the schema the server returns, in the order and groups it gives. */
export function SettingsScreen({ sectionId }: SettingsScreenProps) {
  const query = useSettingsQuery();
  const actions = useSettingActions();
  const data = query.data;

  const groups = useMemo(() => (data ? buildNav(data.sections, data.layout ?? NO_LAYOUT, CUSTOM_SECTIONS) : []), [data]);
  const target = useMemo(() => (data ? resolveTarget(sectionId, groups, data.settings) : null), [data, groups, sectionId]);
  const pageId = target?.page ?? null;
  const partId = target?.part ?? null;
  const settingKey = target?.setting ?? null;

  const [highlighted, setHighlighted] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opened = useRef(false);

  // Which blocks have their advanced settings open. Remembered in this browser;
  // where storage is blocked the choice simply lasts for this visit.
  const [advancedOpen, setAdvancedOpen] = useState<string[]>(() => readAdvancedOpen(browserStore()));
  const openAdvanced = useCallback((section: string, open: boolean) => {
    setAdvancedOpen((current) => {
      const next = toggleAdvancedOpen(current, section, open);
      writeAdvancedOpen(browserStore(), next);
      return next;
    });
  }, []);

  // Search across every page. While there is a query the results take the page's place.
  const [term, setTerm] = useState("");
  const [active, setActive] = useState(0);
  // Counts each result chosen, so choosing the place already open still scrolls to it.
  const [visit, setVisit] = useState(0);
  const searchBox = useRef<HTMLInputElement>(null);
  const searching = queryWords(term).length > 0;
  const searchIndex = useMemo(() => (data ? buildSearchIndex(groups, data.settings, SEARCH_KEYWORDS) : []), [data, groups]);
  const outcome = useMemo(() => searchSettings(searchIndex, term), [searchIndex, term]);

  const clearSearch = useCallback(() => {
    setTerm("");
    setActive(0);
  }, []);

  const go = useCallback(
    (result: SearchResult) => {
      clearSearch();
      setVisit((count) => count + 1);
      if (window.location.hash !== result.entry.href) window.location.hash = result.entry.href;
    },
    [clearSearch],
  );

  // "/" goes to the search box unless something is being typed into. Escape
  // clears a search before it can close the dialog Settings is shown in: that
  // listens on the document, so this one listens a step earlier, on the window.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && searchBox.current && searchBox.current.value !== "" && (event.target === searchBox.current || !typingIn(event.target))) {
        event.preventDefault();
        event.stopPropagation();
        clearSearch();
        searchBox.current.focus();
        return;
      }
      if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.repeat && !typingIn(event.target) && searchBox.current) {
        event.preventDefault();
        searchBox.current.focus();
        searchBox.current.select();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [clearSearch]);

  // Keep the result the arrow keys point at in view.
  useEffect(() => {
    if (searching) document.getElementById(resultId(active))?.scrollIntoView({ block: "nearest" });
  }, [active, searching]);

  const onSearchKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (!searching) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => moveActive(current, outcome.flat.length, event.key as "ArrowDown" | "ArrowUp"));
    } else if (event.key === "Enter") {
      const chosen = outcome.flat[active] ?? outcome.flat[0];
      if (chosen) {
        event.preventDefault();
        go(chosen);
      }
    }
  };

  // Bring the reader to what the link named: one setting, a part of the page,
  // or the top of a page just opened. Focus goes with it, so the keyboard and
  // a screen reader start from the same place the eye does.
  useEffect(() => {
    if (pageId === null || searching) return;
    const firstLook = !opened.current;
    opened.current = true;
    const behavior: ScrollBehavior = prefersReducedMotion() ? "auto" : "smooth";
    if (settingKey !== null) {
      const row = document.getElementById(settingRowId(settingKey));
      if (!row) return;
      row.scrollIntoView({ block: "center", behavior });
      row.focus({ preventScroll: true });
      setHighlighted(settingKey);
      const timer = window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS);
      return () => window.clearTimeout(timer);
    }
    setHighlighted(null);
    if (partId !== null) {
      const part = document.getElementById(blockHeadingId(partId));
      part?.scrollIntoView({ block: "start", behavior });
      part?.focus({ preventScroll: true });
    } else if (!firstLook) {
      // Another page, or the top of this one again.
      heading.current?.scrollIntoView({ block: "start" });
    }
    return undefined;
  }, [pageId, partId, settingKey, sectionId, searching, visit]);

  if (query.isPending) {
    return <p className="p-6 text-sm text-text-faint">Loading settings…</p>;
  }
  if (query.isError) {
    return (
      <EmptyState icon={SlidersHorizontal} title="Settings unavailable" description={errorDetail(query.error)} level={1}>
        <button type="button" className="btn" onClick={() => void query.refetch()}>
          Try again
        </button>
      </EmptyState>
    );
  }

  const current = navPages(groups).find((page) => page.id === pageId);
  if (!current) {
    return <EmptyState icon={SlidersHorizontal} title="Settings" description="There are no settings to show yet." level={1} />;
  }
  const Custom = CUSTOM_SECTIONS.find((section) => section.id === current.id)?.component;
  const Extra = sectionExtra(current.id);
  // A page with its own screen shows that first, then its plain settings under a heading.
  const blocks = pageBlocks(current, query.data.settings, Custom ? "More settings" : "");
  const jumps = Custom ? [] : onThisPage(blocks);

  // Container queries, not viewport breakpoints: the centre column's width
  // depends on whether the sidebar and the dock are open.
  return (
    <div className="@container w-full">
      <div className="flex w-full flex-col gap-5 p-4 @2xl:flex-row @2xl:gap-8 @2xl:p-6">
        <div className="flex flex-col gap-3 @2xl:sticky @2xl:top-6 @2xl:w-48 @2xl:flex-none @2xl:self-start">
          <div role="search" className="relative">
            <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-faint" />
            <input
              ref={searchBox}
              type="text"
              inputMode="search"
              enterKeyHint="search"
              autoComplete="off"
              spellCheck={false}
              role="combobox"
              aria-label="Search all settings"
              aria-keyshortcuts="/"
              aria-expanded={searching}
              aria-controls={searching ? RESULTS_ID : undefined}
              aria-autocomplete="list"
              aria-activedescendant={searching && outcome.flat.length > 0 ? resultId(Math.min(active, outcome.flat.length - 1)) : undefined}
              placeholder="Search settings"
              value={term}
              onChange={(event) => {
                setTerm(event.target.value);
                setActive(0);
              }}
              onKeyDown={onSearchKey}
              className="input w-full pl-8 pr-8"
            />
            {term ? (
              <button
                type="button"
                aria-label="Clear the search"
                onClick={() => {
                  clearSearch();
                  searchBox.current?.focus();
                }}
                className="absolute right-1.5 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded text-text-faint hover:text-text"
              >
                <X size={14} aria-hidden="true" />
              </button>
            ) : (
              <kbd aria-hidden="true" className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 rounded border border-border px-1 font-mono text-[10px] leading-4 text-text-faint @2xl:block">
                /
              </kbd>
            )}
            <p role="status" aria-live="polite" className="sr-only">
              {resultsAnnouncement(term, outcome)}
            </p>
          </div>
          <nav aria-label="Settings sections">
            {/* Narrow: one wrapping run of links with the group names set in it. Wide: a column, one block per group. */}
            <ul className="flex flex-wrap items-center gap-1 @2xl:flex-col @2xl:items-stretch @2xl:gap-4">
              {groups.map((group, index) => {
                const groupLabelId = `settings-group-${group.id}`;
                return (
                  <li key={group.id} className="contents @2xl:block">
                    {group.label ? (
                      <p
                        id={groupLabelId}
                        className={`px-2.5 font-mono text-[10px] uppercase tracking-widest text-text-faint @2xl:mb-1 @2xl:ml-0 ${index > 0 ? "ml-2" : ""}`}
                      >
                        {group.label}
                      </p>
                    ) : null}
                    <ul aria-labelledby={group.label ? groupLabelId : undefined} className="contents @2xl:flex @2xl:flex-col @2xl:gap-0.5">
                      {group.pages.map((page) => {
                        const selected = !searching && page.id === current.id;
                        return (
                          <li key={page.id}>
                            <a
                              href={settingsHash(page.id)}
                              onClick={clearSearch}
                              aria-current={selected ? "page" : undefined}
                              className={`block rounded-lg px-2.5 py-1.5 text-[13px] transition-colors ${
                                selected ? "bg-surface font-medium text-text shadow-sm ring-1 ring-border" : "text-text-muted hover:bg-surface/60 hover:text-text"
                              }`}
                            >
                              {page.label}
                            </a>
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                );
              })}
            </ul>
          </nav>
        </div>
        {searching ? (
          <section aria-labelledby="settings-search-heading" className="min-w-0 flex-1">
            <header className="mb-4">
              <h2 id="settings-search-heading" className="text-base font-semibold">
                Search results
              </h2>
              <p className="mt-0.5 text-[13px] text-text-muted">
                {outcome.total === 0 ? "Nothing found yet." : "Arrow keys move through the results, Enter opens one, Escape clears the search."}
              </p>
            </header>
            <SearchResults query={term} outcome={outcome} active={Math.min(active, outcome.flat.length - 1)} onActive={setActive} onGo={go} />
          </section>
        ) : (
          <section aria-labelledby="settings-section-heading" className="min-w-0 flex-1">
            <header className="mb-4">
              <h2 ref={heading} id="settings-section-heading" className="scroll-mt-6 text-base font-semibold">
                {current.label}
              </h2>
              {current.description ? <p className="mt-0.5 text-[13px] text-text-muted">{current.description}</p> : null}
            </header>
            {jumps.length > 0 ? (
              <nav aria-label="On this page" className="mb-5 rounded-xl border border-border bg-surface px-4 py-3">
                <p className="font-mono text-[10px] uppercase tracking-widest text-text-faint">On this page</p>
                <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                  {jumps.map((jump) => (
                    <li key={jump.id}>
                      <a
                        href={settingsHash(jump.id)}
                        aria-current={partId === jump.id ? "location" : undefined}
                        className="text-[13px] text-accent underline-offset-2 hover:underline aria-[current=location]:font-medium aria-[current=location]:underline"
                      >
                        {jump.label}
                      </a>
                      <span className="ml-1 font-mono text-[10px] text-text-faint" aria-label={`${jump.count} settings`}>
                        {jump.count}
                      </span>
                    </li>
                  ))}
                </ul>
              </nav>
            ) : null}
            {Extra ? (
              <div className="mb-3">
                <Extra />
              </div>
            ) : null}
            {Custom ? <Custom /> : null}
            {blocks.length === 0 && !Custom ? (
              <p className="rounded-xl border border-border bg-surface px-4 py-6 text-sm text-text-faint">This section has no settings yet.</p>
            ) : null}
            {blocks.map((block, index) => (
              <div key={block.id} className={index > 0 || Custom ? "mt-7" : ""}>
                <SettingsBlock
                  block={block}
                  advancedOpen={advancedOpen.includes(block.id)}
                  onAdvancedOpen={(open) => openAdvanced(block.id, open)}
                  revealKey={settingKey}
                  highlighted={highlighted}
                  onSave={(key, value) => void actions.save(key, value)}
                  onReset={(key, value) => void actions.reset(key, value)}
                />
              </div>
            ))}
          </section>
        )}
      </div>
    </div>
  );
}
