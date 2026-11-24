import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleSlash, Pickaxe } from "lucide-react";
import { useId, useState } from "react";
import { errorDetail } from "../../api";
import { Switch } from "../../components/Switch";
import { formatCount } from "../../lib/format";
import { formatHash } from "../../lib/route";
import { memoryApi } from "./api";
import type { Mining, Source, Tally } from "./api";
import { refreshAfterDecision, useSourceSwitch, useSources } from "./hooks";
import {
  benchLine,
  breakdown,
  describeMine,
  miningHeadline,
  modelPlace,
  outcomesLine,
  sectionsFor,
  sourceLabels,
  sourceState,
  tallyFromCounts,
  tallySentence,
  thresholdScale,
  waitingWords,
} from "./lib/mining";
import type { MineOutcome, SettingsSection } from "./lib/mining";
import { Label, Loading, QueryError, When } from "./parts";

const SETTINGS: Record<SettingsSection, { href: string; label: string }> = {
  memory: { href: formatHash({ screen: "settings", sub: "memory" }), label: "Settings, Memory" },
  models: { href: formatHash({ screen: "settings", sub: "models" }), label: "Settings, Models" },
};

function SettingsLink({ section }: { section: SettingsSection }) {
  return (
    <a href={SETTINGS[section].href} className="whitespace-nowrap rounded-sm text-accent underline underline-offset-2">
      {SETTINGS[section].label}
    </a>
  );
}

/** Which pass is running: every source, or one. */
type Running = "all" | string | null;
type Outcomes = Record<string, MineOutcome>;

/**
 * What JimmyHQ is learning from, what it is not, and why: whether mining is
 * working, where the two thresholds sit, and one row per source.
 */
export function SourcesPanel() {
  const client = useQueryClient();
  const query = useSources();
  const switchSource = useSourceSwitch();
  const [running, setRunning] = useState<Running>(null);
  const [outcomes, setOutcomes] = useState<Outcomes>({});
  const headingId = useId();

  const data = query.data;
  const labels = sourceLabels(data?.sources);

  const mine = async (source?: Source) => {
    if (running !== null) return;
    const key = source?.id ?? "all";
    setRunning(key);
    setOutcomes((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    try {
      const result = await memoryApi.mine(source?.id);
      const outcome = describeMine(result, labels);
      const perSource: Outcomes = {};
      if (!source && result.ran) {
        // Each source that read something also says what it did, in its own row.
        for (const [id, counts] of Object.entries(result.sources ?? {})) {
          perSource[id] = { ok: !counts.error, line: tallySentence(tallyFromCounts(counts)), errors: counts.error ? [{ source: labels[id] ?? id, message: counts.error }] : [] };
        }
      }
      setOutcomes((current) => ({ ...current, ...perSource, [key]: outcome }));
    } catch (error) {
      setOutcomes((current) => ({ ...current, [key]: { ok: false, line: errorDetail(error), errors: [] } }));
    } finally {
      setRunning(null);
      // A pass can save facts, change notes and file Bench cards.
      refreshAfterDecision(client);
    }
  };

  return (
    <section aria-labelledby={headingId} className="@container rounded-lg border border-border bg-surface">
      <div className="px-3 pt-2.5">
        <Label id={headingId}>Learning from what happens</Label>
      </div>
      {query.isError && !data ? (
        <QueryError error={query.error} onRetry={() => void query.refetch()} className="!px-3" />
      ) : !data ? (
        <Loading what="what is being learned from" />
      ) : (
        <>
          {query.isError ? <QueryError error={query.error} onRetry={() => void query.refetch()} className="!px-3" /> : null}
          <Overall mining={data.mining} running={running} outcome={outcomes.all} onMine={() => void mine()} />
          {data.sources.length === 0 ? (
            <p className="border-t border-border px-3 py-3 text-xs text-text-muted">No source is registered, so there is nothing to learn from.</p>
          ) : (
            <ul aria-label="Sources">
              {data.sources.map((source) => (
                <SourceRow
                  key={source.id}
                  source={source}
                  mining={data.mining}
                  running={running}
                  outcome={outcomes[source.id]}
                  onSwitch={(on) => void switchSource(source, on)}
                  onMine={() => void mine(source)}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function Outcome({ outcome }: { outcome: MineOutcome | undefined }) {
  if (!outcome) return null;
  return (
    <div className={`mt-2 text-xs leading-snug [overflow-wrap:anywhere] ${outcome.ok ? "text-text" : "text-danger"}`} role={outcome.ok ? "status" : "alert"}>
      <p>{outcome.line}</p>
      {outcome.errors.map((item) => (
        <p key={item.source} className="mt-0.5 text-danger">
          {item.source}: {item.message}
        </p>
      ))}
    </div>
  );
}

function Overall({ mining, running, outcome, onMine }: { mining: Mining; running: Running; outcome: MineOutcome | undefined; onMine: () => void }) {
  const sections = sectionsFor(mining.reason);
  const place = modelPlace(mining.model);
  return (
    <div className="px-3 pb-3 pt-2">
      <p className="flex items-center gap-1.5 text-[13px] font-medium text-text">
        {mining.working ? (
          <CircleCheck size={14} className="flex-none text-success" aria-hidden="true" />
        ) : (
          <CircleAlert size={14} className="flex-none text-warning" aria-hidden="true" />
        )}
        {miningHeadline(mining)}
      </p>
      {mining.thresholds_note ? (
        <p className="mt-1 text-xs leading-snug text-warning [overflow-wrap:anywhere]">{mining.thresholds_note}</p>
      ) : null}
      {mining.reason ? (
        <p className="mt-1 text-xs leading-snug text-text-muted [overflow-wrap:anywhere]">
          {mining.reason}{" "}
          {sections.map((section) => (
            <span key={section}>
              <SettingsLink section={section} />{" "}
            </span>
          ))}
        </p>
      ) : null}

      <dl className="mt-2 flex flex-col gap-1 text-xs">
        <div className="flex gap-2">
          <dt className="w-16 flex-none text-text-faint">Model</dt>
          <dd className="min-w-0 text-text-muted">
            {mining.model.name ? <span className="break-all font-mono text-[11.5px] text-text">{mining.model.name}</span> : null}
            {mining.model.name && place ? ", " : null}
            <span className={mining.model.is_local === false ? "text-warning" : undefined}>{place}</span>
            <span className="block text-text-faint">
              The <span className="font-mono text-[11px]">{mining.model.role}</span> role. {mining.model.local_only ? "Only a model on your own machines may be asked." : "A hosted model may be asked."}
            </span>
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-16 flex-none text-text-faint">Last ran</dt>
          <dd className="min-w-0 text-text-muted">{mining.last_run_ms ? <When ts={mining.last_run_ms} className="text-text" /> : "not yet"}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-16 flex-none text-text-faint">Next run</dt>
          <dd className="min-w-0 text-text-muted">
            {mining.next_run_ms ? mining.next_run_ms <= Date.now() ? "any moment now" : <When ts={mining.next_run_ms} className="text-text" /> : "not scheduled"}
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-16 flex-none text-text-faint">Bench</dt>
          <dd className="min-w-0 text-text-muted">{benchLine(mining)}</dd>
        </div>
      </dl>

      <Thresholds mining={mining} />

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <button type="button" className="btn h-7 px-2.5 text-xs" disabled={running !== null} onClick={onMine} aria-label="Mine now, from every source that is on">
          <Pickaxe size={13} aria-hidden="true" />
          {running === "all" ? "Mining…" : "Mine now"}
        </button>
        <span className="text-[11.5px] text-text-faint">One pass over every source that is on.</span>
      </div>
      <Outcome outcome={outcome} />
    </div>
  );
}

const BAND_CLASS: Record<string, string> = {
  ignored: "bg-border",
  bench: "bg-warning/60",
  saved: "bg-success/70",
};

/** The two thresholds as a small labelled scale from 0 to 1. The whole of it links to the settings. */
function Thresholds({ mining }: { mining: Mining }) {
  const scale = thresholdScale(mining.ignore_below, mining.auto_save_above);
  return (
    <a
      href={formatHash({ screen: "settings", sub: "memory/memory.auto_save_above" })}
      className="mt-2.5 block rounded-md border border-border bg-bg px-2.5 py-2 hover:border-sage"
      aria-label={`Confidence thresholds: ${scale.words} Change them under Settings, Memory.`}
    >
      <span className="flex items-baseline justify-between gap-2 text-[11px] text-text-faint" aria-hidden="true">
        <span>How sure the model must be</span>
        <span className="text-accent underline underline-offset-2">Change</span>
      </span>
      <span className="mt-1.5 flex h-2 w-full overflow-hidden rounded-full" aria-hidden="true">
        {scale.bands.map((band) => (band.percent > 0 ? <span key={band.id} className={BAND_CLASS[band.id]} style={{ width: `${band.percent}%` }} /> : null))}
      </span>
      <span className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] leading-snug text-text-muted" aria-hidden="true">
        {scale.bands.map((band) => (
          <span key={band.id} className="inline-flex items-center gap-1">
            <span className={`inline-block size-2 flex-none rounded-sm ${BAND_CLASS[band.id]}`} />
            {band.percent > 0 ? (
              <>
                {band.label}{" "}
                <span className="font-mono text-[11px] text-text-faint">
                  {band.id === "ignored" ? `below ${band.to}` : band.id === "saved" ? `from ${band.from}` : `${band.from} to ${band.to}`}
                </span>
              </>
            ) : (
              <span className="text-text-faint">{band.id === "ignored" ? "Nothing ignored" : band.id === "saved" ? "Nothing saved without you" : "Nothing sent to the Bench"}</span>
            )}
          </span>
        ))}
      </span>
    </a>
  );
}

function TallyLines({ title, tally }: { title: string; tally: Tally }) {
  const b = breakdown(tally);
  return (
    <div className="min-w-0 rounded-md border border-border bg-bg px-2.5 py-1.5">
      <dt className="font-mono text-[10px] uppercase tracking-[0.08em] text-text-faint">{title}</dt>
      <dd className="mt-0.5 text-[11.5px] leading-snug text-text-muted [overflow-wrap:anywhere]">
        {b.nothing ? (
          "Nothing read yet."
        ) : (
          <>
            <span className="block">
              <span className="text-text">{formatCount(b.read)}</span> read, <span className="text-text">{formatCount(b.proposed)}</span> proposed
            </span>
            {b.proposed > 0 || b.unaccounted !== 0 ? <span className="block">{outcomesLine(b)}</span> : null}
            {b.skipped > 0 ? (
              <span className="block">
                {formatCount(b.skipped)} passed over{b.skippedBy.length ? `: ${b.skippedBy.map((item) => `${item.words} ${item.n}`).join(", ")}` : ""}
              </span>
            ) : null}
          </>
        )}
      </dd>
    </div>
  );
}

type SourceRowProps = {
  source: Source;
  mining: Mining;
  running: Running;
  outcome: MineOutcome | undefined;
  onSwitch: (on: boolean) => void;
  onMine: () => void;
};

function SourceRow({ source, mining, running, outcome, onSwitch, onMine }: SourceRowProps) {
  const labelId = useId();
  const helpId = useId();
  const state = sourceState(source, mining);
  const waiting = waitingWords(source);
  return (
    <li className="border-t border-border px-3 py-2.5">
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span id={labelId} className="text-[13px] font-medium text-text">
              {source.label}
            </span>
            <span className={`inline-flex items-center gap-1 text-[11.5px] ${state.reading ? "text-success" : "text-text-faint"}`}>
              {state.reading ? <CircleCheck size={12} aria-hidden="true" /> : <CircleSlash size={12} aria-hidden="true" />}
              {state.words}
            </span>
          </p>
          <p id={helpId} className="mt-0.5 text-xs leading-snug text-text-muted">
            {source.description}
          </p>
        </div>
        <Switch checked={source.on} onChange={onSwitch} labelledBy={labelId} describedBy={helpId} />
      </div>

      <p className="mt-1.5 text-[11.5px] leading-snug text-text-muted">
        {source.last_read_ms ? (
          <>
            Last read <When ts={source.last_read_ms} className="text-text" />
          </>
        ) : (
          "Not read yet"
        )}
        {" · "}
        {formatCount(source.items_read)} {source.items_read === 1 ? "item" : "items"} read
        {waiting ? ` · ${waiting}` : ""}
      </p>

      <dl className="mt-1.5 grid grid-cols-1 gap-1.5 @sm:grid-cols-2">
        <TallyLines title="Last run" tally={source.last_run} />
        <TallyLines title="All time" tally={source.all_time} />
      </dl>

      {source.last_error ? (
        <p className="mt-1.5 text-xs leading-snug text-danger [overflow-wrap:anywhere]" role="alert">
          {source.last_error.message} <When ts={source.last_error.at_ms} className="text-text-faint" />
        </p>
      ) : null}

      <div className="mt-2">
        <button
          type="button"
          className="btn h-7 px-2.5 text-xs"
          disabled={running !== null || !source.on}
          title={source.on ? undefined : "Switch this source on to read it."}
          aria-label={`Mine now, from ${source.label} only`}
          onClick={onMine}
        >
          <Pickaxe size={13} aria-hidden="true" />
          {running === source.id ? "Mining…" : `Mine ${source.label} now`}
        </button>
      </div>
      <Outcome outcome={outcome} />
    </li>
  );
}
