import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { House } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../hooks/useToast";
import { formatFullTime } from "../../lib/format";
import { localApi } from "./api";
import { EXAMPLE, addWarning, leadsLine, localProblem, moved, movedQuestion, removeLocalQuestion } from "./lib/local";
import type { LocalAddress, LocalAddresses, LocalCheck } from "./lib/local";
import { SectionHeading } from "./ui";

const CARD = "@container rounded-xl border border-border bg-surface";

function localKey(appId: string) {
  return ["apps", "local", appId] as const;
}

/**
 * Addresses on the owner's own network he lets this app reach, on the app's page under
 * Apps: an image server at home, one on a tailnet, one on this computer. Only the owner
 * adds one, after a plain warning in JimmyHQ's own dialog; the app never can. Exactly that
 * scheme, host and port, and nothing else there. Owned by the apps module.
 */
export function LocalAddressesPanel({ id, name }: { id: string; name: string }) {
  const query = useQuery({ queryKey: localKey(id), queryFn: () => localApi.list(id), staleTime: 30_000 });
  if (query.isError) {
    return (
      <p className={`${CARD} mt-3 px-4 py-3.5 text-sm text-text-muted`} role="alert">
        <span className="font-medium text-text">This app's addresses on your own network could not be read.</span> {errorDetail(query.error)}
      </p>
    );
  }
  if (query.isPending) return null;
  const have = query.data;
  return (
    <>
      <SectionHeading id="apps-detail-local">Addresses on your own network</SectionHeading>
      <section aria-labelledby="apps-detail-local" className={`${CARD} divide-y divide-border`}>
        <div className="flex items-start gap-3 px-4 py-3.5">
          <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
            <House size={16} />
          </span>
          <p className="min-w-0 flex-1 text-xs text-text-muted">
            An app reaches only public sites over https unless you add an address here, such as a server on another computer at home or on this one. {name} then reaches exactly that address and port, and nothing else on your network. Never a cloud computer's own settings, and never JimmyHQ itself. Up to {have.max || "a few"}.
          </p>
        </div>
        {have.addresses.map((item) => (
          <AddressRow key={item.key} appId={id} appName={name} item={item} />
        ))}
        <AddAddress appId={id} appName={name} have={have} />
      </section>
    </>
  );
}

function AddressRow({ appId, appName, item }: { appId: string; appName: string; item: LocalAddress }) {
  const client = useQueryClient();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [movedTo, setMovedTo] = useState<LocalCheck | null>(null);
  const remove = useMutation({
    mutationFn: () => localApi.remove(appId, item.key),
    onSuccess: (now: LocalAddresses) => {
      setConfirming(false);
      client.setQueryData(localKey(appId), now);
      toast.success(`${appName} can no longer reach ${item.address}.`);
    },
    onError: (failure) => {
      setConfirming(false);
      toast.error(errorDetail(failure));
    },
  });
  const look = useMutation({
    mutationFn: () => localApi.check(appId, item.address),
    onSuccess: (now: LocalCheck) => {
      if (moved(now)) setMovedTo(now);
      else toast.success(`${item.address} still leads where you agreed: ${now.resolved.join(", ")}.`);
    },
    onError: (failure) => toast.error(errorDetail(failure)),
  });
  const agree = useMutation({
    mutationFn: (now: LocalCheck) => localApi.confirm(appId, now),
    onSuccess: (now: LocalAddresses) => {
      setMovedTo(null);
      client.setQueryData(localKey(appId), now);
      toast.success(`${appName} can reach ${item.address} again, where it leads now.`);
    },
    onError: (failure) => {
      setMovedTo(null);
      toast.error(errorDetail(failure));
    },
  });
  const question = removeLocalQuestion(appName, item.address);
  const leads = leadsLine(item);
  const asked = movedTo ? movedQuestion(appName, movedTo) : null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="break-all font-mono text-[13px] text-text">{item.address}</p>
        {item.addedAt ? <p className="text-xs text-text-muted">Added {formatFullTime(item.addedAt)}.</p> : null}
        {leads ? <p className="break-words text-xs text-text-muted">{leads}</p> : null}
      </div>
      {leads ? (
        <button type="button" className="btn" onClick={() => look.mutate()} disabled={look.isPending} aria-label={`Check where ${item.address} leads now`}>
          {look.isPending ? "Checking…" : "Check"}
        </button>
      ) : null}
      <button type="button" className="btn" onClick={() => setConfirming(true)} aria-label={`Remove ${item.address}`}>
        Remove
      </button>
      {confirming ? (
        <ConfirmDialog title={question.title} message={question.message} confirmLabel="Remove" danger busy={remove.isPending} onConfirm={() => remove.mutate()} onCancel={() => setConfirming(false)} />
      ) : null}
      {movedTo && asked ? (
        <ConfirmDialog title={asked.title} message={asked.message} confirmLabel="Let it reach the new address" busy={agree.isPending} onConfirm={() => agree.mutate(movedTo)} onCancel={() => setMovedTo(null)} />
      ) : null}
    </div>
  );
}

function AddAddress({ appId, appName, have }: { appId: string; appName: string; have: LocalAddresses }) {
  const client = useQueryClient();
  const toast = useToast();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<LocalCheck | null>(null);
  const inputId = useId();
  const errorId = useId();
  // Step one: where it leads now, which the warning shows. Nothing is added.
  const look = useMutation({
    mutationFn: () => localApi.check(appId, typed),
    onSuccess: (found: LocalCheck) => {
      if (found.added) setError(`${appName} may reach ${found.address} already. Nothing was added.`);
      else setShown(found);
    },
    onError: (failure) => setError(errorDetail(failure)),
  });
  // Step two: exactly what the owner was shown, or nothing.
  const add = useMutation({
    mutationFn: (found: LocalCheck) => localApi.add(appId, found),
    onSuccess: (now: LocalAddresses) => {
      setShown(null);
      setTyped("");
      client.setQueryData(localKey(appId), now);
      toast.success(`${appName} can now reach that address on your network. Reopen its screen if it is open.`);
    },
    onError: (failure) => {
      setShown(null);
      setError(errorDetail(failure));
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const problem = localProblem(typed, have);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    look.mutate();
  };
  const warning = shown ? addWarning(appName, shown) : null;
  const busy = look.isPending || add.isPending;
  return (
    <form className="flex flex-col gap-2 px-4 py-3.5 @md:flex-row @md:items-start" onSubmit={submit} noValidate>
      <div className="min-w-0 flex-1">
        <label htmlFor={inputId} className="sr-only">
          An address on your own network, with its port
        </label>
        <input
          id={inputId}
          className="input font-mono"
          type="text"
          inputMode="url"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder={EXAMPLE}
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
        ) : (
          <p className="mt-1 text-xs text-text-muted">The scheme, the computer's name or address, and the port, exactly. The app chooses the page.</p>
        )}
      </div>
      <button type="submit" className="btn btn-primary flex-none" disabled={busy || typed.trim() === ""}>
        {look.isPending ? "Checking…" : add.isPending ? "Adding…" : "Add address"}
      </button>
      {shown && warning ? (
        <ConfirmDialog title={warning.title} message={warning.message} confirmLabel="Let it reach this" busy={add.isPending} onConfirm={() => add.mutate(shown)} onCancel={() => setShown(null)} />
      ) : null}
    </form>
  );
}
