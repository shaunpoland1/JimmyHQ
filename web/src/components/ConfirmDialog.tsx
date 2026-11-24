import { confirmStartsOn } from "../lib/dialog";
import { Modal } from "./Modal";

type ConfirmDialogProps = {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Styled replacement for the native confirm(). When the answer is destructive the
 * keyboard starts on the way out, so Enter or Space cannot delete at once.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel = "Cancel",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const startsOn = confirmStartsOn(danger);
  return (
    <Modal title={title} onClose={onCancel}>
      <p className="px-4 py-4 text-sm text-text-muted">{message}</p>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={onCancel} data-autofocus={startsOn === "cancel" ? true : undefined}>
          {cancelLabel}
        </button>
        <button
          type="button"
          className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
          onClick={onConfirm}
          disabled={busy}
          data-autofocus={startsOn === "confirm" ? true : undefined}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
