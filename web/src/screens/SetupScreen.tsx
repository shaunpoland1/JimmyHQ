import { useState } from "react";
import type { FormEvent } from "react";
import { api, errorDetail } from "../api";
import { AuthCard } from "../components/AuthCard";
import { Field } from "../components/Field";
import { MIN_PASSWORD_LENGTH, hasErrors, validateSetup } from "../lib/auth";
import type { SetupErrors } from "../lib/auth";

/** First-run setup, step 1 of 4: create the owner. The welcome steps follow. */
export function SetupScreen({ onDone }: { onDone: () => Promise<void> | void }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<SetupErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = validateSetup({ name, password, confirm });
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) return;
    setBusy(true);
    try {
      await api.setup(name.trim(), password);
      await onDone();
    } catch (error) {
      setFailure(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title="Welcome to JimmyHQ" subtitle="Set up the owner of this installation to get started.">
      <p className="-mt-3 mb-4 text-center font-mono text-[10.5px] uppercase tracking-widest text-text-faint">Step 1 of 4</p>
      <form onSubmit={(event) => void submit(event)} noValidate className="flex flex-col gap-4">
        <Field label="Your name" name="name" autoComplete="name" autoFocus value={name} onChange={(event) => setName(event.target.value)} error={errors.name} />
        <Field
          label="Password"
          name="new-password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
          error={errors.password}
        />
        <Field
          label="Confirm password"
          name="confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          error={errors.confirm}
        />
        {failure ? (
          <p role="alert" className="text-xs text-danger">
            {failure}
          </p>
        ) : null}
        <button type="submit" className="btn btn-primary mt-1" disabled={busy}>
          {busy ? "Setting up…" : "Create owner"}
        </button>
      </form>
    </AuthCard>
  );
}
