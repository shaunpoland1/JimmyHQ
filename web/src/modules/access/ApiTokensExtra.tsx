import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeySquare } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { copyToClipboard } from "../../lib/browserClipboard";
import { formatFullTime } from "../../lib/format";
import { accessApi } from "./api";
import type { ApiToken, MadeToken, TokenLevel } from "./api";
import { LEVELS, MAX_TOKEN_LABEL, countLine, labelProblem, levelLabel, madeLine, ordered, revokeMessage, revokedLine } from "./lib/tokens";

const CARD = "@container rounded-xl border border-border bg-surface";
const TOKENS_KEY = ["access", "tokens"] as const;

/** Shown under Settings, Access: the API tokens scripts and screens use, made, listed and revoked by the owner. Owned by the access module. */
export function ApiTokensExtra() {
  const cache = useQueryClient();
  const tokens = useQuery({ queryKey: TOKENS_KEY, queryFn: accessApi.listTokens, staleTime: 15_000 });
  const [making, setMaking] = useState(false);
  const [made, setMade] = useState<MadeToken | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const revoke = useMutation({
    mutationFn: (token: ApiToken) => accessApi.revokeToken(token.id),
    onSuccess: (result) => {
      setError(null);
      setDone(revokedLine(result.token));
      setRevoking(null);
      void cache.invalidateQueries({ queryKey: TOKENS_KEY });
    },
    onError: (failure) => {
      setDone(null);
      setError(errorDetail(failure));
      setRevoking(null);
    },
  });

  const all = tokens.data?.tokens ?? [];
  const { live, revoked } = ordered(all);

  return (
    <div className={`${CARD} flex flex-col gap-3 px-4 py-3.5`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
          <KeySquare size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-semibold text-text">API tokens</h3>
          <p className="text-xs text-text-muted">For a script or a screen that uses JimmyHQ without your password. A token never changes settings, passwords or other tokens.</p>
        </div>
        <button type="button" className="btn" onClick={() => setMaking(true)}>
          Make a token
        </button>
      </div>
      {tokens.isError ? (
        <p className="text-xs text-text" role="alert">
          {errorDetail(tokens.error)}
        </p>
      ) : tokens.isPending ? (
        <p className="text-xs text-text-muted">Loading the tokens…</p>
      ) : (
        <p className="text-xs text-text-muted">{countLine(all)}</p>
      )}
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
      {live.length > 0 ? (
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {live.map((token) => (
            <li key={token.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="break-words text-xs font-medium text-text">{token.label}</p>
                <p className="text-[11px] text-text-faint">
                  {levelLabel(token.level)} · made {formatFullTime(token.created)}
                </p>
              </div>
              <button type="button" className="btn" onClick={() => setRevoking(token)}>
                Revoke
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {revoked.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs text-text-muted">
            Revoked ({revoked.length})
          </summary>
          <ul className="mt-2 flex flex-col gap-1">
            {revoked.map((token) => (
              <li key={token.id} className="break-words text-[11px] text-text-faint">
                {token.label} · {levelLabel(token.level)} · revoked {formatFullTime(token.revoked ?? token.created)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {making ? (
        <MakeTokenDialog
          onClose={() => setMaking(false)}
          onMade={(answer) => {
            setMaking(false);
            setMade(answer);
            setError(null);
            setDone(madeLine(answer.token));
            void cache.invalidateQueries({ queryKey: TOKENS_KEY });
          }}
        />
      ) : null}
      {made ? <TokenShownOnce answer={made} onClose={() => setMade(null)} /> : null}
      {revoking ? (
        <ConfirmDialog
          title={`Revoke “${revoking.label}”?`}
          message={revokeMessage(revoking)}
          confirmLabel={revoke.isPending ? "Revoking…" : "Revoke"}
          danger
          busy={revoke.isPending}
          onConfirm={() => revoke.mutate(revoking)}
          onCancel={() => setRevoking(null)}
        />
      ) : null}
    </div>
  );
}

function MakeTokenDialog({ onClose, onMade }: { onClose: () => void; onMade: (answer: MadeToken) => void }) {
  const name = useId();
  const [label, setLabel] = useState("");
  const [level, setLevel] = useState<TokenLevel>("reader");
  const [problem, setProblem] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const make = useMutation({
    mutationFn: () => accessApi.makeToken(label.trim(), level),
    onSuccess: onMade,
    onError: (error) => setFailure(errorDetail(error)),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = labelProblem(label);
    setProblem(found);
    setFailure(null);
    if (!found) make.mutate();
  };

  return (
    <Modal title="Make an API token" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <div className="flex flex-col gap-4 px-4 py-4">
          <Field
            label="Label"
            name="token-label"
            autoComplete="off"
            maxLength={MAX_TOKEN_LABEL}
            data-autofocus
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            hint="What will use it, such as the hallway screen. Only you see this."
            error={problem ?? undefined}
          />
          <fieldset className="flex flex-col gap-2 text-sm text-text">
            <legend className="mb-1 text-xs font-medium text-text">What it may do</legend>
            {LEVELS.map((entry) => (
              <label key={entry.value} className="flex cursor-pointer items-start gap-2.5">
                <input type="radio" name={name} className="mt-1 accent-accent" checked={level === entry.value} onChange={() => setLevel(entry.value)} />
                <span>
                  <span className="font-medium">{entry.label}</span>
                  <span className="block text-xs text-text-muted">{entry.help}</span>
                </span>
              </label>
            ))}
          </fieldset>
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
          <button type="submit" className="btn btn-primary" disabled={make.isPending}>
            {make.isPending ? "Making…" : "Make and show the token"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** The one time a token is on screen. It is held in this dialog's state only. */
function TokenShownOnce({ answer, onClose }: { answer: MadeToken; onClose: () => void }) {
  const toast = useToast();
  const copy = () => {
    void copyToClipboard(answer.secret).then((copied) =>
      copied ? toast.success("The token copied.") : toast.error("The token could not be copied. Select it and copy it by hand."),
    );
  };
  return (
    <Modal title={`The token for ${answer.token.label}`} onClose={onClose} widthClass="max-w-lg">
      <div className="flex flex-col gap-3 px-4 py-4">
        <p className="text-sm text-text-muted">
          <span className="font-medium text-text">This is shown once.</span> JimmyHQ does not keep it and cannot show it again. Send it as a bearer token: an Authorization header that reads “Bearer ” and then the token.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{answer.secret}</code>
          <button type="button" className="btn" onClick={copy} data-autofocus>
            <Copy size={14} aria-hidden="true" /> Copy
          </button>
        </div>
        <p className="text-xs text-text-muted">Anyone who has this token can do what a {levelLabel(answer.token.level).toLowerCase()} can. If it is ever seen by someone else, revoke it and make a new one.</p>
      </div>
      <div className="flex justify-end border-t border-border px-4 py-3">
        <button type="button" className="btn btn-primary" onClick={onClose}>
          I have saved it
        </button>
      </div>
    </Modal>
  );
}
