import { Modal } from "../../components/Modal";
import type { Conversation } from "./api";
import { CANNOT_UNDO, DELETE_CONFIRM, DELETE_TITLE, whatIsErased, whatStays } from "./lib/delete";
import { conversationTitle } from "./lib/format";

type DeleteForGoodDialogProps = {
  conversation: Conversation;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/** Asks before a whole conversation is erased: what goes, what stays, and that it cannot be undone. */
export function DeleteForGoodDialog({ conversation, busy, onConfirm, onCancel }: DeleteForGoodDialogProps) {
  return (
    <Modal title={DELETE_TITLE} onClose={onCancel} widthClass="max-w-lg">
      <div className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto px-4 py-4 text-sm text-text-muted">
        <p className="break-words text-text">“{conversationTitle(conversation)}”</p>
        <section aria-labelledby="chat-delete-erased">
          <h3 id="chat-delete-erased" className="text-xs font-semibold uppercase tracking-wide text-danger">
            Erased
          </h3>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {whatIsErased(conversation).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
        <section aria-labelledby="chat-delete-stays">
          <h3 id="chat-delete-stays" className="text-xs font-semibold uppercase tracking-wide text-text-faint">
            Stays
          </h3>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {whatStays(conversation).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
        <p className="font-medium text-text">{CANNOT_UNDO}</p>
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={onCancel} data-autofocus>
          Cancel
        </button>
        <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy}>
          {busy ? "Deleting…" : DELETE_CONFIRM}
        </button>
      </div>
    </Modal>
  );
}
