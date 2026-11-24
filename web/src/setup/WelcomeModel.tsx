import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleCheck } from "lucide-react";
import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../api";
import { chatModelReady } from "../lib/welcome";
import { modelsApi } from "../modules/models/api";
import type { Provider, RoleEntry, TargetInput } from "../modules/models/api";
import { emptyTarget, targetError, toDraft, toInput } from "../modules/models/lib/roles";
import type { TargetDraft } from "../modules/models/lib/roles";
import { ProvidersSection } from "../modules/models/ProvidersSection";
import { MODEL_KEYS, useModelsLive, useProvidersQuery, useRolesQuery } from "../modules/models/queries";
import { TargetEditor } from "../modules/models/TargetEditor";
import { TryDialog } from "../modules/models/TryDialog";
import type { TryState } from "../modules/models/TryDialog";

/** Chat asks for this role. */
const CHAT_ROLE = "default";
const TEST_MESSAGE = "Reply with one short sentence to show you are working.";

type WelcomeModelProps = {
  busy: boolean;
  failure: string | null;
  onBack: () => void;
  /** `skipped` is true for "I'll do this later". */
  onDone: (skipped: boolean) => void;
};

function startingDraft(role: RoleEntry | undefined, providers: readonly Provider[]): TargetDraft {
  if (role?.primary) return toDraft(role.primary);
  return emptyTarget(providers.find((provider) => provider.enabled)?.id ?? "");
}

/** Step 3: add a provider and choose the model chat uses, reusing the models module's own parts. */
export function WelcomeModel({ busy, failure, onBack, onDone }: WelcomeModelProps) {
  useModelsLive();
  const client = useQueryClient();
  const providers = useProvidersQuery();
  const roles = useRolesQuery();
  const list = providers.data?.providers ?? [];
  const chatRole = roles.data?.roles.find((entry) => entry.role === CHAT_ROLE);
  const ready = chatModelReady(roles.data?.roles ?? [], list);

  // Until the owner touches it, the line follows what is saved (or the first provider).
  const [draft, setDraft] = useState<TargetDraft | null>(null);
  const current = draft ?? startingDraft(chatRole, list);
  const [showError, setShowError] = useState(false);
  const [trying, setTrying] = useState<{ prompt: string; state: TryState } | null>(null);
  const run = useRef(0);
  const problem = targetError(current);
  const savedLine = chatRole?.primary ? toDraft(chatRole.primary) : null;
  const unsaved = draft !== null && (savedLine === null || JSON.stringify(savedLine) !== JSON.stringify(current));

  const save = useMutation({
    mutationFn: (primary: TargetInput) =>
      modelsApi.setRole(CHAT_ROLE, {
        primary,
        // Fallbacks someone set before are kept as they were.
        fallbacks: (chatRole?.fallbacks ?? []).map((target) => ({ provider_id: target.provider_id, model: target.model, effort: target.effort })),
      }),
    onSuccess: () => {
      setDraft(null);
      void client.invalidateQueries({ queryKey: MODEL_KEYS.roles });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setShowError(true);
    if (!problem) save.mutate(toInput(current));
  };

  const tryIt = async (prompt: string) => {
    const turn = ++run.current;
    setTrying({ prompt, state: { pending: true } });
    let state: TryState;
    try {
      state = { pending: false, result: await modelsApi.tryModel({ prompt, role: CHAT_ROLE }) };
    } catch (error) {
      state = { pending: false, failure: errorDetail(error) };
    }
    if (run.current === turn) setTrying((open) => (open ? { ...open, state } : open));
  };

  const closeTry = () => {
    run.current += 1;
    setTrying(null);
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="@container min-w-0 [&>section>*:first-child]:mt-0">
        <ProvidersSection roles={roles.data?.roles} />
      </div>

      <section aria-labelledby="welcome-chat-model" className="flex flex-col gap-2">
        <h2 id="welcome-chat-model" className="text-sm font-semibold">
          The model chat uses
        </h2>
        {list.length === 0 ? (
          <p className="text-xs text-text-faint">Add a provider above, then choose one of its models here.</p>
        ) : (
          <form onSubmit={submit} noValidate className="@container flex flex-col gap-2">
            <TargetEditor
              name="Chat model"
              value={current}
              onChange={(next) => {
                setDraft(next);
                save.reset();
              }}
              providers={list}
              error={showError ? problem : null}
            />
            <div className="flex flex-wrap items-center gap-2">
              <button type="submit" className="btn" disabled={save.isPending || (!unsaved && ready)}>
                {save.isPending ? "Saving…" : "Use this model for chat"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => void tryIt(TEST_MESSAGE)}
                disabled={!ready || unsaved}
                title={ready ? "Send a short test message to the chat model" : "Choose and save a model first."}
              >
                Test it
              </button>
              {ready && !unsaved ? (
                <span className="flex items-center gap-1 text-xs text-success" role="status">
                  <CircleCheck size={13} aria-hidden="true" />
                  Chat has a model.
                </span>
              ) : null}
            </div>
            {save.isError ? (
              <p role="alert" className="text-xs text-danger">
                {errorDetail(save.error)}
              </p>
            ) : null}
          </form>
        )}
      </section>

      {failure ? (
        <p role="alert" className="text-xs text-danger">
          {failure}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
        <button type="button" className="btn" onClick={onBack} disabled={busy}>
          Back
        </button>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn" onClick={() => onDone(true)} disabled={busy}>
            I'll do this later
          </button>
          <button type="button" className="btn btn-primary" onClick={() => onDone(false)} disabled={busy || !ready || unsaved}>
            {busy ? "Saving…" : "Continue"}
          </button>
        </div>
      </div>

      {trying ? <TryDialog role={CHAT_ROLE} prompt={trying.prompt} state={trying.state} providers={list} onRun={(prompt) => void tryIt(prompt)} onClose={closeTry} /> : null}
    </div>
  );
}
