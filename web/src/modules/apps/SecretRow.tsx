import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../hooks/useToast";
import { clearQuestion, secretProblem, statusWord } from "./lib/secrets";
import type { SecretState } from "./lib/secrets";

/**
 * One write-only field: Save sends the value once, Clear asks first. Used for an app's own
 * secrets here and for those it keeps for each of the owner's addresses (`AppHosts`).
 */
export function SecretRow({
  appName,
  state,
  queryKey,
  save: send,
  clear: forget,
}: {
  appName: string;
  state: SecretState;
  queryKey: readonly unknown[];
  save: (typed: string) => Promise<void>;
  clear: () => Promise<void>;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const inputId = useId();
  const errorId = useId();
  const helpId = useId();

  const refresh = () => client.invalidateQueries({ queryKey });
  const save = useMutation({
    mutationFn: send,
    onSuccess: () => {
      toast.success(`${state.label} saved for ${appName}.`);
      void refresh();
    },
    onError: (failure) => setError(errorDetail(failure)),
    // The value leaves the page either way.
    onSettled: () => setValue(""),
  });
  const clear = useMutation({
    mutationFn: forget,
    onSuccess: () => {
      setConfirming(false);
      toast.success(`${state.label} cleared for ${appName}.`);
      void refresh();
    },
    onError: (failure) => {
      setConfirming(false);
      toast.error(errorDetail(failure));
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const problem = secretProblem(value);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    save.mutate(value);
  };
  const question = clearQuestion(appName, state);

  return (
    <div className="flex flex-col gap-2 px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <label htmlFor={inputId} className="min-w-0 flex-1 text-[13px] font-medium text-text">
          {state.label}
        </label>
        <span className={`rounded-full px-2 text-[11px] ring-1 ring-border ${state.set ? "bg-success/10 text-success" : "bg-surface-2 text-text-muted"}`}>{statusWord(state)}</span>
      </div>
      {state.help ? (
        <p id={helpId} className="text-xs text-text-muted">
          {state.help}
        </p>
      ) : null}
      <form className="flex flex-col gap-2 @md:flex-row @md:items-start" onSubmit={submit} noValidate>
        <div className="min-w-0 flex-1">
          <input
            id={inputId}
            className="input font-mono"
            type="password"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder={state.set ? "Paste a new one to replace it" : "Paste it here"}
            value={value}
            aria-invalid={error ? true : undefined}
            aria-describedby={[state.help ? helpId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined}
            onChange={(event) => {
              setValue(event.target.value);
              if (error) setError(null);
            }}
          />
          {error ? (
            <p id={errorId} className="mt-1 break-words text-xs text-danger" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex flex-none gap-2">
          <button type="submit" className="btn btn-primary" disabled={save.isPending || value.trim() === ""}>
            {save.isPending ? "Saving…" : "Save"}
          </button>
          {state.set ? (
            <button type="button" className="btn" onClick={() => setConfirming(true)} aria-label={`Clear ${state.label}`}>
              Clear
            </button>
          ) : null}
        </div>
      </form>
      {confirming ? (
        <ConfirmDialog
          title={question.title}
          message={question.message}
          confirmLabel="Clear"
          danger
          busy={clear.isPending}
          onConfirm={() => clear.mutate()}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </div>
  );
}
