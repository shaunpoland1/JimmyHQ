import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, api, errorDetail } from "../api";
import { AuthCard } from "../components/AuthCard";
import { Field } from "../components/Field";

type SignInScreenProps = { ownerName: string | null; onDone: () => Promise<void> | void };

export function SignInScreen({ ownerName, onDone }: SignInScreenProps) {
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!password) {
      setFailure("Enter your password.");
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      await api.login(password);
      await onDone();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setFailure("That password is not right.");
      else setFailure(errorDetail(error)); // 429 and others: show the server's detail.
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title={ownerName ? `Welcome back, ${ownerName}` : "Welcome back"} subtitle="Sign in to continue.">
      <form onSubmit={(event) => void submit(event)} noValidate className="flex flex-col gap-4">
        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={failure ?? undefined}
        />
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-center text-xs text-text-faint">
          Forgot it? On the computer JimmyHQ runs on, open a terminal in the JimmyHQ folder and run <code className="font-mono">uv run jimmyhq owner set-password</code>.
        </p>
      </form>
    </AuthCard>
  );
}
