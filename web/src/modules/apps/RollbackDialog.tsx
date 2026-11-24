import { Loader2 } from "lucide-react";
import { useId, useState } from "react";
import { Modal } from "../../components/Modal";
import { formatFullTime } from "../../lib/format";
import type { AppDetail } from "./api";
import { rollbackChoices, rollbackLocalSentence, rollbackSentence } from "./lib/update";
import { useRollback } from "./queries";
import { Notice } from "./ui";

type Props = { app: Pick<AppDetail, "id" | "name" | "versions" | "tier" | "local_addresses">; onClose: () => void };

/** Go back to a kept version, and choose whether its data goes back too. */
export function RollbackDialog({ app, onClose }: Props) {
  const group = useId();
  const choices = rollbackChoices(app.versions ?? []);
  const [picked, setPicked] = useState(choices[0]?.name ?? "");
  const [restore, setRestore] = useState(false);
  const rollback = useRollback(onClose);
  const choice = choices.find((v) => v.name === picked) ?? null;
  const busy = rollback.isPending;
  const local = choice ? rollbackLocalSentence(app.name, choice, app.tier, app.local_addresses ?? []) : null;

  return (
    <Modal title={`Roll back ${app.name}?`} onClose={busy ? () => undefined : onClose}>
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 py-4 text-sm">
        {choices.length === 0 ? (
          <p className="text-text-muted">No earlier version of {app.name} is kept, so there is nothing to go back to.</p>
        ) : (
          <fieldset className="flex flex-col gap-2" disabled={busy}>
            <legend className="mb-1 text-xs font-medium text-text">Which version</legend>
            {choices.map((v) => (
              <label key={v.name} className="flex cursor-pointer items-start gap-2 rounded-lg border border-border p-2.5 has-[:checked]:border-accent">
                <input
                  type="radio"
                  name={group}
                  className="mt-1"
                  checked={picked === v.name}
                  onChange={() => {
                    setPicked(v.name);
                    if (!v.hasBackup) setRestore(false);
                  }}
                />
                <span>
                  <span className="block font-medium">{v.version}</span>
                  <span className="block text-xs text-text-muted">
                    {v.usedMs ? `Last used ${formatFullTime(v.usedMs)}. ` : ""}
                    {v.hasBackup ? "A copy of its data is kept." : "No copy of its data is kept."}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        {choice?.hasBackup ? (
          <label className="flex items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-1" checked={restore} onChange={(event) => setRestore(event.target.checked)} disabled={busy} />
            <span>Put back its data from when {choice.version} was replaced</span>
          </label>
        ) : null}
        {choice ? <Notice tone="calm">{rollbackSentence(app.name, choice, restore)}</Notice> : null}
        {local ? <Notice tone={choice?.tier === app.tier ? "calm" : "warn"}>{local}</Notice> : null}
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={onClose} disabled={busy} data-autofocus>
          Cancel
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !choice} onClick={() => choice && rollback.mutate({ app, choice, restore })}>
          {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
          {choice ? `Go back to ${choice.version}` : "Roll back"}
        </button>
      </div>
    </Modal>
  );
}
