import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Smartphone, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, errorDetail } from "../../api";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { subscribeFeed } from "../../lib/feed";
import { REMOTE_STATUS_KEY, remoteApi } from "./api";
import type { RemoteStatus } from "./api";
import { HOSTS_LABEL, actions, affectsRemoteStatus, headline, steps, turnOffPlan, turnOnPlan, turnedOffLine, turnedOnLine } from "./lib/status";

const CARD = "@container rounded-xl border border-border bg-surface";
const TONE_DOT = { ok: "bg-success", warn: "bg-warning", off: "bg-sage" } as const;

/** A refusal will be refused again; only a failure to reach the server is worth another try. */
function retry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return count < 2;
}

function useRemoteStatus() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: REMOTE_STATUS_KEY, queryFn: remoteApi.status, staleTime: 15_000, retry });
  useEffect(() => {
    const refresh = () => void client.invalidateQueries({ queryKey: REMOTE_STATUS_KEY });
    return subscribeFeed({
      onEvent: (event) => {
        if (affectsRemoteStatus(event)) refresh();
      },
      onReconnect: refresh,
    });
  }, [client]);
  return query;
}

/** Shown above Settings, Access: open JimmyHQ to the owner's other devices over Tailscale, or follow the plain steps. Owned by the remote module. */
export function RemoteSettingsExtra() {
  const query = useRemoteStatus();
  if (query.data) return <StatusCard status={query.data} checking={query.isFetching} onCheck={() => void query.refetch()} />;
  if (query.isPending) {
    return (
      <p className={`${CARD} px-4 py-4 text-sm text-text-faint`} role="status">
        Checking Tailscale…
      </p>
    );
  }
  return (
    <div className={`${CARD} flex flex-wrap items-center justify-between gap-3 px-4 py-3.5`} role="alert">
      <p className="min-w-0 flex-1 text-sm text-text-muted">
        <span className="font-medium text-text">Remote access status is unavailable.</span> {errorDetail(query.error)}
      </p>
      <button type="button" className="btn" onClick={() => void query.refetch()} disabled={query.isFetching}>
        Try again
      </button>
    </div>
  );
}

function StatusCard({ status, checking, onCheck }: { status: RemoteStatus; checking: boolean; onCheck: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [modal, setModal] = useState<"on" | "off" | null>(null);
  const [removeHost, setRemoveHost] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const say = headline(status);
  const shown = actions(status);

  const settle = (next: RemoteStatus, line: string) => {
    client.setQueryData(REMOTE_STATUS_KEY, next);
    setError(null);
    setDone(line);
    setModal(null);
  };
  const failed = (failure: unknown) => {
    setModal(null);
    setDone(null);
    setError(errorDetail(failure));
  };

  const turnOn = useMutation({
    mutationFn: remoteApi.turnOn,
    onSuccess: (result) => settle(result.status, turnedOnLine(result)),
    onError: failed,
  });
  const turnOff = useMutation({
    mutationFn: (alsoRemove: boolean) => remoteApi.turnOff(alsoRemove),
    onSuccess: (result) => settle(result.status, turnedOffLine(result)),
    onError: failed,
  });
  const allow = useMutation({
    mutationFn: remoteApi.allowHost,
    onSuccess: (result) => {
      settle(result.status, result.added ? "Added to the allowed hosts." : "It was already in the allowed hosts.");
      toast.success("Allowed hosts updated.");
    },
    onError: failed,
  });

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${what} copied.`);
    } catch {
      toast.error(`The ${what.toLowerCase()} could not be copied. Select it and copy it by hand.`);
    }
  };

  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
            <Smartphone size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-[13px] font-semibold text-text">Reach JimmyHQ from your other devices</h3>
            <p className="text-xs text-text-muted">Open JimmyHQ to your own private network with Tailscale. JimmyHQ listens on this computer only until you do.</p>
          </div>
          <span className="inline-flex flex-none items-center gap-1.5 text-xs font-medium text-text" role="status">
            <span className={`dot ${TONE_DOT[say.tone]}`} aria-hidden="true" />
            {say.label}
          </span>
        </div>

        {say.detail ? (
          <p className="flex items-start gap-2 break-words text-xs text-text-muted">
            {say.tone === "warn" ? <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" /> : null}
            <span className="min-w-0">{say.detail}</span>
          </p>
        ) : null}
        {status.path_missing ? (
          <p className="text-xs text-text-muted">The path set under “Path to the Tailscale command” points at nothing, so JimmyHQ looked in the usual places instead.</p>
        ) : null}
        {status.state === "ready" && status.https_enabled === false ? (
          <p className="text-xs text-text-muted">HTTPS certificates look switched off for your tailnet. Tailscale needs them: turn on MagicDNS and HTTPS on the DNS page of the Tailscale admin console, or the command will wait for you.</p>
        ) : null}

        {status.address ? (
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 select-all overflow-x-auto whitespace-nowrap rounded-md border border-border bg-surface-2 px-2 py-1 font-mono text-xs text-text">{status.address}</code>
            <button type="button" className="icon-btn" onClick={() => void copy(status.address ?? "", "Address")} aria-label="Copy the address" title="Copy">
              <Copy size={14} aria-hidden="true" />
            </button>
          </div>
        ) : null}

        {done ? (
          <p className="break-words text-xs text-text" role="status">
            {done}
          </p>
        ) : null}
        {error ? (
          <p className="flex items-start gap-2 break-words rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-xs text-text" role="alert">
            <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
            <span className="min-w-0">{error}</span>
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          {shown.turnOn ? (
            <button type="button" className="btn btn-primary" onClick={() => setModal("on")}>
              Turn on
            </button>
          ) : null}
          {shown.allowHost ? (
            <button type="button" className="btn" onClick={() => allow.mutate()} disabled={allow.isPending}>
              {allow.isPending ? "Adding…" : "Add this address to allowed hosts"}
            </button>
          ) : null}
          {shown.turnOff ? (
            <button type="button" className="btn" onClick={() => setModal("off")}>
              Turn off
            </button>
          ) : null}
          <button type="button" className="btn" onClick={onCheck} disabled={checking}>
            {checking ? "Checking…" : "Check again"}
          </button>
        </div>
        {shown.atMachine ? (
          <p className="text-xs text-text-muted">
            This step is done at the computer JimmyHQ runs on, not from another device. Open JimmyHQ there at http://127.0.0.1 and use Settings, Access, or follow “Do it yourself” below.
          </p>
        ) : null}
      </div>

      <details className="group px-4 py-3">
        <summary className="cursor-pointer text-[13px] font-medium text-text">Do it yourself</summary>
        <ol className="mt-3 flex flex-col gap-3">
          {steps(status).map((step, at) => (
            <li key={step.title} className="flex flex-col gap-1">
              <p className="text-xs font-medium text-text">
                {at + 1}. {step.title}
              </p>
              <p className="text-xs text-text-muted">{step.text}</p>
              {step.command ? <CommandLine command={step.command} onCopy={() => void copy(step.command ?? "", "Command")} /> : null}
            </li>
          ))}
        </ol>
        {status.commands.off ? (
          <p className="mt-3 text-xs text-text-muted">
            To turn it off again: <code className="font-mono text-text">{status.commands.off}</code>. Then remove the name from “{HOSTS_LABEL}” if you no longer want it answered.
          </p>
        ) : null}
        <p className="mt-3 text-xs text-text-muted">
          Using another reverse proxy or VPN instead? Add the host name it uses to “{HOSTS_LABEL}”. If it is a reverse proxy that forwards the real visitor address, also list its address in trusted_proxies in config.toml. See docs/remote.md.
        </p>
      </details>

      <p className="px-4 py-3 text-xs text-text-faint">
        None of this opens JimmyHQ to the public internet. Tailscale Serve reaches only devices signed in to your own tailnet, and JimmyHQ never turns on Tailscale Funnel, which is the public one. Sign-in is still needed on every device.
      </p>

      {modal === "on" ? (
        <ConfirmCommand
          title="Turn on access from your other devices?"
          plan={turnOnPlan(status)}
          confirmLabel="Turn on"
          busy={turnOn.isPending}
          onConfirm={() => turnOn.mutate()}
          onCancel={() => setModal(null)}
        />
      ) : null}
      {modal === "off" ? (
        <ConfirmCommand
          title="Turn off access from your other devices?"
          plan={turnOffPlan(status)}
          confirmLabel="Turn off"
          busy={turnOff.isPending}
          onConfirm={() => turnOff.mutate(removeHost)}
          onCancel={() => setModal(null)}
        >
          {status.allowed ? (
            <label className="flex items-start gap-2 text-xs text-text">
              <input type="checkbox" className="mt-0.5" checked={removeHost} onChange={(event) => setRemoveHost(event.target.checked)} />
              <span>Also remove this name from “{HOSTS_LABEL}”</span>
            </label>
          ) : null}
        </ConfirmCommand>
      ) : null}
    </div>
  );
}

function CommandLine({ command, onCopy }: { command: string; onCopy: () => void }) {
  return (
    <div className="mt-1 flex items-center gap-2">
      <code className="min-w-0 flex-1 select-all overflow-x-auto whitespace-nowrap rounded-md border border-border bg-surface-2 px-2 py-1 font-mono text-xs text-text">{command}</code>
      <button type="button" className="icon-btn" onClick={onCopy} aria-label="Copy the command" title="Copy">
        <Copy size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

function ConfirmCommand({
  title,
  plan,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
  children,
}: {
  title: string;
  plan: { command: string; changes: string[] };
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children?: React.ReactNode;
}) {
  return (
    <Modal title={title} onClose={onCancel} widthClass="max-w-lg">
      <div className="flex flex-col gap-3 overflow-y-auto px-4 py-4">
        <div>
          <p className="text-xs text-text-muted">JimmyHQ will run this on this computer:</p>
          <code className="mt-1 block select-all overflow-x-auto whitespace-nowrap rounded-md border border-border bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{plan.command}</code>
        </div>
        <ul className="flex list-disc flex-col gap-1.5 pl-4 text-xs text-text-muted">
          {plan.changes.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {children}
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="btn btn-primary" onClick={onConfirm} disabled={busy} data-autofocus>
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
