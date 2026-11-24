import { RotateCw } from "lucide-react";
import { formatFullTime } from "../../lib/format";
import type { AppDetail } from "./api";
import { canRestart, healthWords, jobLine, startsInLastDay } from "./lib/health";
import { useAppHealth, useRestartApp } from "./queries";
import { ErrorCard, LoadingCard, Pill, SMALL_BTN, SectionHeading } from "./ui";

/** A full app's program: how it is, its jobs, its last lines, and Restart. */
export function HealthPanel({ app }: { app: AppDetail }) {
  const query = useAppHealth(app.id, app.tier === "full");
  const restart = useRestartApp();
  const offered = canRestart(app);
  const health = query.data;
  const words = health ? healthWords(health.state) : null;

  const button = offered ? (
    <button
      type="button"
      className={SMALL_BTN}
      disabled={restart.isPending || !health}
      onClick={() => health && restart.mutate({ app, was: health.state })}
      aria-label={`Restart ${app.name}`}
    >
      <RotateCw size={13} aria-hidden="true" />
      {restart.isPending ? "Restarting…" : "Restart"}
    </button>
  ) : null;

  return (
    <>
      <SectionHeading id="apps-detail-health" aside={button}>
        Its program
      </SectionHeading>
      {query.isPending ? (
        <LoadingCard>Asking how its program is…</LoadingCard>
      ) : query.isError || !health || !words ? (
        <ErrorCard what="How its program is" error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <section aria-labelledby="apps-detail-health" className="rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone={words.tone}>{words.label}</Pill>
          </div>
          <p className="mt-1.5 text-text-muted" role="status">
            {words.sentence}
          </p>
          <dl className="mt-2 grid gap-x-4 gap-y-1 @md:grid-cols-[9rem_1fr]">
            {health.startedAt !== null ? (
              <>
                <dt className="text-text-faint">Started</dt>
                <dd>{formatFullTime(health.startedAt)}</dd>
              </>
            ) : null}
            {health.lastBeat !== null ? (
              <>
                <dt className="text-text-faint">Last asked for work</dt>
                <dd>{formatFullTime(health.lastBeat)}</dd>
              </>
            ) : null}
            <dt className="text-text-faint">Starts in the last day</dt>
            <dd>{startsInLastDay(health.restarts, Date.now())}</dd>
            {health.lastExit && health.lastExit.at !== null ? (
              <>
                <dt className="text-text-faint">Last stopped</dt>
                <dd>
                  {formatFullTime(health.lastExit.at)}
                  {health.lastExit.code !== null ? `, with code ${health.lastExit.code}` : ""}
                </dd>
              </>
            ) : null}
          </dl>
          {health.jobs.length > 0 ? (
            <>
              <h3 className="mt-3 text-xs font-semibold text-text-faint">Its jobs</h3>
              <ul className="mt-1 flex flex-col gap-1">
                {health.jobs.map((job) => (
                  <li key={job.name} className="break-words">
                    <span className="font-mono text-xs">{job.name}</span>: {jobLine(job, formatFullTime)}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {health.log.length > 0 ? (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-text-muted">Its last {health.log.length} lines of output</summary>
              <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-bg px-2.5 py-2 font-mono text-[11px] leading-4">
                {health.log.join("\n")}
              </pre>
            </details>
          ) : null}
        </section>
      )}
    </>
  );
}
