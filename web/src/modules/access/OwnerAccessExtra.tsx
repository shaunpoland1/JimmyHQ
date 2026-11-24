import { useMutation } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { MIN_PASSWORD_LENGTH } from "../../lib/auth";
import { accessApi } from "./api";
import { changedLine, endedLine, refusalField, validateChange } from "./lib/password";
import type { ChangeErrors } from "./lib/password";

const CARD = "@container rounded-xl border border-border bg-surface";

/** Shown above Settings, Access: change the owner's password, or sign out every other browser. Owned by the access module. */
export function OwnerAccessExtra() {
  const [open, setOpen] = useState<"password" | "sessions" | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const endOthers = useMutation({
    mutationFn: accessApi.endOtherSessions,
    onSuccess: (result) => {
      setError(null);
      setDone(endedLine(result.sessions_ended));
      setOpen(null);
    },
    onError: (failure) => {
      setDone(null);
      setError(errorDetail(failure));
      setOpen(null);
    },
  });

  return (
    <div className={`${CARD} flex flex-col gap-3 px-4 py-3.5`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
          <KeyRound size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-semibold text-text">Your password and where you are signed in</h3>
          <p className="text-xs text-text-muted">
            Changing the password signs out every other browser. If you forget it, open a terminal in the JimmyHQ folder on the computer JimmyHQ runs on and run <code className="font-mono">uv run jimmyhq owner set-password</code>.
          </p>
        </div>
      </div>
      {done ? (
        <p className="break-words text-xs text-text" role="status">
          {done}
        </p>
      ) : null}
      {error ? (
        <p className="break-words rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-xs text-text" role="alert">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn" onClick={() => setOpen("password")}>
          Change password
        </button>
        <button type="button" className="btn" onClick={() => setOpen("sessions")}>
          Sign out everywhere else
        </button>
      </div>
      {open === "password" ? (
        <ChangePasswordDialog
          onClose={() => setOpen(null)}
          onDone={(sessionsEnded) => {
            setError(null);
            setDone(changedLine(sessionsEnded));
            setOpen(null);
          }}
        />
      ) : null}
      {open === "sessions" ? (
        <ConfirmDialog
          title="Sign out everywhere else?"
          message="Every other browser and device signed in to JimmyHQ will have to enter the password again. This one stays signed in. API tokens and connected clients are not affected."
          confirmLabel={endOthers.isPending ? "Signing out…" : "Sign out the others"}
          busy={endOthers.isPending}
          onConfirm={() => endOthers.mutate()}
          onCancel={() => setOpen(null)}
        />
      ) : null}
    </div>
  );
}

function ChangePasswordDialog({ onClose, onDone }: { onClose: () => void; onDone: (sessionsEnded: number) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<ChangeErrors>({});
  const [failure, setFailure] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: () => accessApi.changePassword(current, next),
    onSuccess: (result) => onDone(result.sessions_ended),
    onError: (error) => {
      const field = error instanceof ApiError ? refusalField(error.status) : null;
      if (field) setErrors({ [field]: errorDetail(error) });
      else setFailure(errorDetail(error));
      if (field === "current") setCurrent("");
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = validateChange({ current, next, confirm });
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length === 0) change.mutate();
  };

  return (
    <Modal title="Change password" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <div className="flex flex-col gap-4 px-4 py-4">
          <Field
            label="Current password"
            name="current-password"
            type="password"
            autoComplete="current-password"
            data-autofocus
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            error={errors.current}
          />
          <Field
            label="New password"
            name="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
            error={errors.next}
          />
          <Field
            label="New password again"
            name="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            error={errors.confirm}
          />
          {failure ? (
            <p className="text-xs text-danger" role="alert">
              {failure}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={change.isPending}>
            {change.isPending ? "Changing…" : "Change password"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
