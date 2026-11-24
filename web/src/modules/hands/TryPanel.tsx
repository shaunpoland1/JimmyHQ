import { Loader2 } from "lucide-react";
import { useState } from "react";
import { errorDetail } from "../../api";
import { handsApi } from "./api";
import { kindLabel, tryArguments } from "./lib/args";
import type { ArgRow } from "./lib/args";
import { Notice, SMALL_BTN } from "./ui";

type Props = { account: string; action: string; rows: ArgRow[] };

/** Runs a reading action once, as the owner, so they can see what it gives before an agent has it. What is typed is sent to the provider and is not kept. */
export function TryPanel({ account, action, rows }: Props) {
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [state, setState] = useState<{ pending: true } | { pending: false; ok: boolean; text: string; more: boolean } | null>(null);

  const run = async () => {
    const parsed = tryArguments(rows, typed);
    if (!parsed.ok) {
      setErrors(parsed.errors);
      return;
    }
    setErrors({});
    setState({ pending: true });
    try {
      const result = await handsApi.tryRead(account, action, parsed.args);
      setState({ pending: false, ok: result.ok, text: result.text, more: result.more });
    } catch (error) {
      setState({ pending: false, ok: false, text: errorDetail(error), more: false });
    }
  };

  return (
    <details className="rounded-lg border border-border">
      <summary className="cursor-pointer rounded-lg px-3 py-2 text-xs font-medium text-text">Try it first</summary>
      <div className="flex flex-col gap-3 border-t border-border px-3 py-3">
        <p className="text-xs text-text-muted">This action only reads, so you can run it once to see what an agent would get. What you type here goes to the provider and is not kept.</p>
        {rows.map((row) => (
          <div key={row.name} className="flex flex-col gap-1">
            <label htmlFor={`try-${row.name}`} className="text-xs font-medium text-text">
              {row.name}
              <span className="ml-2 font-normal text-text-faint">
                {kindLabel(row.kind)}
                {row.required ? ", needed" : ", optional"}
              </span>
            </label>
            <input id={`try-${row.name}`} className="input" value={typed[row.name] ?? ""} onChange={(event) => setTyped({ ...typed, [row.name]: event.target.value })} aria-invalid={errors[row.name] ? true : undefined} autoComplete="off" spellCheck={false} />
            {errors[row.name] ? (
              <p className="text-xs text-danger" role="alert">
                {errors[row.name]}
              </p>
            ) : null}
          </div>
        ))}
        <div className="flex">
          <button type="button" className={SMALL_BTN} onClick={() => void run()} disabled={state?.pending === true}>
            {state?.pending ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : null}
            Run once
          </button>
        </div>
        {state && !state.pending ? (
          <div className="flex flex-col gap-1.5" role="status">
            <Notice tone={state.ok ? "good" : "bad"}>{state.ok ? "It worked. This is what came back, with secrets and addresses taken out." : "It did not work."}</Notice>
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-text">{state.text}</pre>
            {state.more ? <p className="text-xs text-text-faint">There is more than this. An agent reads on in parts.</p> : null}
          </div>
        ) : null}
      </div>
    </details>
  );
}
