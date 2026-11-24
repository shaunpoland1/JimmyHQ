import { Loader2 } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import type { Provider, TryResult } from "./api";
import { formatCompact, formatDuration } from "./lib/format";
import { isLocal } from "./lib/providers";
import { LocalityTag } from "./ui";

export type TryState = { pending: true } | { pending: false; result: TryResult } | { pending: false; failure: string };

type TryDialogProps = {
  role: string;
  prompt: string;
  state: TryState;
  providers: readonly Provider[];
  onRun: (prompt: string) => void;
  onClose: () => void;
};

/** Shows a short test completion for a role: the reply, who answered, and whether a fallback stepped in. */
export function TryDialog({ role, prompt, state, providers, onRun, onClose }: TryDialogProps) {
  const [text, setText] = useState(prompt);
  const blank = text.trim() === "";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!blank && !state.pending) onRun(text);
  };

  const labelOf = (id: string) => providers.find((provider) => provider.id === id)?.label ?? id;

  let body;
  if (state.pending) {
    body = (
      <p className="flex items-center gap-2 text-sm text-text-muted">
        <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        Asking the {role} role…
      </p>
    );
  } else if ("failure" in state) {
    body = <p className="break-words text-sm text-danger">{state.failure}</p>;
  } else if (!state.result.ok) {
    body = (
      <p className="break-words text-sm text-danger">
        <span className="font-medium">No model answered.</span> {state.result.error}
      </p>
    );
  } else {
    const { reply } = state.result;
    body = (
      <div className="flex flex-col gap-3">
        <blockquote className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-bg px-3 py-2 text-sm">{reply.text || "(an empty reply)"}</blockquote>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-muted">
          <span>
            Answered by <span className="font-medium text-text">{labelOf(reply.provider_id)}</span>
          </span>
          <span className="min-w-0 break-all font-mono text-text">{reply.model}</span>
          <LocalityTag isLocal={isLocal(reply)} />
        </p>
        <p className="font-mono text-[11px] text-text-faint">
          {formatDuration(reply.duration_ms)} · {formatCompact(reply.input_tokens)} tok in · {formatCompact(reply.output_tokens)} tok out
        </p>
        {reply.fallback_from ? (
          <p className="break-words rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
            <span className="font-medium">A fallback answered.</span> The first choice (<span className="font-mono">{reply.fallback_from}</span>) was passed over
            {reply.fallback_reason ? `: ${reply.fallback_reason}` : "."}
          </p>
        ) : (
          <p className="text-xs text-success">The first choice answered. No fallback was needed.</p>
        )}
      </div>
    );
  }

  return (
    <Modal title={`Try the ${role} role`} onClose={onClose} widthClass="max-w-lg">
      <form className="flex min-h-0 flex-1 flex-col" onSubmit={submit}>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
          <Field label="Test message" value={text} onChange={(event) => setText(event.target.value)} maxLength={4000} hint="Sent once to the role. The text is not kept; only token counts are recorded." autoComplete="off" />
          <div aria-live="polite">{body}</div>
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onClose} data-autofocus>
            Close
          </button>
          <button type="submit" className="btn btn-primary" disabled={blank || state.pending}>
            Run again
          </button>
        </div>
      </form>
    </Modal>
  );
}
