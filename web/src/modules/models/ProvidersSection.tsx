import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plug, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState } from "../../components/EmptyState";
import { Modal } from "../../components/Modal";
import { Switch } from "../../components/Switch";
import { useToast } from "../../hooks/useToast";
import { modelsApi } from "./api";
import type { HealthEntry, Provider, ProviderTest, ProvidersResponse, RoleEntry } from "./api";
import { formatChecked } from "./lib/format";
import { isLocal, monogram, providerDetail, providerStatus, testSentence } from "./lib/providers";
import { roleList, rolesUsing } from "./lib/roles";
import { ProviderDialog } from "./ProviderDialog";
import { MODEL_KEYS, useHealthQuery, useProvidersQuery } from "./queries";
import { ErrorCard, LoadingCard, LocalityTag, Pill, SMALL_BTN, SectionHeading } from "./ui";

type TestState = { pending: true } | { pending: false; result: ProviderTest } | { pending: false; failure: string };

type ProvidersSectionProps = {
  /** Roles, when loaded, so removing a provider can say which roles point at it. */
  roles: RoleEntry[] | undefined;
};

/** The list of providers: status, switch, test, configure and remove. */
export function ProvidersSection({ roles }: ProvidersSectionProps) {
  const client = useQueryClient();
  const toast = useToast();
  const providers = useProvidersQuery();
  const health = useHealthQuery();
  const [editing, setEditing] = useState<{ mode: "add" } | { mode: "edit"; id: string } | null>(null);
  const [removing, setRemoving] = useState<Provider | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});

  const refresh = () => {
    void client.invalidateQueries({ queryKey: MODEL_KEYS.providers });
    void client.invalidateQueries({ queryKey: MODEL_KEYS.health });
  };

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => modelsApi.updateProvider(id, { enabled }),
    onMutate: async ({ id, enabled }) => {
      await client.cancelQueries({ queryKey: MODEL_KEYS.providers });
      const previous = client.getQueryData<ProvidersResponse>(MODEL_KEYS.providers);
      client.setQueryData<ProvidersResponse>(MODEL_KEYS.providers, (data) =>
        data ? { providers: data.providers.map((item) => (item.id === id ? { ...item, enabled } : item)) } : data,
      );
      return { previous };
    },
    onError: (error, _input, context) => {
      if (context?.previous) client.setQueryData(MODEL_KEYS.providers, context.previous);
      toast.error(errorDetail(error));
    },
    onSettled: refresh,
  });

  const remove = useMutation({
    mutationFn: (provider: Provider) => modelsApi.removeProvider(provider.id),
    onSuccess: (_result, provider) => {
      toast.success(`Removed ${provider.label}.`);
      client.removeQueries({ queryKey: MODEL_KEYS.providerModels(provider.id) });
      setRemoving(null);
    },
    onError: (error) => {
      toast.error(errorDetail(error));
      setRemoving(null);
    },
    onSettled: refresh,
  });

  const runTest = async (provider: Provider) => {
    setTests((current) => ({ ...current, [provider.id]: { pending: true } }));
    try {
      const result = await modelsApi.testProvider(provider.id);
      setTests((current) => ({ ...current, [provider.id]: { pending: false, result } }));
    } catch (error) {
      setTests((current) => ({ ...current, [provider.id]: { pending: false, failure: errorDetail(error) } }));
    }
    refresh();
  };

  const list = providers.data?.providers ?? [];
  const healthById = new Map<string, HealthEntry>((health.data?.providers ?? []).map((entry) => [entry.id, entry]));
  const statusOf = (provider: Provider) => providerStatus(provider, healthById.get(provider.id) ?? provider.health);
  const connected = list.filter((provider) => statusOf(provider).tone === "ok").length;
  const edited = editing?.mode === "edit" ? list.find((provider) => provider.id === editing.id) : undefined;
  const usedBy = removing && roles ? rolesUsing(roles, removing.id) : [];

  return (
    <section aria-labelledby="models-providers-heading">
      <SectionHeading
        id="models-providers-heading"
        aside={
          <button type="button" className={SMALL_BTN} onClick={() => setEditing({ mode: "add" })}>
            <Plus size={13} aria-hidden="true" />
            Add provider
          </button>
        }
      >
        Providers
        {providers.data ? (
          <span className="rounded border border-border bg-surface px-1.5 font-mono text-[10.5px] font-normal leading-4 text-text-muted">
            {connected} of {list.length} connected
          </span>
        ) : null}
      </SectionHeading>

      {providers.isPending ? (
        <LoadingCard>Loading providers…</LoadingCard>
      ) : providers.isError ? (
        <ErrorCard what="Providers" error={providers.error} onRetry={() => void providers.refetch()} />
      ) : list.length === 0 ? (
        <div className="rounded-xl border border-border bg-surface">
          <EmptyState icon={Plug} title="No providers yet" description="Add the model server you run, or a hosted service you have a key for. Nothing is contacted until you add one." compact>
            <button type="button" className="btn btn-primary" onClick={() => setEditing({ mode: "add" })}>
              Add provider
            </button>
          </EmptyState>
        </div>
      ) : (
        <ul className="rounded-xl border border-border bg-surface">
          {list.map((provider) => {
            const status = statusOf(provider);
            const test = tests[provider.id];
            const nameId = `models-provider-${provider.id}`;
            const checked = (healthById.get(provider.id) ?? provider.health).checked_ms;
            return (
              <li key={provider.id} className="flex flex-wrap items-center gap-x-2.5 gap-y-2 border-b border-border/55 px-3 py-2.5 last:border-b-0">
                <span className="grid size-[26px] flex-none place-items-center rounded-md border border-border bg-bg font-mono text-[10px] font-semibold text-text-muted" aria-hidden="true">
                  {monogram(provider)}
                </span>
                <div className="min-w-0 flex-1 basis-44">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span id={nameId} className="min-w-0 break-words font-semibold">
                      {provider.label}
                    </span>
                    <LocalityTag isLocal={isLocal(provider)} />
                  </div>
                  <p className="break-all text-xs text-text-muted @2xl:truncate" title={providerDetail(provider)}>
                    {providerDetail(provider)}
                  </p>
                </div>
                <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                  <span title={checked !== null && provider.enabled ? `Last checked ${formatChecked(checked)}` : undefined}>
                    <Pill tone={status.tone}>{status.text}</Pill>
                  </span>
                  <span className="inline-flex items-center" title={provider.enabled ? "Switch off" : "Switch on"}>
                    <span id={`${nameId}-switch`} className="sr-only">
                      {provider.label} enabled
                    </span>
                    <Switch checked={provider.enabled} onChange={(enabled) => toggle.mutate({ id: provider.id, enabled })} labelledBy={`${nameId}-switch`} />
                  </span>
                  <button type="button" className={SMALL_BTN} onClick={() => void runTest(provider)} disabled={test?.pending === true} aria-label={`Test ${provider.label}`}>
                    {test?.pending ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : null}
                    Test
                  </button>
                  <button type="button" className={SMALL_BTN} onClick={() => setEditing({ mode: "edit", id: provider.id })} aria-label={`Configure ${provider.label}`}>
                    Configure
                  </button>
                  <button type="button" className="icon-btn" onClick={() => setRemoving(provider)} aria-label={`Remove ${provider.label}`}>
                    <Trash2 size={14} />
                  </button>
                </div>
                {status.error && !(test && !test.pending) ? <p className={`basis-full break-words text-xs ${status.tone === "bad" ? "text-danger" : "text-text-muted"}`}>{status.error}</p> : null}
                {test && !test.pending ? (
                  <p className={`basis-full break-words text-xs ${"result" in test && test.result.ok ? "text-success" : "text-danger"}`} role="status">
                    {"result" in test ? testSentence(test.result) : test.failure}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {editing?.mode === "add" ? <ProviderDialog mode="add" onClose={() => setEditing(null)} /> : null}
      {editing?.mode === "edit" && edited ? <ProviderDialog mode="edit" provider={edited} onClose={() => setEditing(null)} /> : null}

      {removing && usedBy.length > 0 ? (
        <Modal title={`${removing.label} is in use`} onClose={() => setRemoving(null)}>
          <p className="px-4 py-4 text-sm text-text-muted">
            {removing.label} is used by {roleList(usedBy)}. Point {usedBy.length === 1 ? "that role" : "those roles"} at another model first, then remove it.
          </p>
          <div className="flex justify-end border-t border-border px-4 py-3">
            <button type="button" className="btn btn-primary" onClick={() => setRemoving(null)} data-autofocus>
              OK
            </button>
          </div>
        </Modal>
      ) : removing ? (
        <ConfirmDialog
          title={`Remove ${removing.label}?`}
          message={`${roles ? "No role points at it. " : ""}Its saved key, health record and model list are deleted too. Usage already recorded is kept.`}
          confirmLabel="Remove"
          danger
          busy={remove.isPending}
          onConfirm={() => remove.mutate(removing)}
          onCancel={() => setRemoving(null)}
        />
      ) : null}
    </section>
  );
}
