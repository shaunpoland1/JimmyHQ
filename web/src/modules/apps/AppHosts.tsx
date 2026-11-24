import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../hooks/useToast";
import { hostsApi } from "./api";
import { SecretRow } from "./SecretRow";
import { addQuestion, hostProblem, removeQuestion } from "./lib/hosts";
import type { OwnerHost, OwnerHosts } from "./lib/hosts";
import { useInstalledApps } from "./useInstalledApps";

const CARD = "@container rounded-xl border border-border bg-surface";

function hostsKey(appId: string) {
  return ["apps", "hosts", appId] as const;
}

/**
 * The owner's own addresses an app may reach, on that app's own Settings page: only for
 * an app whose manifest declares `owner_hosts`. The owner adds and removes each one, after
 * a plain question in the app's own dialog; the app itself never can. Beside each address
 * are the secrets the app keeps for it, write only. Owned by the apps module.
 */
export function AppHosts({ appId }: { appId: string }) {
  const query = useQuery({ queryKey: hostsKey(appId), queryFn: () => hostsApi.list(appId), staleTime: 30_000 });
  const installed = useInstalledApps();
  const appName = installed.apps.find((app) => app.id === appId)?.name ?? appId;
  if (query.isError) {
    return (
      <p className={`${CARD} px-4 py-3.5 text-sm text-text-muted`} role="alert">
        <span className="font-medium text-text">This app's addresses could not be read.</span> {errorDetail(query.error)}
      </p>
    );
  }
  if (query.isPending || !query.data?.declared) return null;
  const have = query.data;
  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex items-center gap-3 px-4 py-3.5">
        <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
          <Globe size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-semibold text-text">Your own addresses</h3>
          <p className="text-xs text-text-muted">
            {appName} says why it asks: “{have.declared?.purpose}” It may be given up to {have.declared?.max}, and reaches only what you add here.
          </p>
          {have.declared?.next ? <p className="mt-1 text-xs text-text">{have.declared.next}</p> : null}
        </div>
      </div>
      {have.hosts.map((host) => (
        <HostRow key={host.host} appId={appId} appName={appName} host={host} />
      ))}
      <AddHost appId={appId} appName={appName} have={have} />
    </div>
  );
}

function HostRow({ appId, appName, host }: { appId: string; appName: string; host: OwnerHost }) {
  const client = useQueryClient();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const remove = useMutation({
    mutationFn: () => hostsApi.remove(appId, host.host),
    onSuccess: (now: OwnerHosts) => {
      setConfirming(false);
      client.setQueryData(hostsKey(appId), now);
      toast.success(`${appName} can no longer reach ${host.host}.`);
    },
    onError: (failure) => {
      setConfirming(false);
      toast.error(errorDetail(failure));
    },
  });
  const question = removeQuestion(appName, host.host);
  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3.5">
        <span className="min-w-0 flex-1 break-all font-mono text-[13px] text-text">{host.host}</span>
        <button type="button" className="btn" onClick={() => setConfirming(true)} aria-label={`Remove ${host.host}`}>
          Remove
        </button>
      </div>
      {host.secrets.map((state) => (
        <SecretRow
          key={state.name}
          appName={appName}
          state={state}
          queryKey={hostsKey(appId)}
          save={(typed) => hostsApi.setSecret(appId, host.host, state.name, typed)}
          clear={() => hostsApi.clearSecret(appId, host.host, state.name)}
        />
      ))}
      {confirming ? (
        <ConfirmDialog
          title={question.title}
          message={question.message}
          confirmLabel="Remove"
          danger
          busy={remove.isPending}
          onConfirm={() => remove.mutate()}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </div>
  );
}

function AddHost({ appId, appName, have }: { appId: string; appName: string; have: OwnerHosts }) {
  const client = useQueryClient();
  const toast = useToast();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const inputId = useId();
  const errorId = useId();
  const add = useMutation({
    mutationFn: () => hostsApi.add(appId, typed),
    onSuccess: (now: OwnerHosts) => {
      setConfirming(false);
      setTyped("");
      client.setQueryData(hostsKey(appId), now);
      toast.success(`${appName} can now reach the address you gave it.`);
    },
    onError: (failure) => {
      setConfirming(false);
      setError(errorDetail(failure));
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const problem = hostProblem(typed, have);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setConfirming(true);
  };
  const question = addQuestion(appName, typed);
  return (
    <form className="flex flex-col gap-2 px-4 py-3.5 @md:flex-row @md:items-start" onSubmit={submit} noValidate>
      <div className="min-w-0 flex-1">
        <label htmlFor={inputId} className="sr-only">
          Address of your site
        </label>
        <input
          id={inputId}
          className="input font-mono"
          type="text"
          inputMode="url"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="https://blog.example.com"
          value={typed}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => {
            setTyped(event.target.value);
            if (error) setError(null);
          }}
        />
        {error ? (
          <p id={errorId} className="mt-1 break-words text-xs text-danger" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <button type="submit" className="btn btn-primary flex-none" disabled={add.isPending || typed.trim() === ""}>
        {add.isPending ? "Adding…" : "Add address"}
      </button>
      {confirming ? (
        <ConfirmDialog
          title={question.title}
          message={question.message}
          confirmLabel="Add"
          busy={add.isPending}
          onConfirm={() => add.mutate()}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </form>
  );
}
