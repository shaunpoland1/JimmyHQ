import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, RadioTower } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Modal } from "../../components/Modal";
import { Switch } from "../../components/Switch";
import { useToast } from "../../hooks/useToast";
import { copyToClipboard } from "../../lib/browserClipboard";
import { formatFullTime } from "../../lib/format";
import { inboundApi } from "./api";
import { ADDRESS_WARNING, LABEL_MAX, closedReason, countsLine, keyAddress, labelProblem, removeQuestion, sendAddress, sizeNote } from "./lib/inbound";
import type { Inbound, InboundKey } from "./lib/inbound";
import { SectionHeading } from "./ui";

const CARD = "@container rounded-xl border border-border bg-surface";

function inboundKey(appId: string) {
  return ["apps", "inbound", appId] as const;
}

function useCopy() {
  const toast = useToast();
  return (text: string, what: string) => {
    void copyToClipboard(text).then((copied) =>
      copied ? toast.success(`${what} copied.`) : toast.error(`${what} could not be copied. Select it and copy it by hand.`),
    );
  };
}

/**
 * Messages from the owner's devices, on a full app's page: shown only for an app whose
 * manifest declares `inbound`. The address to send to, the app's own switch, its keys (a
 * key is on screen once, in the dialog that makes it) and the counts. Owned by the apps module.
 */
export function InboundPanel({ id, name }: { id: string; name: string }) {
  const query = useQuery({ queryKey: inboundKey(id), queryFn: () => inboundApi.get(id), staleTime: 15_000 });
  const client = useQueryClient();
  const toast = useToast();
  const switchId = useId();
  const setOpen = useMutation({
    mutationFn: (open: boolean) => inboundApi.setOpen(id, open),
    onSuccess: (now: Inbound) => {
      client.setQueryData(inboundKey(id), now);
      toast.success(now.open ? `${name} takes messages from your devices again.` : `${name} refuses every message from your devices now.`);
    },
    onError: (failure) => toast.error(errorDetail(failure)),
  });
  if (query.isError) {
    return (
      <p className={`${CARD} mt-3 px-4 py-3.5 text-sm text-text-muted`} role="alert">
        <span className="font-medium text-text">This app's messages from devices could not be read.</span> {errorDetail(query.error)}
      </p>
    );
  }
  if (query.isPending || query.data.declared.length === 0) return null;
  const state = query.data;
  const closed = closedReason(state);
  return (
    <>
      <SectionHeading id="apps-detail-inbound">Messages from your devices</SectionHeading>
      <section aria-labelledby="apps-detail-inbound" className={`${CARD} divide-y divide-border`}>
        <div className="flex items-start gap-3 px-4 py-3.5">
          <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
            <RadioTower size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-semibold text-text" id={`${switchId}-label`}>
              Let your devices send {name} messages
            </p>
            <p className="text-xs text-text-muted" id={`${switchId}-help`}>
              {closed ?? "Open: a device with one of the keys below can send it a message."} Anything that can reach JimmyHQ can reach this address; a key is what lets a message in.
            </p>
          </div>
          <Switch
            id={switchId}
            checked={state.open}
            labelledBy={`${switchId}-label`}
            describedBy={`${switchId}-help`}
            disabled={setOpen.isPending || !state.granted || state.keys.length === 0}
            onChange={(next) => setOpen.mutate(next)}
          />
        </div>
        <Addresses id={id} state={state} />
        <div className="px-4 py-3 text-xs text-text-muted" role="status">
          <p>{countsLine(state)}</p>
          {state.lastError ? (
            <p className="mt-1 break-words">
              <span className="font-medium text-text">Last failure{state.lastErrorAt ? `, ${formatFullTime(state.lastErrorAt)}` : ""}:</span> {state.lastError}
            </p>
          ) : null}
        </div>
        {state.keys.map((key) => (
          <KeyRow key={key.id} appId={id} appName={name} item={key} />
        ))}
        {state.granted ? <MakeKey appId={id} appName={name} state={state} /> : null}
      </section>
    </>
  );
}

function Addresses({ id, state }: { id: string; state: Inbound }) {
  const copy = useCopy();
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <div className="flex flex-col gap-2 px-4 py-3.5">
      {state.declared.map((item) => {
        const address = sendAddress(origin, id, item.name);
        const size = sizeNote(item, state.maxKb);
        return (
          <div key={item.name} className="flex flex-col gap-1">
            <p className="text-xs font-medium text-text">{item.label}</p>
            {item.help ? <p className="text-xs text-text-muted">{item.help}</p> : null}
            {item.array || size ? (
              <p className="text-xs text-text-muted">
                {item.array ? "Takes a JSON list as well as an object. " : ""}
                {size}
              </p>
            ) : null}
            {address ? (
              <div className="flex flex-wrap items-center gap-2">
                <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">POST {address}</code>
                <button type="button" className="btn" onClick={() => copy(address, "The address")} aria-label={`Copy the address for ${item.label}`}>
                  <Copy size={14} aria-hidden="true" /> Copy
                </button>
              </div>
            ) : null}
          </div>
        );
      })}
      <p className="text-xs text-text-muted">
        A device must use an address it can reach, which may not be the one in your browser. It sends a JSON object, with <span className="font-mono">Authorization: Bearer</span> and its key. A sender that can only be given an address gets a key made for that, which is put in the address. Anything a sender adds after <span className="font-mono">?</span> is passed to the app.
      </p>
    </div>
  );
}

function KeyRow({ appId, appName, item }: { appId: string; appName: string; item: InboundKey }) {
  const client = useQueryClient();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const remove = useMutation({
    mutationFn: () => inboundApi.removeKey(appId, item.id),
    onSuccess: (now: Inbound) => {
      setConfirming(false);
      client.setQueryData(inboundKey(appId), now);
      toast.success(`The key “${item.label}” no longer works.`);
    },
    onError: (failure) => {
      setConfirming(false);
      toast.error(errorDetail(failure));
    },
  });
  const question = removeQuestion(appName, item.label);
  const used = item.lastUsed ? `last used ${formatFullTime(item.lastUsed)}, ${item.uses === 1 ? "once" : `${item.uses} times`}` : "not used yet";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
      <KeyRound size={14} className="flex-none text-text-muted" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="break-words text-[13px] text-text">{item.label}</p>
        <p className="text-xs text-text-muted">
          {item.inAddress ? "In its address. " : ""}Made {item.created ? formatFullTime(item.created) : "some time ago"}, {used}.
        </p>
      </div>
      <button type="button" className="btn" onClick={() => setConfirming(true)} aria-label={`Remove the key ${item.label}`}>
        Remove
      </button>
      {confirming ? (
        <ConfirmDialog title={question.title} message={question.message} confirmLabel="Remove" danger busy={remove.isPending} onConfirm={() => remove.mutate()} onCancel={() => setConfirming(false)} />
      ) : null}
    </div>
  );
}

type Made = { label: string; key: string; addresses: { label: string; address: string }[] };

function MakeKey({ appId, appName, state }: { appId: string; appName: string; state: Inbound }) {
  const client = useQueryClient();
  const [typed, setTyped] = useState("");
  const [inAddress, setInAddress] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<Made | null>(null);
  const inputId = useId();
  const errorId = useId();
  const make = useMutation({
    mutationFn: () => inboundApi.makeKey(appId, typed, inAddress),
    onSuccess: (answer) => {
      client.setQueryData(inboundKey(appId), answer.inbound);
      const origin = typeof window === "undefined" ? "" : window.location.origin;
      const addresses = answer.paths.flatMap(({ name, path }) => {
        const address = keyAddress(origin, appId, name, answer.key, path);
        const label = answer.inbound.declared.find((item) => item.name === name)?.label ?? name;
        return address ? [{ label, address }] : [];
      });
      setMade({ label: typed.trim(), key: answer.key, addresses });
      setTyped("");
      setInAddress(false);
    },
    onError: (failure) => setError(errorDetail(failure)),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const problem = labelProblem(typed, state);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    make.mutate();
  };
  return (
    <form className="flex flex-col gap-2 px-4 py-3.5 @md:flex-row @md:items-start" onSubmit={submit} noValidate>
      <div className="min-w-0 flex-1">
        <label htmlFor={inputId} className="sr-only">
          Name of the device the key is for
        </label>
        <input
          id={inputId}
          className="input"
          type="text"
          autoComplete="off"
          maxLength={LABEL_MAX + 20}
          placeholder="Name of the device, such as Home hub"
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
          <p className="mt-1 text-xs text-text-muted">
            One key for each device, {state.keys.length} of {state.keysMax} made. The first key opens the app to messages.
          </p>
        )}
        <label className="mt-2 flex items-start gap-2 text-xs text-text">
          <input type="checkbox" className="mt-0.5" checked={inAddress} onChange={(event) => setInAddress(event.target.checked)} disabled={make.isPending} />
          <span>
            For a sender that can only be given an address
            <span className="block text-text-muted">The key goes in the address itself, so anyone who sees the address can send {appName} messages. Leave this off for a device that can send a header.</span>
          </span>
        </label>
      </div>
      <button type="submit" className="btn btn-primary flex-none" disabled={make.isPending || typed.trim() === ""}>
        {make.isPending ? "Making…" : "Make a key"}
      </button>
      {made && made.addresses.length > 0 ? <AddressDialog made={made} onClose={() => setMade(null)} /> : null}
      {made && made.addresses.length === 0 ? <KeyDialog label={made.label} keyText={made.key} onClose={() => setMade(null)} /> : null}
    </form>
  );
}

/** The one time a key is on screen. It is held in this dialog's state only. */
function KeyDialog({ label, keyText, onClose }: { label: string; keyText: string; onClose: () => void }) {
  const copy = useCopy();
  return (
    <Modal title={`The key for ${label}`} onClose={onClose} widthClass="max-w-lg">
      <div className="flex flex-col gap-3 px-4 py-4">
        <p className="text-sm text-text-muted">
          <span className="font-medium text-text">This is shown once.</span> JimmyHQ keeps only a fingerprint of it and cannot show it again. Put it into the device now, as a bearer token.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{keyText}</code>
          <button type="button" className="btn" onClick={() => copy(keyText, "The key")} data-autofocus>
            <Copy size={14} aria-hidden="true" /> Copy
          </button>
        </div>
        <p className="text-xs text-text-muted">Anyone who has this key can send this app messages. If someone else sees it, remove it and make a new one.</p>
      </div>
      <div className="flex justify-end border-t border-border px-4 py-3">
        <button type="button" className="btn btn-primary" onClick={onClose}>
          I have saved it
        </button>
      </div>
    </Modal>
  );
}

/** A key made for a sender that can only be given an address: each name's full address, once. */
function AddressDialog({ made, onClose }: { made: Made; onClose: () => void }) {
  const copy = useCopy();
  return (
    <Modal title={`The address for ${made.label}`} onClose={onClose} widthClass="max-w-lg">
      <div className="flex flex-col gap-3 px-4 py-4">
        <p className="text-sm text-text-muted">
          <span className="font-medium text-text">This is shown once.</span> JimmyHQ keeps only a fingerprint of the key in it and cannot show it again. Give the sender this address now; it posts its JSON there with no header.
        </p>
        {made.addresses.map(({ label, address }, n) => (
          <div key={address} className="flex flex-col gap-1">
            {made.addresses.length > 1 ? <p className="text-xs font-medium text-text">{label}</p> : null}
            <div className="flex flex-wrap items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{address}</code>
              <button type="button" className="btn" onClick={() => copy(address, "The address")} aria-label={`Copy the address for ${label}`} data-autofocus={n === 0 ? true : undefined}>
                <Copy size={14} aria-hidden="true" /> Copy
              </button>
            </div>
          </div>
        ))}
        <p className="text-xs font-medium text-text" role="note">
          {ADDRESS_WARNING}
        </p>
        <p className="text-xs text-text-muted">A device must use an address it can reach, which may not be the one in your browser: change the start of it if so, and keep everything from /api/in/ on.</p>
      </div>
      <div className="flex justify-end border-t border-border px-4 py-3">
        <button type="button" className="btn btn-primary" onClick={onClose}>
          I have saved it
        </button>
      </div>
    </Modal>
  );
}
