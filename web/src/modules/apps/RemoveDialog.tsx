import { Loader2 } from "lucide-react";
import { useId, useState } from "react";
import { Modal } from "../../components/Modal";
import type { AppSummary, RemoveChoice } from "./api";
import { DELETE_WARNING, DOOR_ERASED, ERASE_NOTE, RECORD_KEPT, RECORD_ERASED, keptLine, removeIntro } from "./lib/status";
import { useRemoveApp } from "./queries";

type Props = { app: Pick<AppSummary, "id" | "name" | "tier"> & { data_dir?: string | null }; onClose: () => void; onRemoved?: () => void };

/** Asks before removing an app, and says what is kept and where. Its data is kept unless the owner chooses otherwise. */
export function RemoveDialog({ app, onClose, onRemoved }: Props) {
  const group = useId();
  const [keepData, setKeepData] = useState(true);
  const [eraseRecord, setEraseRecord] = useState(true);
  const remove = useRemoveApp(() => {
    onRemoved?.();
    onClose();
  });
  const busy = remove.isPending;
  // With the data kept, nothing is erased; deleting it erases what it wrote unless unticked.
  const choice: RemoveChoice = keepData ? { keepData, eraseRecord: false } : { keepData, eraseRecord };

  return (
    <Modal title={`Remove ${app.name}?`} onClose={busy ? () => undefined : onClose}>
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 py-4 text-sm">
        <p className="text-text-muted">{removeIntro(app)}</p>
        <fieldset className="flex flex-col gap-2" disabled={busy}>
          <legend className="mb-1 text-xs font-medium text-text">What happens to its data</legend>
          <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-border p-2.5 has-[:checked]:border-accent">
            <input type="radio" name={group} className="mt-1" checked={keepData} onChange={() => setKeepData(true)} />
            <span>
              <span className="block font-medium">Keep its data</span>
              <span className="block text-xs text-text-muted">{keptLine(app)}</span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-border p-2.5 has-[:checked]:border-danger">
            <input type="radio" name={group} className="mt-1" checked={!keepData} onChange={() => setKeepData(false)} />
            <span>
              <span className="block font-medium">Delete its data for good</span>
              <span className="block text-xs text-text-muted">Everything it stored on this computer is wiped. {app.tier === "full" ? `${DOOR_ERASED} ` : ""}{ERASE_NOTE}</span>
            </span>
          </label>
        </fieldset>
        {keepData ? null : (
          <div className="flex flex-col gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-text" role="status">
            <p className="font-semibold text-danger">{DELETE_WARNING}</p>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={eraseRecord} onChange={(event) => setEraseRecord(event.target.checked)} disabled={busy} />
              <span>Also erase what it wrote on your record</span>
            </label>
            <p>{eraseRecord ? RECORD_ERASED : RECORD_KEPT}</p>
          </div>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={onClose} disabled={busy} data-autofocus>
          Cancel
        </button>
        <button type="button" className="btn btn-danger" disabled={busy} onClick={() => remove.mutate({ app, choice })}>
          {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
          {keepData ? "Remove, keep its data" : "Remove and delete its data"}
        </button>
      </div>
    </Modal>
  );
}
