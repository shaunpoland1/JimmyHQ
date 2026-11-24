import { Check, Circle, CircleCheck, Eraser, FileText, Pencil, Sparkles, Trash2, X } from "lucide-react";
import { useId, useState } from "react";
import { Modal } from "../../components/Modal";
import type { Fact } from "./api";
import type { FactActions } from "./hooks";
import { useForever } from "./forever";
import { awaitsReview, describeActor, parseEvidence } from "./lib/facts";
import { Confidence, When } from "./parts";

const MAX_FACT = 2000;
const MAX_REASON = 300;

type FactItemProps = {
  fact: Fact;
  /** The kinds to offer in the kind menu. */
  kinds: readonly string[];
  actions: FactActions;
  /** Show which note the fact is in, as a link to it. */
  onOpenNote?: (path: string) => void;
};

/** One fact with its details and what the owner can do with it. */
export function FactItem({ fact, kinds, actions, onOpenNote }: FactItemProps) {
  const [draft, setDraft] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const forever = useForever();
  const review = awaitsReview(fact);
  const editId = useId();
  const reasonId = useId();

  const run = async (work: () => Promise<boolean>) => {
    if (busy) return false;
    setBusy(true);
    try {
      return await work();
    } finally {
      setBusy(false);
    }
  };

  const typed = draft?.trim() ?? "";
  const saveText = async () => {
    if (draft === null) return;
    if (!typed || typed === fact.text) {
      setDraft(null);
      return;
    }
    // The field stays open with what was typed if the server refuses.
    if (await run(() => actions.setText(fact, typed))) setDraft(null);
  };

  const kindOptions = kinds.includes(fact.kind) ? kinds : [...kinds, fact.kind];

  return (
    <li className="group flex gap-2 border-b border-border px-3 py-2.5 last:border-b-0">
      <button
        type="button"
        className="icon-btn -ml-1 size-6"
        aria-pressed={fact.confirmed}
        aria-label={fact.confirmed ? "Confirmed by you. Press to take the confirmation back" : "Not confirmed. Press to confirm this fact"}
        title={fact.confirmed ? "Confirmed by you" : "Confirm this fact"}
        disabled={busy}
        onClick={() => void run(() => actions.setConfirmed(fact, !fact.confirmed))}
      >
        {fact.confirmed ? <CircleCheck size={15} className="text-success" aria-hidden="true" /> : <Circle size={15} aria-hidden="true" />}
      </button>
      <div className="min-w-0 flex-1">
        {draft === null ? (
          <p className="whitespace-pre-wrap text-[13px] leading-snug text-text [overflow-wrap:anywhere]">{fact.text}</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            <label htmlFor={editId} className="sr-only">
              The fact's text
            </label>
            <textarea
              id={editId}
              className="input h-auto min-h-16 resize-y py-1.5 leading-snug"
              value={draft}
              maxLength={MAX_FACT}
              rows={2}
              autoFocus
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  setDraft(null);
                } else if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void saveText();
                }
              }}
            />
            <div className="flex gap-1.5">
              <button type="button" className="btn btn-primary h-7 px-2.5 text-xs" disabled={busy || !typed} onClick={() => void saveText()}>
                <Check size={13} aria-hidden="true" />
                Save
              </button>
              <button type="button" className="btn h-7 px-2.5 text-xs" onClick={() => setDraft(null)}>
                <X size={13} aria-hidden="true" />
                Cancel
              </button>
            </div>
          </div>
        )}

        {review && draft === null ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-md border border-border bg-bg px-2 py-1.5">
            <p className="flex min-w-0 flex-1 basis-40 items-center gap-1.5 text-[11.5px] leading-snug text-text-muted">
              <Sparkles size={12} className="flex-none text-warning" aria-hidden="true" />
              Saved automatically, not confirmed by you
            </p>
            <div className="flex flex-none gap-1.5">
              <button type="button" className="btn h-6 px-2 text-[11.5px]" disabled={busy} aria-label="Confirm this fact" onClick={() => void run(() => actions.setConfirmed(fact, true))}>
                <Check size={12} aria-hidden="true" />
                Confirm
              </button>
              <button
                type="button"
                className="btn h-6 px-2 text-[11.5px]"
                disabled={busy}
                aria-label="Forget this fact. It will not be proposed again"
                onClick={() => void run(() => actions.forget(fact, ""))}
              >
                <X size={12} aria-hidden="true" />
                Forget
              </button>
            </div>
          </div>
        ) : null}

        <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11.5px] text-text-muted">
          <select
            className="h-5 max-w-[9rem] rounded border border-border bg-surface-2 px-1 font-mono text-[10.5px] text-text-muted"
            aria-label="Kind of fact"
            title="Kind"
            value={fact.kind}
            disabled={busy}
            onChange={(event) => {
              const kind = event.target.value;
              if (kind !== fact.kind) void run(() => actions.setKind(fact, kind));
            }}
          >
            {kindOptions.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
          <Confidence value={fact.confidence} />
          <span>
            saved by {describeActor(fact.saved_by)}, <When ts={fact.created_ms} />
          </span>
          {fact.updated_ms > fact.created_ms ? (
            <span>
              changed <When ts={fact.updated_ms} />
            </span>
          ) : null}
        </div>

        {fact.evidence.length > 0 ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11.5px] text-text-faint">
            <span>From</span>
            {fact.evidence.map((raw) => {
              const ref = parseEvidence(raw);
              return (
                <span key={raw} className="chip" title={raw}>
                  <span className="truncate">{ref.label}</span>
                </span>
              );
            })}
          </div>
        ) : null}

        {onOpenNote ? (
          <button
            type="button"
            className="mt-1.5 flex max-w-full items-center gap-1 rounded text-[11.5px] text-accent hover:underline"
            onClick={() => onOpenNote(fact.note)}
            aria-label={`Open the note ${fact.note}, line ${fact.line}`}
          >
            <FileText size={12} className="flex-none" aria-hidden="true" />
            <span className="truncate font-mono">{fact.note}</span>
            <span className="flex-none text-text-faint">line {fact.line}</span>
          </button>
        ) : null}
      </div>

      {draft === null ? (
        <div className="flex flex-none flex-col gap-0.5">
          <button type="button" className="icon-btn size-6" aria-label="Edit this fact's text" title="Edit" disabled={busy} onClick={() => setDraft(fact.text)}>
            <Pencil size={13} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="icon-btn size-6"
            aria-label="Forget this fact"
            title="Forget"
            disabled={busy}
            onClick={() => {
              setReason("");
              setForgetting(true);
            }}
          >
            <Trash2 size={13} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="icon-btn size-6"
            aria-label="Forget this fact everywhere, and erase it from stored history"
            title="Forget everywhere"
            disabled={busy}
            onClick={() => forever.forgetEverywhere(fact)}
          >
            <Eraser size={13} aria-hidden="true" />
          </button>
        </div>
      ) : null}

      {forgetting ? (
        <Modal title="Forget this fact?" onClose={() => setForgetting(false)} widthClass="max-w-sm">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setForgetting(false);
              void run(() => actions.forget(fact, reason));
            }}
          >
            <div className="flex flex-col gap-3 px-4 py-4">
              <p className="rounded-md border border-border bg-bg px-2.5 py-2 text-[13px] text-text [overflow-wrap:anywhere]">{fact.text}</p>
              <p className="text-xs text-text-muted">
                Its line is removed from <span className="font-mono">{fact.note}</span>. The note's earlier versions, which still contain it, stay in its history. To erase those too, use "Forget everywhere".
              </p>
              <div className="flex flex-col gap-1.5">
                <label htmlFor={reasonId} className="text-xs font-medium text-text">
                  Why (optional)
                </label>
                <input
                  id={reasonId}
                  className="input"
                  value={reason}
                  maxLength={MAX_REASON}
                  placeholder="No longer true"
                  onChange={(event) => setReason(event.target.value)}
                />
              </div>
            </div>
            <div className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3">
              <button type="button" className="btn" onClick={() => setForgetting(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setForgetting(false);
                  forever.forgetEverywhere(fact);
                }}
              >
                Forget everywhere…
              </button>
              <button type="submit" className="btn btn-danger" data-autofocus>
                Forget
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </li>
  );
}
