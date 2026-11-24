import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, RefreshCw, Wrench } from "lucide-react";
import { useState } from "react";
import { errorDetail } from "../../api";
import { formatCount } from "../../lib/format";
import { formatHash } from "../../lib/route";
import { memoryApi } from "./api";
import type { ScanResult, Status } from "./api";
import { MEMORY_KEY, STATUS_KEY, useStatus } from "./hooks";
import { describeScan, vectorProgress } from "./lib/status";
import { Label, Loading, QueryError, When } from "./parts";
import { SourcesPanel } from "./SourcesPanel";

const SETTINGS_MODELS = formatHash({ screen: "settings", sub: "models" });
const SETTINGS_MEMORY = formatHash({ screen: "settings", sub: "memory/memory.embed_local_only" });

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-bg px-2.5 py-1.5">
      <dd className="font-mono text-[15px] font-medium text-text">{formatCount(value)}</dd>
      <dt className="truncate text-[11px] text-text-muted">{label}</dt>
    </div>
  );
}

type Outcome = { what: "scan" | "rebuild"; ok: true; scan: ScanResult } | { what: "scan" | "rebuild"; ok: false; detail: string };

/** What memory holds, how search is working, and the two owner actions on the index. */
export function StatusPanel() {
  const client = useQueryClient();
  const status = useStatus();
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const run = useMutation({
    mutationFn: (what: "scan" | "rebuild") => (what === "scan" ? memoryApi.scan() : memoryApi.reindex()),
    onMutate: () => setOutcome(null),
    onSuccess: (data, what) => {
      setOutcome({ what, ok: true, scan: data.scan });
      client.setQueryData<Status>(STATUS_KEY, (current) => (current ? { ...current, last_scan: data.scan } : current));
      void client.invalidateQueries({ queryKey: MEMORY_KEY });
    },
    onError: (error, what) => setOutcome({ what, ok: false, detail: errorDetail(error) }),
  });

  const data = status.data;

  return (
    <div className="flex flex-col gap-3 p-2.5">
      {status.isError && !data ? (
        <div className="rounded-lg border border-border bg-surface">
          <QueryError error={status.error} onRetry={() => void status.refetch()} />
        </div>
      ) : !data ? (
        <Loading what="the status" />
      ) : (
        <>
          {status.isError ? (
            <div className="rounded-lg border border-border bg-surface">
              <QueryError error={status.error} onRetry={() => void status.refetch()} />
            </div>
          ) : null}
          <section aria-labelledby="memory-status-holds" className="rounded-lg border border-border bg-surface px-3 py-2.5">
            <Label id="memory-status-holds">What memory holds</Label>
            <dl className="mt-2 grid grid-cols-2 gap-1.5">
              <Count label="notes" value={data.counts.notes} />
              <Count label="facts" value={data.counts.facts} />
              <Count label="passages" value={data.counts.chunks} />
              <Count label="passages with vectors" value={data.counts.chunks_with_vectors} />
            </dl>
            {data.library_path ? (
              <p className="mt-2.5 text-xs text-text-muted">
                On disk at <span className="select-all break-all font-mono text-[11.5px] text-text">{data.library_path}</span>
              </p>
            ) : null}
          </section>

          <section aria-labelledby="memory-status-meaning" className="rounded-lg border border-border bg-surface px-3 py-2.5">
            <Label id="memory-status-meaning">Meaning search</Label>
            <p className="mt-2 flex items-center gap-1.5 text-[13px] font-medium text-text">
              {data.meaning_search.on ? (
                <CircleCheck size={14} className="flex-none text-success" aria-hidden="true" />
              ) : (
                <CircleAlert size={14} className="flex-none text-warning" aria-hidden="true" />
              )}
              {data.meaning_search.on ? "On" : "Off: search matches words only"}
            </p>
            {data.meaning_search.reason ? <p className="mt-1 text-xs leading-snug text-text-muted">{data.meaning_search.reason}</p> : null}
            <dl className="mt-2 flex flex-col gap-1 text-xs">
              <div className="flex gap-2">
                <dt className="w-28 flex-none text-text-faint">Embeddings model</dt>
                <dd className="min-w-0 break-all font-mono text-[11.5px] text-text">{data.meaning_search.model ?? "none"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-28 flex-none text-text-faint">Note text may go</dt>
                <dd className="min-w-0 text-text-muted">{data.meaning_search.local_only ? "only to a model on your own machines" : "to a hosted model too"}</dd>
              </div>
            </dl>
            {vectorProgress(data) ? <p className="mt-1.5 text-xs text-text-muted">{vectorProgress(data)}</p> : null}
            <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
              <a href={SETTINGS_MODELS} className="text-accent underline underline-offset-2">
                Settings, Models
              </a>
              <a href={SETTINGS_MEMORY} className="text-accent underline underline-offset-2">
                Settings, Memory
              </a>
            </p>
          </section>

          <section aria-labelledby="memory-status-scan" className="rounded-lg border border-border bg-surface px-3 py-2.5">
            <Label id="memory-status-scan">Keeping up with the files</Label>
            <p className="mt-2 text-xs leading-snug text-text-muted">
              {data.last_scan ? (
                <>
                  Last scan <When ts={data.last_scan.at_ms} className="text-text" />. {describeScan(data.last_scan)}
                </>
              ) : (
                "The Library has not been scanned yet."
              )}
            </p>
            <p className="mt-1 text-xs leading-snug text-text-faint">
              {data.watch_disk
                ? "Edits made outside the app are picked up on their own."
                : "Edits made outside the app are picked up only when you press Scan now."}
            </p>
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              <button type="button" className="btn h-7 px-2.5 text-xs" disabled={run.isPending} onClick={() => run.mutate("scan")}>
                <RefreshCw size={13} aria-hidden="true" />
                {run.isPending && run.variables === "scan" ? "Scanning…" : "Scan now"}
              </button>
              <button
                type="button"
                className="btn h-7 px-2.5 text-xs"
                disabled={run.isPending}
                title="Throws the search index away and builds it again from the files. What is known about each fact is kept."
                onClick={() => run.mutate("rebuild")}
              >
                <Wrench size={13} aria-hidden="true" />
                {run.isPending && run.variables === "rebuild" ? "Rebuilding…" : "Rebuild the index"}
              </button>
            </div>
            {outcome ? (
              <p className={`mt-2 text-xs leading-snug ${outcome.ok ? "text-text" : "text-danger"}`} role={outcome.ok ? "status" : "alert"}>
                {outcome.ok
                  ? `${outcome.what === "scan" ? "Scanned" : "Rebuilt"} in ${formatCount(outcome.scan.took_ms)} ms. ${describeScan(outcome.scan)}`
                  : `${outcome.what === "scan" ? "The scan" : "The rebuild"} did not finish. ${outcome.detail}`}
              </p>
            ) : null}
          </section>

          <section aria-labelledby="memory-status-skipped" className="rounded-lg border border-border bg-surface px-3 py-2.5">
            <Label id="memory-status-skipped">Skipped files</Label>
            {data.skipped.length === 0 ? (
              <p className="mt-2 text-xs text-text-muted">None. Every note in the Library is part of memory.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1.5">
                {data.skipped.map((file) => (
                  <li key={file.path} className="text-xs leading-snug">
                    <span className="break-all font-mono text-[11.5px] text-text">{file.path}</span>
                    <span className="block text-text-muted">{file.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      <SourcesPanel />
    </div>
  );
}
