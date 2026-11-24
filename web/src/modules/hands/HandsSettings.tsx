import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plug, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState } from "../../components/EmptyState";
import { Modal } from "../../components/Modal";
import { Switch } from "../../components/Switch";
import { useToast } from "../../hooks/useToast";
import { handsApi } from "./api";
import type { Account, Connecting, Provider, ProviderType } from "./api";
import { ConnectDialog } from "./ConnectDialog";
import { ProviderDialog } from "./ProviderDialog";
import { addressLine } from "./lib/mail";
import { forgetMessage, healthTone, removeBlockedMessage, typeAbout, typeLabel } from "./lib/wording";
import { HANDS_KEYS, useAccountsQuery, useDependentsQuery, useHandsLive, useProvidersQuery, useTypesQuery } from "./queries";
import { CARD, Failed, Loading, Pill, SMALL_BTN, Tag, whenText } from "./ui";

type Test = { pending: true } | { pending: false; ok: boolean; text: string };

/** The "Integrations" settings section: providers, and the accounts connected under them. Owned by the hands module. */
export function HandsSettings() {
  useHandsLive();
  const client = useQueryClient();
  const toast = useToast();
  const types = useTypesQuery();
  const providers = useProvidersQuery();
  const accounts = useAccountsQuery();
  const [dialog, setDialog] = useState<{ mode: "add" } | { mode: "edit"; id: string } | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Provider | null>(null);
  const [forgetting, setForgetting] = useState<Account | null>(null);
  const [tests, setTests] = useState<Record<string, Test>>({});

  const refresh = () => {
    void client.invalidateQueries({ queryKey: HANDS_KEYS.providers });
    void client.invalidateQueries({ queryKey: HANDS_KEYS.accounts });
  };

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => handsApi.updateProvider(id, { enabled }),
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: refresh,
  });

  const remove = useMutation({
    mutationFn: (provider: Provider) => handsApi.removeProvider(provider.id),
    onSuccess: (_result, provider) => {
      toast.success(`Removed ${provider.label}. Its saved key and secrets are deleted.`);
      setRemoving(null);
    },
    onError: (error) => {
      toast.error(errorDetail(error));
      setRemoving(null);
    },
    onSettled: refresh,
  });

  const forget = useMutation({
    mutationFn: (account: Account) => handsApi.forget(account.account),
    onSuccess: (_result, account) => {
      toast.success(`Forgot ${account.label}.`);
      setForgetting(null);
      void client.invalidateQueries({ queryKey: HANDS_KEYS.rulesAll });
    },
    onError: (error) => {
      toast.error(errorDetail(error));
      setForgetting(null);
    },
    onSettled: refresh,
  });

  const runTest = async (provider: Provider) => {
    setTests((current) => ({ ...current, [provider.id]: { pending: true } }));
    try {
      const result = await handsApi.check(provider.id);
      setTests((current) => ({ ...current, [provider.id]: { pending: false, ok: result.ok, text: result.ok ? "It answered. The provider can be reached." : (result.error ?? "It could not be reached.") } }));
    } catch (error) {
      setTests((current) => ({ ...current, [provider.id]: { pending: false, ok: false, text: errorDetail(error) } }));
    }
    refresh();
  };

  const list = providers.data?.providers ?? [];
  const typeList = types.data?.types ?? [];
  const allAccounts = accounts.data?.accounts ?? [];
  const pending = accounts.data?.connecting ?? [];
  const taken = [...allAccounts.map((a) => a.account), ...pending.map((c) => c.account)];
  const editedProvider = dialog?.mode === "edit" ? list.find((p) => p.id === dialog.id) : undefined;
  const connectProvider = connecting ? list.find((p) => p.id === connecting) : undefined;
  const removingAccounts = removing ? allAccounts.filter((a) => a.provider_id === removing.id).map((a) => a.account) : [];

  return (
    <div className="@container min-w-0">
      <p className="max-w-[62ch] text-sm text-text-muted">
        An integration lets your agents use an outside service, such as a mailbox or a calendar. Add the provider, connect your account with it, then switch on one action at a time for one agent, from that agent's Integrations tab. Nothing is offered to an agent until you do.
      </p>

      <div className="mb-2 mt-6 flex flex-wrap items-center gap-2">
        <h3 id="hands-providers-heading" className="text-[13px] font-semibold">
          Providers
        </h3>
        <div className="ml-auto">
          {typeList.length > 0 ? (
            <button type="button" className={SMALL_BTN} onClick={() => setDialog({ mode: "add" })}>
              <Plus size={13} aria-hidden="true" />
              Add provider
            </button>
          ) : null}
        </div>
      </div>

      {providers.isPending || types.isPending ? (
        <Loading>Loading integrations…</Loading>
      ) : providers.isError ? (
        <Failed what="Providers" error={providers.error} onRetry={() => void providers.refetch()} />
      ) : types.isError ? (
        <Failed what="The list of provider types" error={types.error} onRetry={() => void types.refetch()} />
      ) : list.length === 0 ? (
        <div className={CARD}>
          {typeList.length === 0 ? (
            <EmptyState
              icon={Plug}
              title="No integrations are installed yet"
              description="This copy of JimmyHQ has no connector for an outside service, so there is nothing to add yet. Nothing is contacted. Providers appear here once a connector is installed."
              compact
            />
          ) : (
            <EmptyState icon={Plug} title="No providers yet" description="Add the service you want your agents to use. Nothing is contacted until you add one, and no agent can use it until you switch an action on for it." compact>
              <button type="button" className="btn btn-primary" onClick={() => setDialog({ mode: "add" })}>
                Add provider
              </button>
            </EmptyState>
          )}
        </div>
      ) : (
        <ul className="flex flex-col gap-3" aria-labelledby="hands-providers-heading">
          {list.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              types={typeList}
              accounts={allAccounts.filter((a) => a.provider_id === provider.id)}
              connecting={pending.filter((c) => c.provider_id === provider.id)}
              accountsState={{ loading: accounts.isPending, error: accounts.isError ? errorDetail(accounts.error) : null, retry: () => void accounts.refetch() }}
              test={tests[provider.id]}
              onToggle={(enabled) => toggle.mutate({ id: provider.id, enabled })}
              onTest={() => void runTest(provider)}
              onConfigure={() => setDialog({ mode: "edit", id: provider.id })}
              onRemove={() => setRemoving(provider)}
              onConnect={() => setConnecting(provider.id)}
              onForget={setForgetting}
              onRefresh={refresh}
            />
          ))}
        </ul>
      )}

      {dialog?.mode === "add" ? <ProviderDialog mode="add" types={typeList} onClose={() => setDialog(null)} /> : null}
      {dialog?.mode === "edit" && editedProvider ? <ProviderDialog mode="edit" provider={editedProvider} types={typeList} onClose={() => setDialog(null)} /> : null}
      {connectProvider ? <ConnectDialog provider={connectProvider} label={connectProvider.label} takenNames={taken} onClose={() => setConnecting(null)} /> : null}

      {removing && removingAccounts.length > 0 ? (
        <Modal title={`${removing.label} still has accounts`} onClose={() => setRemoving(null)}>
          <p className="px-4 py-4 text-sm text-text-muted">{removeBlockedMessage(removing.label, removingAccounts)}</p>
          <div className="flex justify-end border-t border-border px-4 py-3">
            <button type="button" className="btn btn-primary" onClick={() => setRemoving(null)} data-autofocus>
              OK
            </button>
          </div>
        </Modal>
      ) : removing ? (
        <ConfirmDialog
          title={`Remove ${removing.label}?`}
          message="No account is connected to it. Its saved key and any other saved secrets are deleted. You can add it again later."
          confirmLabel="Remove"
          danger
          busy={remove.isPending}
          onConfirm={() => remove.mutate(removing)}
          onCancel={() => setRemoving(null)}
        />
      ) : null}

      {forgetting ? <ForgetDialog account={forgetting} busy={forget.isPending} onConfirm={() => forget.mutate(forgetting)} onCancel={() => setForgetting(null)} /> : null}
    </div>
  );
}

function ForgetDialog({ account, busy, onConfirm, onCancel }: { account: Account; busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  // The server says which agents' rules depend on it. If it cannot, the dialog still says what forgetting does in general.
  const uses = useDependentsQuery(account.account);
  const message = forgetMessage(account.label, account.provider_label, uses.isError ? undefined : uses.data);
  return <ConfirmDialog title={`Forget ${account.label}?`} message={uses.isPending ? "Checking what uses this account…" : message} confirmLabel="Forget account" danger busy={busy || uses.isPending} onConfirm={onConfirm} onCancel={onCancel} />;
}

type CardProps = {
  provider: Provider;
  types: ProviderType[];
  accounts: Account[];
  connecting: Connecting[];
  accountsState: { loading: boolean; error: string | null; retry: () => void };
  test: Test | undefined;
  onToggle: (enabled: boolean) => void;
  onTest: () => void;
  onConfigure: () => void;
  onRemove: () => void;
  onConnect: () => void;
  onForget: (account: Account) => void;
  onRefresh: () => void;
};

function ProviderCard({ provider, types, accounts, connecting, accountsState, test, onToggle, onTest, onConfigure, onRemove, onConnect, onForget, onRefresh }: CardProps) {
  const toast = useToast();
  const status = healthTone(provider);
  const nameId = `hands-provider-${provider.id}`;
  const info = types.find((t) => t.type === provider.type);

  const dropPending = async (item: Connecting) => {
    try {
      await handsApi.cancelConnect(item.account);
      toast.success(`Stopped connecting ${item.label}.`);
    } catch (error) {
      toast.error(errorDetail(error));
    }
    onRefresh();
  };

  const finishPending = async (item: Connecting) => {
    try {
      const answer = await handsApi.finish(item.account);
      if (answer.status === "ready") toast.success(`Connected ${item.label}.`);
      else toast.show("info", `${item.label} is not finished at the provider yet. Finish there, then check again.`);
    } catch (error) {
      toast.error(errorDetail(error));
    }
    onRefresh();
  };

  return (
    <li className={`${CARD} min-w-0`}>
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2 px-3 py-2.5">
        <span className="grid size-[26px] flex-none place-items-center rounded-md border border-border bg-bg font-mono text-[10px] font-semibold text-text-muted" aria-hidden="true">
          {provider.label.slice(0, 2).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1 basis-44">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span id={nameId} className="min-w-0 break-words font-semibold">
              {provider.label}
            </span>
            <Tag>{typeLabel(types, provider.type)}</Tag>
          </div>
          <p className="break-words text-xs text-text-muted">{typeAbout(info)}</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <span title={provider.health.checked_ms ? `Last tested ${whenText(provider.health.checked_ms)}` : undefined}>
            <Pill tone={status.tone}>{status.text}</Pill>
          </span>
          <span className="inline-flex items-center" title={provider.enabled ? "Switch off" : "Switch on"}>
            <span id={`${nameId}-switch`} className="sr-only">
              {provider.label} enabled
            </span>
            <Switch checked={provider.enabled} onChange={onToggle} labelledBy={`${nameId}-switch`} />
          </span>
          <button type="button" className={SMALL_BTN} onClick={onTest} disabled={test?.pending === true || !provider.type_known} aria-label={`Test ${provider.label}`}>
            {test?.pending ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : null}
            Test
          </button>
          <button type="button" className={SMALL_BTN} onClick={onConfigure} aria-label={`Configure ${provider.label}`}>
            Configure
          </button>
          <button type="button" className="icon-btn" onClick={onRemove} aria-label={`Remove ${provider.label}`}>
            <Trash2 size={14} />
          </button>
        </div>
        {provider.health.error && provider.health.ok === false && !(test && !test.pending) ? <p className="basis-full break-words text-xs text-danger">{provider.health.error}</p> : null}
        {test && !test.pending ? (
          <p className={`basis-full break-words text-xs ${test.ok ? "text-success" : "text-danger"}`} role="status">
            {test.text}
          </p>
        ) : null}
      </div>

      <div className="border-t border-border bg-surface-2/40 px-3 py-2.5">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <h4 className="text-xs font-semibold text-text">Accounts</h4>
          <button type="button" className={`${SMALL_BTN} ml-auto`} onClick={onConnect} disabled={!provider.type_known || !provider.enabled} title={!provider.enabled ? "Switch the provider on first." : undefined} aria-label={`Connect an account to ${provider.label}`}>
            <Plus size={12} aria-hidden="true" />
            Connect an account
          </button>
        </div>
        {accountsState.loading ? <p className="text-xs text-text-faint">Loading accounts…</p> : null}
        {accountsState.error ? (
          <p className="flex flex-wrap items-center gap-2 text-xs text-danger" role="alert">
            {accountsState.error}
            <button type="button" className={SMALL_BTN} onClick={accountsState.retry}>
              Try again
            </button>
          </p>
        ) : null}
        {!accountsState.loading && !accountsState.error && accounts.length === 0 && connecting.length === 0 ? (
          <p className="text-xs text-text-muted">No account connected. Use Connect an account to sign in with {provider.label}. No agent can use it until you switch an action on for that agent.</p>
        ) : null}
        <ul className="flex flex-col">
          {accounts.map((account) => (
            <li key={account.account} className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border/55 py-1.5 last:border-b-0">
              <div className="min-w-0 flex-1 basis-40">
                <span className="break-words text-[13px] font-medium">{account.label}</span>
                <span className="ml-2 font-mono text-[11px] text-text-faint">{account.account}</span>
                {account.app ? <span className="ml-2 text-[11px] text-text-faint">app: {account.app}</span> : null}
                {addressLine(account) ? <p className="break-all text-xs text-text-muted">{addressLine(account)}</p> : null}
                {!account.connected ? <p className="text-xs text-warning">Its sign-in is missing here. Forget it and connect it again.</p> : null}
              </div>
              <button type="button" className={SMALL_BTN} onClick={() => onForget(account)} aria-label={`Forget the account ${account.label}`}>
                Forget
              </button>
            </li>
          ))}
          {connecting.map((item) => (
            <li key={item.account} className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border/55 py-1.5 last:border-b-0">
              <p className="min-w-0 flex-1 basis-40 text-xs text-text-muted" role="status">
                <span className="font-medium text-text">{item.label}</span> was started {whenText(item.started_ms)} and is waiting for you to finish at the provider's page.
              </p>
              <button type="button" className={SMALL_BTN} onClick={() => void finishPending(item)}>
                Check now
              </button>
              <button type="button" className={SMALL_BTN} onClick={() => void dropPending(item)} aria-label={`Stop connecting ${item.label}`}>
                Cancel
              </button>
            </li>
          ))}
        </ul>
      </div>
    </li>
  );
}
