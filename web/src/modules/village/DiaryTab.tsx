import { ChevronLeft, ChevronRight, Moon } from "lucide-react";
import { useMemo, useState } from "react";
import { errorDetail } from "../../api";
import type { AgentSummary } from "./api";
import { useDiary, useNow } from "./hooks";
import { dayHeading, entryCount, neighbours, newestFirst, pagesToShow, visibleDays } from "./lib/diary";
import { displayName } from "./lib/status";
import { Flag, Note } from "./parts";

/** What the agent wrote about each day, in its own words. Days run newest first, a page of them at a time. */
export function DiaryTab({ agent }: { agent: AgentSummary }) {
  const name = displayName(agent);
  const [picked, setPicked] = useState("");
  const [pages, setPages] = useState(1);
  const diary = useDiary(agent.id, picked);
  const now = useNow(60_000);

  const days = useMemo(() => newestFirst(diary.data?.days), [diary.data]);
  const day = diary.data?.day || picked;
  const shownPages = pagesToShow(days, day, pages);
  const { shown, hidden } = visibleDays(days, shownPages);
  const near = neighbours(days, day);
  const entries = diary.data?.entries ?? [];
  const switching = diary.isPlaceholderData;

  if (diary.isPending) return <p className="text-sm text-text-faint">Loading the diary…</p>;
  if (diary.isError && !diary.data) {
    return <Note tone="bad">{errorDetail(diary.error)} Only the owner can read an agent's diary: sign in as the owner, then reload the page.</Note>;
  }
  if (days.length === 0) {
    return (
      <section className="village-section">
        <h2>Diary</h2>
        <p className="text-sm text-text-muted">
          {name} has not written in its diary yet. It writes as its days go by, and a night's dream is added after it sleeps. Come back after its first night.
        </p>
      </section>
    );
  }

  return (
    <div className="village-diary">
      <nav className="village-days" aria-label={`${name}'s diary days`}>
        <ol>
          {shown.map((item) => (
            <li key={item}>
              <button type="button" aria-current={item === day ? "date" : undefined} onClick={() => setPicked(item)}>
                {dayHeading(item, now)}
              </button>
            </li>
          ))}
        </ol>
        {hidden > 0 ? (
          <button type="button" className="btn h-7 px-2.5 text-xs" onClick={() => setPages(shownPages + 1)}>
            Show older days ({hidden} more)
          </button>
        ) : null}
      </nav>

      <section className="village-section village-diary-day" aria-labelledby="village-diary-heading" aria-busy={switching}>
        <h2 id="village-diary-heading" className="flex flex-wrap items-center justify-between gap-2">
          <span>{dayHeading(day, now)}</span>
          <span className="flex items-center gap-1 normal-case tracking-normal">
            <button type="button" className="icon-btn" disabled={!near.newer} onClick={() => near.newer && setPicked(near.newer)} aria-label="Newer day" title="Newer day">
              <ChevronLeft size={15} aria-hidden="true" />
            </button>
            <button type="button" className="icon-btn" disabled={!near.older} onClick={() => near.older && setPicked(near.older)} aria-label="Older day" title="Older day">
              <ChevronRight size={15} aria-hidden="true" />
            </button>
          </span>
        </h2>
        {diary.isError ? <Note tone="bad">{errorDetail(diary.error)} The days listed are the last ones read. Reload the page to try again.</Note> : null}
        {switching ? <p className="text-sm text-text-faint">Loading that day…</p> : null}
        {!switching && entries.length === 0 ? (
          <p className="text-sm text-text-muted">Nothing was written on this day. Pick another from the list.</p>
        ) : null}
        {!switching && entries.length > 0 ? (
          <>
            <p className="mb-3 text-xs text-text-faint">
              {entryCount(entries)}, in the order they were written. These are {name}'s own words, shown as written.
              {entries.some((entry) => entry.dream) ? " A dream is marked as one. The night does not use dreams when it sorts memory." : ""}
            </p>
            <ol className="flex flex-col gap-3">
              {entries.map((entry, index) => (
                <li key={`${entry.time}-${index}`} className="village-entry" data-dream={entry.dream ? "true" : undefined}>
                  <div className="village-entry-head">
                    <time className="font-mono text-[11px] text-text-faint">{entry.time}</time>
                    {entry.dream ? (
                      <Flag tone="dream">
                        <Moon size={11} aria-hidden="true" />A dream
                      </Flag>
                    ) : null}
                  </div>
                  <p className="village-entry-text">{entry.text}</p>
                </li>
              ))}
            </ol>
          </>
        ) : null}
      </section>
    </div>
  );
}
