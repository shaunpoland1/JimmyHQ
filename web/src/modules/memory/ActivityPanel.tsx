import { useId, useMemo } from "react";
import { formatFullTime } from "../../lib/format";
import { formatHash } from "../../lib/route";
import { useActivity, useSources } from "./hooks";
import { activityFactText, activityNote, dayHeading, describeActivity, groupByDay, timeOfDay } from "./lib/activity";
import { sourceLabels } from "./lib/mining";
import { nameOf } from "./lib/paths";
import { Label, Loading, QueryError } from "./parts";

/**
 * What has happened in memory, newest first and grouped by day: facts saved,
 * changed and forgotten, notes changed, mining passes, and history erased. A
 * log to look things up in, so it stays quiet.
 */
export function ActivityPanel() {
  const query = useActivity();
  const sources = useSources();
  const labels = sourceLabels(sources.data?.sources);
  const headingId = useId();

  const groups = useMemo(() => groupByDay((query.data?.pages ?? []).flatMap((page) => page.activity)), [query.data]);
  const now = Date.now();

  return (
    <section aria-labelledby={headingId} className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-3 py-2">
        <Label id={headingId}>Activity</Label>
      </div>
      {query.isError && !query.data ? (
        <QueryError error={query.error} onRetry={() => void query.refetch()} className="!px-3" />
      ) : !query.data ? (
        <Loading what="the activity" />
      ) : groups.length === 0 ? (
        <p className="px-3 py-3 text-[13px] leading-snug text-text-faint">Nothing has happened in memory yet. Saved facts, changed notes and mining passes are listed here.</p>
      ) : (
        <>
          {query.isError ? <QueryError error={query.error} onRetry={() => void query.refetch()} className="!px-3" /> : null}
          {groups.map((group) => {
            const first = group.entries[0];
            return (
              <section key={group.day} aria-label={first ? dayHeading(first.ts, now) : group.day}>
                <h3 className="border-b border-border bg-bg px-3 py-1 text-[11px] font-medium text-text-faint">{first ? dayHeading(first.ts, now) : group.day}</h3>
                <ul>
                  {group.entries.map((entry) => {
                    const said = describeActivity(entry, labels);
                    const fact = activityFactText(entry);
                    const note = activityNote(entry);
                    return (
                      <li key={entry.id} className="flex gap-2.5 border-b border-border px-3 py-1.5 text-xs leading-snug last:border-b-0">
                        <time dateTime={new Date(entry.ts).toISOString()} title={formatFullTime(entry.ts)} className="w-11 flex-none pt-px font-mono text-[10.5px] text-text-faint">
                          {timeOfDay(entry.ts)}
                        </time>
                        <div className="min-w-0 flex-1">
                          <p className="text-text-muted [overflow-wrap:anywhere]">{said.line}</p>
                          {fact ? (
                            <p className={`mt-0.5 line-clamp-2 whitespace-pre-wrap [overflow-wrap:anywhere] ${fact.stored ? "text-text" : "italic text-text-faint"}`} title={fact.stored ? fact.text : undefined}>
                              {fact.text}
                            </p>
                          ) : null}
                          {said.more ? <p className="mt-0.5 text-text-faint [overflow-wrap:anywhere]">{said.more}</p> : null}
                          {note ? (
                            <a
                              href={formatHash({ screen: "memory", sub: note })}
                              className="mt-0.5 inline-block max-w-full truncate rounded-sm align-bottom font-mono text-[11px] text-accent hover:underline"
                              aria-label={`Open the note ${note}`}
                              title={note}
                            >
                              {nameOf(note)}
                            </a>
                          ) : entry.note ? (
                            <span className="mt-0.5 block truncate font-mono text-[11px] text-text-faint" title={entry.note}>
                              {nameOf(entry.note)}
                            </span>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
          {query.hasNextPage ? (
            <div className="border-t border-border px-3 py-2">
              <button type="button" className="btn h-7 w-full text-xs" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {query.isFetchingNextPage ? "Loading…" : "Show earlier"}
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
