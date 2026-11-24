import { AuthCard } from "../components/AuthCard";

/** Shown when the server cannot be reached at start-up. */
export function UnreachableScreen({ detail, onRetry, busy }: { detail: string; onRetry: () => void; busy: boolean }) {
  return (
    <AuthCard title="Cannot reach JimmyHQ" subtitle="The server is not answering. Check that it is running, then try again.">
      <div className="flex flex-col gap-4">
        <p className="rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-text-muted">{detail}</p>
        <button type="button" className="btn btn-primary" onClick={onRetry} disabled={busy}>
          {busy ? "Trying…" : "Try again"}
        </button>
      </div>
    </AuthCard>
  );
}
