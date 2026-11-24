import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, TriangleAlert, X } from "lucide-react";
import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { modelsApi } from "./api";
import type { Provider, RoleBody, RoleEntry } from "./api";
import { MAX_FALLBACKS, checkRole, draftRole, emptyTarget, moveItem, removeAt, replaceAt, roleUse, roleWarnings, toInput } from "./lib/roles";
import type { RoleDraft, TargetDraft } from "./lib/roles";
import { MODEL_KEYS } from "./queries";
import { TargetEditor } from "./TargetEditor";
import { SMALL_BTN } from "./ui";

type RoleDialogProps = {
  entry: RoleEntry;
  providers: readonly Provider[];
  onClose: () => void;
};

function asTarget(draft: TargetDraft) {
  const input = toInput(draft);
  return { provider_id: input.provider_id, model: input.model, effort: input.effort ?? null };
}

/** Choose a role's first choice and its ordered fallbacks. */
export function RoleDialog({ entry, providers, onClose }: RoleDialogProps) {
  const client = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<RoleDraft>(() => draftRole(entry));
  const [showErrors, setShowErrors] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  // A stable key per fallback line, kept in step with the draft, so a line's
  // own state (typing a name, refreshing) follows it when it moves.
  const [keys, setKeys] = useState<number[]>(() => entry.fallbacks.map((_, index) => index));
  const nextKey = useRef(entry.fallbacks.length);

  const check = checkRole(draft);
  const change = (next: RoleDraft) => {
    setDraft(next);
    setServerError(null);
  };

  const save = useMutation({
    mutationFn: (body: RoleBody) => modelsApi.setRole(entry.role, body),
    onSuccess: (saved) => {
      toast.success(saved.primary ? `Saved the ${entry.role} role.` : `Cleared the ${entry.role} role.`);
      void client.invalidateQueries({ queryKey: MODEL_KEYS.roles });
      onClose();
    },
    onError: (error) => setServerError(errorDetail(error)),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setShowErrors(true);
    setServerError(null);
    if (check.ok) save.mutate(check.body);
  };

  // Warnings follow the draft, so the owner sees them before saving.
  const complete = draft.primary && draft.primary.provider_id && draft.primary.model.trim();
  const warnings = complete && draft.primary
    ? roleWarnings({ primary: asTarget(draft.primary), fallbacks: draft.fallbacks.filter((item) => item.provider_id).map(asTarget) }, providers)
    : [];
  const firstEnabled = providers.find((provider) => provider.enabled)?.id ?? "";
  const use = roleUse(entry.role);

  return (
    <Modal title={`The ${entry.role} role`} onClose={onClose} widthClass="max-w-2xl">
      <form className="flex min-h-0 flex-1 flex-col" onSubmit={submit} noValidate>
        <div className="@container flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
          <p className="text-xs text-text-muted">
            {use ? `Used for ${use}. ` : ""}
            The first choice answers; if it cannot, the fallbacks are tried in order.
            {entry.role !== "default" && entry.role !== "embeddings" ? " A role with nothing chosen uses the default role's models." : ""}
          </p>

          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-2 text-xs font-medium text-text">First choice</legend>
            {draft.primary ? (
              <div className="flex items-start gap-1">
                <TargetEditor
                  name="First choice"
                  value={draft.primary}
                  onChange={(primary) => change({ ...draft, primary })}
                  providers={providers}
                  error={showErrors && !check.ok ? check.primary : null}
                  focusFirst
                />
                <button type="button" className="icon-btn mt-1" aria-label="Clear this role" title="Clear this role" onClick={() => {
                    change({ primary: null, fallbacks: [] });
                    setKeys([]);
                  }}
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2">
                <p className="min-w-0 flex-1 basis-40 text-xs text-text-muted">Nothing is chosen for this role.</p>
                <button type="button" className={SMALL_BTN} onClick={() => change({ ...draft, primary: emptyTarget(firstEnabled) })} data-autofocus>
                  Choose a model
                </button>
              </div>
            )}
          </fieldset>

          <fieldset className="flex min-w-0 flex-col gap-2" disabled={!draft.primary}>
            <legend className="mb-2 text-xs font-medium text-text">Fallbacks, in the order they are tried</legend>
            {draft.fallbacks.length === 0 ? <p className="text-xs text-text-faint">No fallbacks. If the first choice cannot answer, the request fails.</p> : null}
            <ol className="flex flex-col gap-3">
              {draft.fallbacks.map((fallback, index) => {
                const name = `Fallback ${index + 1}`;
                return (
                  <li key={keys[index] ?? `at-${index}`} className="flex items-start gap-1">
                    <span className="mt-2 w-5 flex-none font-mono text-xs text-text-faint" aria-hidden="true">
                      {index + 1}.
                    </span>
                    <TargetEditor
                      name={name}
                      value={fallback}
                      onChange={(next) => change({ ...draft, fallbacks: replaceAt(draft.fallbacks, index, next) })}
                      providers={providers}
                      error={showErrors && !check.ok ? check.fallbacks[index] : null}
                    />
                    <div className="mt-1 flex flex-none flex-col @md:flex-row">
                      <button type="button" className="icon-btn" aria-label={`Move ${name.toLowerCase()} up`} disabled={index === 0}
                        onClick={() => {
                          change({ ...draft, fallbacks: moveItem(draft.fallbacks, index, -1) });
                          setKeys(moveItem(keys, index, -1));
                        }}
                      >
                        <ArrowUp size={14} />
                      </button>
                      <button
                        type="button"
                        className="icon-btn"
                        aria-label={`Move ${name.toLowerCase()} down`}
                        disabled={index === draft.fallbacks.length - 1}
                        onClick={() => {
                          change({ ...draft, fallbacks: moveItem(draft.fallbacks, index, 1) });
                          setKeys(moveItem(keys, index, 1));
                        }}
                      >
                        <ArrowDown size={14} />
                      </button>
                      <button type="button" className="icon-btn" aria-label={`Remove ${name.toLowerCase()}`} onClick={() => {
                          change({ ...draft, fallbacks: removeAt(draft.fallbacks, index) });
                          setKeys(removeAt(keys, index));
                        }}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ol>
            <button
              type="button"
              className={`${SMALL_BTN} self-start`}
              disabled={draft.fallbacks.length >= MAX_FALLBACKS}
              onClick={() => {
                change({ ...draft, fallbacks: [...draft.fallbacks, emptyTarget(firstEnabled)] });
                nextKey.current += 1;
                setKeys([...keys, nextKey.current]);
              }}
            >
              <Plus size={13} aria-hidden="true" />
              Add fallback
            </button>
          </fieldset>

          {warnings.length > 0 ? (
            <ul className="flex flex-col gap-1" aria-label="Warnings">
              {warnings.map((warning) => (
                <li key={warning} className="flex items-start gap-1.5 text-xs text-warning">
                  <TriangleAlert size={13} className="mt-0.5 flex-none" aria-hidden="true" />
                  <span className="min-w-0 break-words">{warning}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {serverError || (showErrors && !check.ok) ? (
            <p className="break-words text-xs text-danger" role="alert">
              {serverError ?? (check.ok ? "" : check.summary)}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
