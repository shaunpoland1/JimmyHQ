import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { errorDetail } from "../../api";
import { Modal } from "../../components/Modal";
import { memoryApi } from "./api";
import type { Fact, FactPurge, Note, NotePurge } from "./api";
import { FACTS_KEY, HISTORIES_KEY, NOTES_KEY, noteKey, refreshLists } from "./hooks";
import { dropDraft } from "./lib/drafts";
import { dropNoteFact, dropPagedFact } from "./lib/facts";
import type { FactPages } from "./lib/facts";
import { FACT_WARNING, OUT_OF_REACH, factPurged, notePurged, noteWarning } from "./lib/purge";

const MAX_REASON = 300;
const VERSIONS_KEY = ["memory", "version"] as const;

export type ForeverActions = {
  /** Asks, then forgets the fact and erases every stored version that holds its line. */
  forgetEverywhere: (fact: Fact) => void;
  /** Asks, then deletes the note and all of its history. `onDeleted` runs once the owner has read the result. */
  deleteNoteForGood: (path: string, facts: number, onDeleted: () => void) => void;
};

const ForeverContext = createContext<ForeverActions | null>(null);

/** The two deletes that cannot be undone. Their dialogs outlive the row or note they were opened from. */
export function useForever(): ForeverActions {
  const actions = useContext(ForeverContext);
  if (!actions) throw new Error("useForever must be used inside ForeverProvider");
  return actions;
}

type Asking =
  | { what: "fact"; fact: Fact }
  | { what: "note"; path: string; facts: number; onDeleted: () => void };

type Done = { what: "fact"; purged: FactPurge } | { what: "note"; purged: NotePurge };

export function ForeverProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [asking, setAsking] = useState<Asking | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [done, setDone] = useState<Done | null>(null);
  const reasonId = useId();
  const warningId = useId();

  const open = useCallback((next: Asking) => {
    setAsking(next);
    setReason("");
    setBusy(false);
    setRefusal(null);
    setDone(null);
  }, []);

  const actions = useMemo<ForeverActions>(
    () => ({
      forgetEverywhere: (fact) => open({ what: "fact", fact }),
      deleteNoteForGood: (path, facts, onDeleted) => open({ what: "note", path, facts, onDeleted }),
    }),
    [open],
  );

  const close = () => {
    if (busy) return;
    const finished = asking && done ? asking : null;
    setAsking(null);
    setDone(null);
    if (finished?.what === "note") finished.onDeleted();
  };

  const erase = async () => {
    if (!asking || busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      if (asking.what === "fact") {
        const { fact } = asking;
        const answer = await memoryApi.forgetEverywhere(fact.id, reason.trim() || undefined);
        client.setQueriesData<Note>({ queryKey: NOTES_KEY }, (note) => (note ? dropNoteFact(note, fact.id) : note));
        client.setQueriesData<FactPages>({ queryKey: FACTS_KEY }, (pages) => (pages ? dropPagedFact(pages, fact.id) : pages));
        setDone({ what: "fact", purged: answer.purged });
      } else {
        const answer = await memoryApi.deleteNoteForGood(asking.path);
        dropDraft(asking.path);
        client.removeQueries({ queryKey: noteKey(asking.path) });
        setDone({ what: "note", purged: answer.purged });
      }
      // A version read earlier is kept for good by its query; its text may be erased now.
      client.removeQueries({ queryKey: VERSIONS_KEY });
      refreshLists(client);
      void client.invalidateQueries({ queryKey: NOTES_KEY });
      void client.invalidateQueries({ queryKey: HISTORIES_KEY });
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  let dialog: ReactNode = null;
  if (asking && done) {
    dialog = (
      <Modal title={done.what === "fact" ? "Forgotten everywhere" : "Deleted with its history"} onClose={close} widthClass="max-w-sm">
        <div className="flex flex-col gap-2 px-4 py-4" role="status">
          <p className="text-[13px] text-text">{done.what === "fact" ? factPurged(done.purged) : notePurged(done.purged)}</p>
          <p className="text-xs leading-snug text-text-muted">{OUT_OF_REACH}</p>
        </div>
        <div className="flex justify-end border-t border-border px-4 py-3">
          <button type="button" className="btn btn-primary" onClick={close} data-autofocus>
            Done
          </button>
        </div>
      </Modal>
    );
  } else if (asking) {
    const isFact = asking.what === "fact";
    dialog = (
      <Modal title={isFact ? "Forget this fact everywhere?" : "Delete this note and its history?"} onClose={close} widthClass="max-w-sm">
        <form
          aria-describedby={warningId}
          onSubmit={(event) => {
            event.preventDefault();
            void erase();
          }}
        >
          <div className="flex flex-col gap-3 px-4 py-4">
            {asking.what === "fact" ? (
              <p className="rounded-md border border-border bg-bg px-2.5 py-2 text-[13px] text-text [overflow-wrap:anywhere]">{asking.fact.text}</p>
            ) : null}
            <div id={warningId} className="flex flex-col gap-2">
              <p className="text-[13px] leading-snug text-text [overflow-wrap:anywhere]">{asking.what === "fact" ? FACT_WARNING : noteWarning(asking.path, asking.facts)}</p>
              <p className="text-xs leading-snug text-text-muted">{OUT_OF_REACH}</p>
            </div>
            {isFact ? (
              <div className="flex flex-col gap-1.5">
                <label htmlFor={reasonId} className="text-xs font-medium text-text">
                  Why (optional)
                </label>
                <input id={reasonId} className="input" value={reason} maxLength={MAX_REASON} placeholder="Private" onChange={(event) => setReason(event.target.value)} />
              </div>
            ) : null}
            {refusal ? (
              <p className="text-xs text-danger [overflow-wrap:anywhere]" role="alert">
                Nothing was erased. {refusal}
              </p>
            ) : null}
          </div>
          <div className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3">
            <button type="button" className="btn" onClick={close} disabled={busy} data-autofocus>
              Cancel
            </button>
            <button type="submit" className="btn btn-danger" disabled={busy}>
              {busy ? "Erasing…" : refusal ? "Try again" : isFact ? "Forget everywhere" : "Delete with history"}
            </button>
          </div>
        </form>
      </Modal>
    );
  }

  return (
    <ForeverContext.Provider value={actions}>
      {children}
      {dialog}
    </ForeverContext.Provider>
  );
}
