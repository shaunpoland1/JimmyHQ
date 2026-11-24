import { useQueryClient } from "@tanstack/react-query";
import { List, PenLine, RefreshCw } from "lucide-react";
import { useId, useState } from "react";
import { errorDetail } from "../../api";
import { EFFORTS, modelsApi } from "./api";
import type { Effort, Provider } from "./api";
import { MAX_MODEL_LENGTH } from "./lib/roles";
import type { TargetDraft } from "./lib/roles";
import { MODEL_KEYS, useProviderModelsQuery } from "./queries";

type TargetEditorProps = {
  /** Names the line for screen readers, e.g. "First choice" or "Fallback 2". */
  name: string;
  value: TargetDraft;
  onChange: (value: TargetDraft) => void;
  providers: readonly Provider[];
  error?: string | null;
  /** Put the keyboard here when the dialog opens. */
  focusFirst?: boolean;
};

const TYPE_A_NAME = "__type_a_name__";

/** One provider + model + effort line. Models come from the provider's list, or are typed. */
export function TargetEditor({ name, value, onChange, providers, error, focusFirst = false }: TargetEditorProps) {
  const client = useQueryClient();
  const id = useId();
  const [typing, setTyping] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const known = providers.find((provider) => provider.id === value.provider_id);
  const models = useProviderModelsQuery(value.provider_id, known !== undefined);
  const listed = models.data?.models ?? [];
  const canPick = listed.length > 0;
  const picking = canPick && !typing && (value.model === "" || listed.includes(value.model));

  const refresh = async () => {
    if (!known) return;
    setRefreshing(true);
    try {
      await client.fetchQuery({
        queryKey: MODEL_KEYS.providerModels(known.id),
        queryFn: () => modelsApi.providerModels(known.id, true),
        staleTime: 0,
        retry: false,
      });
    } catch {
      // The query now holds the error and the hint below shows it.
    } finally {
      setRefreshing(false);
    }
  };

  let hint: string | null = null;
  if (!value.provider_id) hint = null;
  else if (!known) hint = "This provider no longer exists. Choose another.";
  else if (models.isPending) hint = "Loading this provider's models…";
  else if (models.isError) hint = `${errorDetail(models.error)} You can still type the model's name.`;
  else if (!canPick) hint = "This provider lists no models. Type the model's name.";

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <div className="grid grid-cols-1 gap-2 @md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6.5rem]">
        <select
          className="input pr-7"
          aria-label={`${name}: provider`}
          data-autofocus={focusFirst ? true : undefined}
          value={value.provider_id}
          onChange={(event) => {
            setTyping(false);
            onChange({ ...value, provider_id: event.target.value, model: "" });
          }}
        >
          <option value="">Choose a provider…</option>
          {value.provider_id && !known ? <option value={value.provider_id}>{value.provider_id} (removed)</option> : null}
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id} disabled={!provider.enabled && provider.id !== value.provider_id}>
              {provider.label}
              {provider.enabled ? "" : " (switched off)"}
            </option>
          ))}
        </select>
        <div className="flex min-w-0 items-center gap-1">
          {picking ? (
            <select
              className="input pr-7 font-mono"
              aria-label={`${name}: model`}
              aria-describedby={hint ? `${id}-hint` : undefined}
              value={value.model}
              onChange={(event) => {
                if (event.target.value === TYPE_A_NAME) setTyping(true);
                else onChange({ ...value, model: event.target.value });
              }}
            >
              <option value="">Choose a model…</option>
              {listed.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
              <option value={TYPE_A_NAME}>Type a name…</option>
            </select>
          ) : (
            <input
              className="input font-mono"
              aria-label={`${name}: model`}
              aria-describedby={hint ? `${id}-hint` : undefined}
              value={value.model}
              onChange={(event) => onChange({ ...value, model: event.target.value })}
              placeholder="Model name"
              maxLength={MAX_MODEL_LENGTH}
              disabled={!value.provider_id}
              autoComplete="off"
              spellCheck={false}
            />
          )}
          {canPick && !picking ? (
            <button
              type="button"
              className="icon-btn"
              aria-label={`${name}: choose the model from the list`}
              title="Choose from the list"
              onClick={() => {
                setTyping(false);
                if (!listed.includes(value.model)) onChange({ ...value, model: "" });
              }}
            >
              <List size={14} />
            </button>
          ) : canPick ? (
            <button type="button" className="icon-btn" aria-label={`${name}: type the model's name`} title="Type a name" onClick={() => setTyping(true)}>
              <PenLine size={14} />
            </button>
          ) : null}
          <button type="button" className="icon-btn" aria-label={`${name}: refresh the model list`} title="Refresh the model list" onClick={() => void refresh()} disabled={!known || refreshing}>
            <RefreshCw size={14} className={refreshing ? "animate-spin" : undefined} />
          </button>
        </div>
        <select className="input pr-7" aria-label={`${name}: effort`} value={value.effort} onChange={(event) => onChange({ ...value, effort: event.target.value as Effort | "" })}>
          <option value="">Effort: not set</option>
          {EFFORTS.map((effort) => (
            <option key={effort} value={effort}>
              {effort}
            </option>
          ))}
        </select>
      </div>
      {hint ? (
        <p id={`${id}-hint`} className="break-words text-xs text-text-faint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
