import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, TriangleAlert } from "lucide-react";
import { useEffect } from "react";
import { ApiError, errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { useSettingsQuery } from "../../hooks/useSettings";
import { subscribeFeed } from "../../lib/feed";
import { BACKUP_STATUS_KEY, backupApi } from "./api";
import type { BackupStatus } from "./api";
import { affectsBackupStatus, destinationLines, destinationName, headline, runningLine } from "./lib/status";

const CARD = "@container rounded-xl border border-border bg-surface";
const TONE_DOT = { ok: "bg-success", warn: "bg-warning", off: "bg-sage" } as const;

/** A refusal will be refused again; only a failure to reach the server is worth another try. */
function retry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return count < 2;
}

/** The status, kept current from the live feed, and asked for every few seconds while a backup runs. */
function useBackupStatus() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: BACKUP_STATUS_KEY,
    queryFn: backupApi.status,
    staleTime: 15_000,
    retry,
    refetchInterval: (current) => (current.state.data?.running ? 3_000 : false),
  });
  useEffect(() => {
    const refresh = () => void client.invalidateQueries({ queryKey: BACKUP_STATUS_KEY });
    return subscribeFeed({
      onEvent: (event) => {
        if (affectsBackupStatus(event)) refresh();
      },
      onReconnect: refresh,
    });
  }, [client]);
  return query;
}

/** Shown above the generated Backups settings: each folder's last backup and test, and the two buttons. Owned by the backup module. */
export function BackupSettingsExtra() {
  const query = useBackupStatus();
  if (query.data) return <StatusCard status={query.data} />;
  if (query.isPending) {
    return (
      <p className={`${CARD} px-4 py-4 text-sm text-text-faint`} role="status">
        Checking backups…
      </p>
    );
  }
  return (
    <div className={`${CARD} flex flex-wrap items-center justify-between gap-3 px-4 py-3.5`} role="alert">
      <p className="min-w-0 flex-1 text-sm text-text-muted">
        <span className="font-medium text-text">The backup status is unavailable.</span> {errorDetail(query.error)}
      </p>
      <button type="button" className="btn" onClick={() => void query.refetch()} disabled={query.isFetching}>
        Try again
      </button>
    </div>
  );
}

function StatusCard({ status }: { status: BackupStatus }) {
  const client = useQueryClient();
  const toast = useToast();
  const state = headline(status);
  const settings = useSettingsQuery().data?.settings;
  const setting = (key: string) => settings?.find((s) => s.key === key)?.value;
  const limit = setting("backup.stuck_after_hours");
  const zone = setting("owner.timezone");
  const running = runningLine(status, { stuckAfterHours: typeof limit === "number" ? limit : null, timeZone: typeof zone === "string" ? zone : undefined });
  const started = (what: string) => (result: { status: BackupStatus }) => {
    client.setQueryData(BACKUP_STATUS_KEY, result.status);
    toast.success(what);
  };
  const now = useMutation({ mutationFn: backupApi.now, onSuccess: started("Backup started. This page says when it is done."), onError: (failure) => toast.error(errorDetail(failure)) });
  const test = useMutation({ mutationFn: backupApi.test, onSuccess: started("Restore test started. This page says how it went."), onError: (failure) => toast.error(errorDetail(failure)) });
  const disabled = !status.on || status.running || now.isPending || test.isPending;

  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
            <Archive size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-[13px] font-semibold text-text">Backups</h3>
            <p className="break-words text-xs text-text-muted">
              {status.on ? "Each backup is checked when it is made, and the newest is tested on a schedule." : "Choose a backup folder below to switch backups on."}
            </p>
          </div>
          <span className="inline-flex flex-none items-center gap-1.5 text-xs font-medium text-text" role="status">
            <span className={`dot ${TONE_DOT[state.tone]}`} aria-hidden="true" />
            {state.label}
          </span>
        </div>

        {running ? (
          <p className="flex items-start gap-2 break-words text-xs text-text-muted" role="status">
            {running.stuck ? <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" /> : null}
            <span className={running.stuck ? "min-w-0 text-text" : "min-w-0"}>{running.text}</span>
          </p>
        ) : null}

        {status.problem ? (
          <div className="rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <p className="flex items-start gap-2 text-xs text-text">
              <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
              <span className="min-w-0 break-words">{status.problem}</span>
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={disabled} onClick={() => now.mutate()}>
            {now.isPending ? "Starting…" : "Back up now"}
          </button>
          <button type="button" className="btn" disabled={disabled} onClick={() => test.mutate()}>
            {test.isPending ? "Starting…" : "Test the newest backup"}
          </button>
        </div>
      </div>

      <ul className="divide-y divide-border" aria-label="Backup folders">
        {status.destinations.map((destination) => {
          const shown = destinationLines(destination, status.now);
          return (
            <li key={destination.which} className="flex flex-col gap-1 px-4 py-3">
              <p className="flex items-center gap-2 text-[13px] font-medium text-text">
                <span className={`dot ${TONE_DOT[shown.tone]}`} aria-hidden="true" />
                {destinationName(destination.which)}
              </p>
              {shown.lines.map((line, at) => (
                <p key={at} className="break-words text-xs text-text-muted">
                  {line}
                </p>
              ))}
            </li>
          );
        })}
      </ul>

      <p className="px-4 py-3 text-xs text-text-faint">
        To put a backup back, stop JimmyHQ and run <code className="font-mono">jimmyhq backup restore &lt;backup folder&gt;</code> on this computer. The data there now is moved aside, never deleted.
      </p>
    </div>
  );
}
