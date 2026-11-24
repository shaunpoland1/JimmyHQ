import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { EFFORTS, modelsApi } from "./api";
import type { Effort, Provider, ProviderTest, ProvidersResponse } from "./api";
import {
  KINDS,
  LOCAL_SUGGESTIONS,
  changeBody,
  createBody,
  draftFrom,
  emptyDraft,
  isEmptyChange,
  keyError,
  kindInfo,
  missingKey,
  slugify,
  switchKind,
  testSentence,
  validateDraft,
} from "./lib/providers";
import type { DraftErrors, Locality, NativeJson, ProviderDraft, ProviderKind } from "./lib/providers";
import { MODEL_KEYS } from "./queries";
import { SMALL_BTN, SelectField } from "./ui";

type ProviderDialogProps = { mode: "add"; provider?: undefined; onClose: () => void } | { mode: "edit"; provider: Provider; onClose: () => void };

const LOCAL_PROMISE =
  "Local is a promise that prompts sent here stay on machines you control. Local-only work may use it and its tokens count as free, so only say local when that is true.";

/** Add or change a provider. Nothing is created or contacted until Save. */
export function ProviderDialog({ provider, onClose }: ProviderDialogProps) {
  const client = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<ProviderDraft>(() => (provider ? draftFrom(provider) : emptyDraft("local")));
  const [key, setKey] = useState("");
  const [replacingKey, setReplacingKey] = useState(false);
  const [confirmingKeyRemoval, setConfirmingKeyRemoval] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [test, setTest] = useState<{ pending: true } | { pending: false; result: ProviderTest } | { pending: false; failure: string } | null>(null);
  // After a provider is created but a later step fails, the dialog carries on as an edit.
  const [created, setCreated] = useState<Provider | null>(null);

  const saved = provider ?? created ?? null;
  const editing = saved !== null;
  const info = kindInfo(draft.kind);
  const errors: DraftErrors = validateDraft(draft, editing ? "edit" : "add");
  const hasKey = saved?.has_key === true;
  const keyFieldShown = info.key !== "none" && (!hasKey || replacingKey);
  const keyProblem = keyFieldShown && key.trim() ? keyError(key) : null;
  const shown = (field: keyof DraftErrors) => (showErrors ? errors[field] : undefined);
  const change = saved ? changeBody(draft, saved) : null;
  const dirty = saved ? !isEmptyChange(change ?? {}) || key.trim() !== "" : true;

  const set = (patch: Partial<ProviderDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setServerError(null);
  };

  const store = (next: Provider) => {
    client.setQueryData<ProvidersResponse>(MODEL_KEYS.providers, (data) => {
      if (!data) return data;
      const others = data.providers.filter((item) => item.id !== next.id);
      return { providers: [...others, next].sort((a, b) => a.label.toLowerCase().localeCompare(b.label.toLowerCase())) };
    });
  };

  const refresh = () => {
    void client.invalidateQueries({ queryKey: MODEL_KEYS.providers });
    void client.invalidateQueries({ queryKey: MODEL_KEYS.health });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setShowErrors(true);
    setServerError(null);
    if (Object.keys(errors).length > 0 || keyProblem) return;
    setSaving(true);
    let current = saved ?? null;
    try {
      if (!current) {
        current = (await modelsApi.createProvider(createBody(draft))).provider;
        setCreated(current);
        setDraft(draftFrom(current));
        store(current);
      } else if (change && !isEmptyChange(change)) {
        current = (await modelsApi.updateProvider(current.id, change)).provider;
        store(current);
        if (change.base_url !== undefined || change.region !== undefined || change.profile !== undefined) {
          // The cached model list belongs to the old address or account.
          void client.invalidateQueries({ queryKey: MODEL_KEYS.providerModels(current.id) });
        }
      }
      const typed = key.trim();
      if (keyFieldShown && typed) {
        await modelsApi.setKey(current.id, typed);
        current = { ...current, has_key: true };
        store(current);
        // The server forgot what it knew: that was learned with the old key, or with none.
        void client.invalidateQueries({ queryKey: MODEL_KEYS.providerModels(current.id) });
      }
      // The key never stays in the page once it is saved.
      setKey("");
      setReplacingKey(false);
      const label = current.label;
      if (saved) {
        toast.success(`Saved ${label}.`);
      } else if (missingKey(current)) {
        toast.success(`Added ${label}. Save a key before using it.`);
      } else {
        // A new provider is tested straight away so the list shows whether it works.
        try {
          const result = await modelsApi.testProvider(current.id);
          if (result.ok) toast.success(`Added ${label}. ${testSentence(result)}`);
          else toast.error(`Added ${label}, but the test failed. ${testSentence(result)}`);
        } catch (error) {
          toast.error(`Added ${label}, but it could not be tested. ${errorDetail(error)}`);
        }
      }
      refresh();
      onClose();
    } catch (error) {
      setServerError(errorDetail(error));
      refresh();
    } finally {
      setSaving(false);
    }
  };

  const removeKey = async () => {
    if (!saved) return;
    setKeyBusy(true);
    try {
      await modelsApi.clearKey(saved.id);
      const next = { ...saved, has_key: false };
      store(next);
      void client.invalidateQueries({ queryKey: MODEL_KEYS.providerModels(saved.id) });
      if (created) setCreated(next);
      toast.success(`Removed the key for ${saved.label}.`);
      setConfirmingKeyRemoval(false);
      refresh();
    } catch (error) {
      toast.error(errorDetail(error));
    } finally {
      setKeyBusy(false);
    }
  };

  const runTest = async () => {
    if (!saved) return;
    setTest({ pending: true });
    try {
      setTest({ pending: false, result: await modelsApi.testProvider(saved.id) });
    } catch (error) {
      setTest({ pending: false, failure: errorDetail(error) });
    }
    refresh();
  };

  const applySuggestion = (name: string, address: string) => {
    const untouched = draft.label.trim() === "" || draft.label === info.defaultLabel || LOCAL_SUGGESTIONS.some((item) => item.name === draft.label);
    set({ base_url: address, label: untouched ? name : draft.label });
  };

  const addressHint =
    info.address === "optional"
      ? "Leave empty to use the vendor's public address."
      : draft.kind === "local"
        ? "Where the server answers, including /v1 if it uses it."
        : "The endpoint's base address, usually ending in /v1.";

  return (
    <Modal title={editing ? `Configure ${saved.label}` : "Add a provider"} onClose={onClose} widthClass="max-w-lg">
      <form className="flex min-h-0 flex-1 flex-col" onSubmit={(event) => void submit(event)} noValidate>
        <div className="@container flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
          {editing ? (
            <p className="text-xs text-text-muted">
              {info.title} · id <span className="font-mono">{saved.id}</span>. The type and id cannot be changed.
            </p>
          ) : (
            <SelectField label="Type" value={draft.kind} hint={info.hint} onChange={(event) => setDraft((current) => switchKind(current, event.target.value as ProviderKind))} data-autofocus>
              {KINDS.map((item) => (
                <option key={item.kind} value={item.kind}>
                  {item.title}
                </option>
              ))}
            </SelectField>
          )}

          <Field label="Name" value={draft.label} onChange={(event) => set({ label: event.target.value })} error={shown("label")} maxLength={80} autoComplete="off" data-autofocus={editing ? true : undefined} />

          {info.address !== "none" ? (
            <div className="flex flex-col gap-2">
              <Field
                label={info.address === "optional" ? "Address (optional)" : "Address"}
                value={draft.base_url}
                onChange={(event) => set({ base_url: event.target.value })}
                error={shown("base_url")}
                hint={addressHint}
                placeholder={info.address === "optional" ? "" : "http://127.0.0.1:11434/v1"}
                inputMode="url"
                autoComplete="off"
                spellCheck={false}
              />
              {draft.kind === "local" && !editing ? (
                <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Usual addresses of well-known local servers">
                  <span className="text-xs text-text-faint">Suggestions:</span>
                  {LOCAL_SUGGESTIONS.map((item) => (
                    <button key={item.name} type="button" className={SMALL_BTN} title={item.address} aria-label={`Use the usual ${item.name} address, ${item.address}`} onClick={() => applySuggestion(item.name, item.address)}>
                      {item.name}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {info.type === "bedrock" ? (
            <div className="grid grid-cols-1 gap-4 @md:grid-cols-2">
              <Field label="AWS region" value={draft.region} onChange={(event) => set({ region: event.target.value })} error={shown("region")} placeholder="us-east-1" autoComplete="off" spellCheck={false} />
              <Field
                label="Credentials profile (optional)"
                value={draft.profile}
                onChange={(event) => set({ profile: event.target.value })}
                error={shown("profile")}
                hint="Empty uses the default AWS credentials."
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          ) : null}

          {info.key === "none" ? null : hasKey && !replacingKey ? (
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-text">API key</span>
              {confirmingKeyRemoval ? (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2" role="group" aria-label="Remove the saved key">
                  <p className="min-w-0 flex-1 basis-40 text-xs text-text-muted">Remove the saved key? {saved?.label} will stop answering if it needs one.</p>
                  <button type="button" className={`${SMALL_BTN} btn-danger`} onClick={() => void removeKey()} disabled={keyBusy}>
                    Remove key
                  </button>
                  <button type="button" className={SMALL_BTN} onClick={() => setConfirmingKeyRemoval(false)}>
                    Keep it
                  </button>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2">
                  <p className="min-w-0 flex-1 basis-40 text-xs text-text-muted">A key is saved. It is never shown again.</p>
                  <button type="button" className={SMALL_BTN} onClick={() => setReplacingKey(true)}>
                    Replace
                  </button>
                  <button type="button" className={SMALL_BTN} onClick={() => setConfirmingKeyRemoval(true)}>
                    Remove
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Field
                label={replacingKey ? "New API key" : info.key === "optional" ? "API key (optional)" : "API key"}
                type="password"
                value={key}
                onChange={(event) => {
                  setKey(event.target.value);
                  setServerError(null);
                }}
                error={showErrors && keyProblem ? keyProblem : undefined}
                hint={
                  info.key === "optional"
                    ? "Only if the server asks for one. Saved in the secret store and never shown again."
                    : "Saved in the secret store and never shown again."
                }
                autoComplete="off"
                spellCheck={false}
              />
              {replacingKey ? (
                <button
                  type="button"
                  className="self-start rounded text-xs text-accent hover:underline"
                  onClick={() => {
                    setReplacingKey(false);
                    setKey("");
                  }}
                >
                  Keep the saved key
                </button>
              ) : null}
            </div>
          )}

          <SelectField label="Where it runs" value={draft.locality} hint={LOCAL_PROMISE} onChange={(event) => set({ locality: event.target.value as Locality })}>
            <option value="auto">{editing ? "Decide again from the address" : "Decide from the address"}</option>
            <option value="local">Local: stays on my machines</option>
            <option value="hosted">Hosted: leaves my machines</option>
          </SelectField>

          <details className="rounded-lg border border-border">
            <summary className="cursor-pointer rounded-lg px-3 py-2 text-xs font-medium text-text">Advanced</summary>
            <div className="grid grid-cols-1 gap-4 border-t border-border px-3 py-3 @md:grid-cols-2">
              {editing ? null : (
                <div className="@md:col-span-2">
                  <Field
                    label="Id (optional)"
                    value={draft.id}
                    onChange={(event) => set({ id: event.target.value })}
                    error={shown("id")}
                    hint={`Made from the name if left empty: ${slugify(draft.label)}. It cannot be changed later.`}
                    maxLength={40}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
              )}
              <Field
                label="Longest answer (tokens)"
                value={draft.max_tokens}
                onChange={(event) => set({ max_tokens: event.target.value })}
                error={shown("max_tokens")}
                hint="Empty uses the general setting."
                inputMode="numeric"
                autoComplete="off"
              />
              <Field
                label="Temperature"
                value={draft.temperature}
                onChange={(event) => set({ temperature: event.target.value })}
                error={shown("temperature")}
                hint="0 to 2. Empty leaves it to the provider."
                inputMode="decimal"
                autoComplete="off"
              />
              <SelectField label="Effort" value={draft.effort} hint="Used when a role does not set one." onChange={(event) => set({ effort: event.target.value as Effort | "" })}>
                <option value="">Not set</option>
                {EFFORTS.map((effort) => (
                  <option key={effort} value={effort}>
                    {effort}
                  </option>
                ))}
              </SelectField>
              <SelectField label="Structured answers" value={draft.native_json} hint="Whether to use the provider's own JSON mode." onChange={(event) => set({ native_json: event.target.value as NativeJson })}>
                <option value="auto">Let the provider type decide</option>
                <option value="yes">Use its JSON mode</option>
                <option value="no">Ask in the prompt instead</option>
              </SelectField>
            </div>
          </details>

          {test && !test.pending ? (
            <p className={`break-words text-xs ${"result" in test && test.result.ok ? "text-success" : "text-danger"}`} role="status">
              {"result" in test ? testSentence(test.result) : test.failure}
            </p>
          ) : null}
          {serverError ? (
            <p className="break-words text-xs text-danger" role="alert">
              {serverError}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
          {editing ? (
            <button
              type="button"
              className="btn mr-auto"
              onClick={() => void runTest()}
              disabled={test?.pending === true || saving || dirty}
              title={dirty ? "Save your changes first: the test uses what is saved." : undefined}
            >
              {test?.pending ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
              Test
            </button>
          ) : null}
          <button type="button" className="btn" onClick={onClose}>
            {editing && !dirty ? "Close" : "Cancel"}
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving || (editing && !dirty)}>
            {saving ? "Saving…" : editing ? "Save" : "Add provider"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
