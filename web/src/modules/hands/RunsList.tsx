import { formatHash } from "../../lib/route";
import type { Rule, Run } from "./api";
import { runCard, runCardWords, runLabel, runsOfAgent } from "./lib/group";
import { EFFECT_LABELS, levelShort, runProblem, runStatus } from "./lib/wording";
import { Failed, Loading, Pill, Tag, whenText } from "./ui";
import { useRunsQuery } from "./queries";

type Props = { agent: string; agentName: string; rules: Rule[] };

/** What the agent's integrations did lately: what, when, at which level and how it ended. Values are never shown: the server does not send them. */
export function RunsList({ agent, agentName, rules }: Props) {
  const runs = useRunsQuery(agent);
  const list = runsOfAgent(runs.data?.runs ?? [], agent);

  return (
    <section aria-labelledby="hands-runs-heading" className="flex flex-col gap-2">
      <h2 id="hands-runs-heading" className="text-[13px] font-semibold text-text">
        Recent activity
      </h2>
      {runs.isPending ? <Loading>Loading what {agentName} has done…</Loading> : null}
      {runs.isError ? <Failed what="Recent activity" error={runs.error} onRetry={() => void runs.refetch()} /> : null}
      {runs.isSuccess && list.length === 0 ? <p className="text-sm text-text-muted">Nothing yet. When {agentName} uses an integration, each use is listed here: what was done, when, and how it ended. What was sent is never shown.</p> : null}
      {list.length > 0 ? (
        <ul className="rounded-xl border border-border bg-surface">
          {list.map((run) => (
            <RunRow key={run.id} run={run} label={runLabel(run, rules)} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function RunRow({ run, label }: { run: Run; label: string }) {
  const status = runStatus(run.status);
  const problem = runProblem(run);
  const card = runCard(run);
  return (
    <li className="flex flex-col gap-1 border-b border-border/55 px-3 py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 break-words text-[13px] font-medium text-text">{label}</span>
        <span className="font-mono text-[11px] text-text-faint">{run.account}</span>
        <Tag>{EFFECT_LABELS[run.effect] ?? run.effect}</Tag>
        <Tag title="How far the agent was allowed to go">{levelShort(run.level)}</Tag>
        <span className="ml-auto flex items-center gap-2">
          <Pill tone={status.tone}>{status.text}</Pill>
        </span>
      </div>
      <p className="text-xs text-text-faint">
        <time dateTime={new Date(run.started_ms).toISOString()}>{whenText(run.started_ms)}</time>
        {card ? (
          <>
            {" · "}
            <a className="text-accent underline underline-offset-2" href={formatHash({ screen: "bench", sub: card })}>
              {runCardWords(run)}
            </a>
          </>
        ) : null}
      </p>
      {problem ? <p className="break-words text-xs text-text-muted">{problem}</p> : null}
    </li>
  );
}
