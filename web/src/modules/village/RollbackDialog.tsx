import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { errorDetail } from "../../api";
import { Modal } from "../../components/Modal";
import { villageApi } from "./api";
import type { AgentSummary, Snapshot } from "./api";
import { ProgressNote, useProgress } from "./dialogs";
import { AGENTS_KEY, refreshKept } from "./hooks";
import { pathWords, rollbackEffects } from "./lib/memory";
import { displayName } from "./lib/status";
import { DialogButtons, Note, dateTime } from "./parts";

type Props = { agent: AgentSummary; snapshot: Snapshot; title: string; at: number; onClose: () => void };

/**
 * Rolling back is asked for, then done by the village process. This says exactly what
 * comes back, lets the owner choose whether the diary does too, and waits with them.
 */
export function RollbackDialog({ agent, snapshot, title, at, onClose }: Props) {
  const client = useQueryClient();
  const name = displayName(agent);
  const [withDiary, setWithDiary] = useState(false);
  const [note, setNote] = useState("");
  const [commandId, setCommandId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const diaryId = useId();
  const noteId = useId();

  const { progress } = useProgress(commandId, "rollback", () => {
    refreshKept(client, agent.id);
    void client.invalidateQueries({ queryKey: AGENTS_KEY });
  });

  const roll = async () => {
    if (busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      const ticket = await villageApi.rollback(agent.id, { to: snapshot.seq, diary: withDiary, note: note.trim() });
      setCommandId(ticket.command_id);
    } catch (error) {
      setRefusal(`${errorDetail(error)} Nothing was changed.`);
    } finally {
      setBusy(false);
    }
  };

  if (progress) {
    const finished = progress.phase === "done";
    return (
      <Modal title={finished ? "Memory rolled back" : progress.phase === "refused" ? "Not rolled back" : `Rolling back ${name}'s memory`} onClose={onClose}>
        <div className="px-4 py-4 text-sm">
          <ProgressNote progress={progress} />
        </div>
        <DialogButtons onCancel={onClose} cancel={finished ? "Done" : "Close"} />
      </Modal>
    );
  }

  const effects = rollbackEffects(snapshot.seq, withDiary, name);
  return (
    <Modal title={`Roll ${name}'s memory back to snapshot ${snapshot.seq}?`} onClose={onClose} widthClass="max-w-lg">
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 py-4 text-sm text-text-muted">
        <p>
          <span className="font-medium text-text">{title}</span>, saved {dateTime(at)}. It holds {pathWords(snapshot.paths)}.
        </p>
        <div>
          <p className="mb-1 font-medium text-text">What is put back</p>
          <ul className="list-disc space-y-1 pl-5">
            {effects.restored.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
        <label htmlFor={diaryId} className="flex items-start gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-xs text-text-muted">
          <input id={diaryId} type="checkbox" className="mt-0.5" checked={withDiary} onChange={(event) => setWithDiary(event.target.checked)} />
          <span>
            <span className="font-medium text-text">Roll the diary back too</span>
            <span className="block text-text-faint">Off by default: the diary is {name}'s own record of its days, and memory can be wrong without it being wrong.</span>
          </span>
        </label>
        <div>
          <p className="mb-1 font-medium text-text">What stays</p>
          <ul className="list-disc space-y-1 pl-5">
            {effects.untouched.map((line) => (
              <li key={line}>{line}</li>
            ))}
            <li>{effects.undo}</li>
          </ul>
        </div>
        <Note tone="warn">While it happens, {name} is paused between turns. It takes no new turn until the rollback is done, then carries on.</Note>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={noteId} className="text-xs font-medium text-text">
            A note for the record (optional)
          </label>
          <textarea id={noteId} className="village-textarea" rows={2} maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Why you are rolling back" />
        </div>
        {refusal ? <Note tone="bad">{refusal}</Note> : null}
      </div>
      <DialogButtons onCancel={onClose} cancel="Keep memory as it is">
        <button type="button" className="btn btn-danger" onClick={() => void roll()} disabled={busy}>
          Roll back to snapshot {snapshot.seq}
        </button>
      </DialogButtons>
    </Modal>
  );
}
