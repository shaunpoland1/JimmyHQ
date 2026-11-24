import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Globe, Search, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useSettingsQuery } from "../../hooks/useSettings";
import { useToast } from "../../hooks/useToast";
import { LOOKUP_STATUS_KEY, lookupApi } from "./api";
import type { LookupProvider, LookupStatus } from "./api";
import { KEY_MAX_LENGTH, SEARXNG_HINT, checkKey, keyState, leavesLine, providerAbout, readiness, searchWords, tryLine, tryRows } from "./lib/status";
import { safeWebUrl } from "./lib/url";
import { useLookupStatus } from "./useLookupStatus";

const CARD = "@container rounded-xl border border-border bg-surface";
const TONE_DOT = { ok: "bg-success", warn: "bg-warning", off: "bg-sage" } as const;

/** Shown above the generated Web lookup settings: status, search providers and their keys, and a way to try a search. Owned by the lookup module. */
export function LookupSettingsExtra() {
  const query = useLookupStatus();

  // A failed refresh keeps the last good answer on screen rather than dropping the card.
  if (query.data) return <StatusCard status={query.data} />;
  if (query.isPending) {
    return (
      <p className={`${CARD} px-4 py-4 text-sm text-text-faint`} role="status">
        Checking web lookup…
      </p>
    );
  }
  return (
    <div className={`${CARD} flex flex-wrap items-center justify-between gap-3 px-4 py-3.5`} role="alert">
      <p className="min-w-0 flex-1 text-sm text-text-muted">
        <span className="font-medium text-text">Web lookup status is unavailable.</span> {errorDetail(query.error)}
      </p>
      <button type="button" className="btn" onClick={() => void query.refetch()} disabled={query.isFetching}>
        Try again
      </button>
    </div>
  );
}

function StatusCard({ status }: { status: LookupStatus }) {
  const settings = useSettingsQuery().data?.settings;
  const enabledLabel = settings?.find((item) => item.key === "lookup.enabled")?.label ?? "Let models look things up on the web";
  const address = settings?.find((item) => item.key === "lookup.searxng_url")?.value;
  const state = readiness(status);
  const listed = status.providers.filter((provider) => provider.id !== "none");

  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
            <Globe size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-[13px] font-semibold text-text">Web lookup</h3>
            <p className="break-words text-xs text-text-muted">
              Search provider: {status.provider_label}
              {status.needs_key ? ` · ${status.key_set ? "key saved" : "no key yet"}` : null}
            </p>
          </div>
          <span className="inline-flex flex-none items-center gap-1.5 text-xs font-medium text-text" role="status">
            <span className={`dot ${TONE_DOT[state.tone]}`} aria-hidden="true" />
            {state.label}
          </span>
        </div>

        {status.enabled ? null : (
          <p className="text-xs text-text-muted">
            Models cannot search the web or read pages. Switch on “{enabledLabel}” below to let them. Nothing leaves this computer until then.
          </p>
        )}
        {status.search_problem ? (
          <div className="rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <p className="flex items-start gap-2 text-xs text-text">
              <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
              <span className="min-w-0 break-words">{status.search_problem}</span>
            </p>
          </div>
        ) : null}
        <p className="text-xs text-text-faint">{leavesLine(status)} Choose the provider in “Search provider” below.</p>
      </div>

      <div className="flex flex-col gap-0.5 px-4 pb-1 pt-3.5">
        <h3 className="text-[13px] font-medium text-text">Search providers</h3>
        <p className="text-xs text-text-muted">Keys are stored on this computer and never shown again once saved.</p>
      </div>
      <ul className="divide-y divide-border" aria-label="Search providers">
        {listed.map((provider) => (
          <ProviderRow key={provider.id} provider={provider} inUse={provider.id === status.provider} address={typeof address === "string" ? address : ""} />
        ))}
      </ul>

      <TrySearch status={status} />
    </div>
  );
}

function ProviderRow({ provider, inUse, address }: { provider: LookupProvider; inUse: boolean; address: string }) {
  const client = useQueryClient();
  const toast = useToast();
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const input = useRef<HTMLInputElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const replaceButton = useRef<HTMLButtonElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  /** Set when the focused control is about to leave the page: where focus should land once that is on screen. */
  const refocus = useRef<"add" | "buttons" | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const state = keyState(provider);

  const save = useMutation({
    mutationFn: (key: string) => lookupApi.saveKey(provider.id, key),
    onSuccess: (result) => {
      setValue("");
      setError(null);
      setEditing(false);
      refocus.current = "buttons";
      client.setQueryData(LOOKUP_STATUS_KEY, result);
      toast.success(`${provider.label} key saved.`);
    },
    onError: (failure) => setError(errorDetail(failure)),
  });

  const remove = useMutation({
    mutationFn: () => lookupApi.clearKey(provider.id),
    onSuccess: (result) => {
      setConfirming(false);
      refocus.current = "add";
      client.setQueryData(LOOKUP_STATUS_KEY, result);
      toast.success(`${provider.label} key removed.`);
    },
    onError: (failure) => {
      setConfirming(false);
      toast.error(errorDetail(failure));
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const check = checkKey(value);
    if (!check.ok) {
      setError(check.error);
      input.current?.focus();
      return;
    }
    setError(null);
    save.mutate(check.key);
  };

  const cancel = () => {
    setEditing(false);
    setValue("");
    setError(null);
    refocus.current = state === "saved" ? "buttons" : "add";
  };

  // Saving, cancelling and removing each take away the control that had focus.
  useEffect(() => {
    if (!refocus.current) return;
    const target = refocus.current === "add" ? addButton.current : (replaceButton.current ?? removeButton.current);
    if (!target) return;
    refocus.current = null;
    target.focus();
  });

  const name = provider.label;
  return (
    <li className="flex flex-col gap-2.5 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <p className="flex flex-wrap items-center gap-x-2 text-[13px] font-medium text-text">
            {name}
            {inUse ? <span className="chip font-sans">In use</span> : null}
          </p>
          <p className="mt-0.5 text-xs text-text-muted">{providerAbout(provider.id)}</p>
          {provider.id === "searxng" ? (
            <p className="mt-1 text-xs text-text-faint">
              {SEARXNG_HINT} {address ? `It is set to ${address}.` : "No address is set yet."}
            </p>
          ) : null}
        </div>
        <div className="flex flex-none flex-wrap items-center gap-2">
          {state === "none" ? (
            <span className="text-xs text-text-faint">No key needed</span>
          ) : (
            <>
              <span className="text-xs text-text-muted">{state === "saved" ? "Key saved" : "No key yet"}</span>
              {editing ? null : state === "saved" ? (
                <>
                  <button ref={replaceButton} type="button" className="btn" onClick={() => setEditing(true)} aria-label={`Replace the ${name} key`}>
                    Replace
                  </button>
                  <button ref={removeButton} type="button" className="btn" onClick={() => setConfirming(true)} aria-label={`Remove the ${name} key`}>
                    Remove
                  </button>
                </>
              ) : (
                <button ref={addButton} type="button" className="btn" onClick={() => setEditing(true)} aria-label={`Add a ${name} key`}>
                  Add key
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {editing ? (
        <form className="flex flex-col gap-2 @md:flex-row @md:items-start" onSubmit={submit} noValidate>
          <div className="min-w-0 flex-1">
            <label htmlFor={inputId} className="sr-only">
              {name} key
            </label>
            <input
              ref={input}
              id={inputId}
              className="input font-mono"
              type="password"
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={KEY_MAX_LENGTH + 20}
              placeholder={state === "saved" ? "Paste the new key" : "Paste the key"}
              value={value}
              autoFocus
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? errorId : undefined}
              onChange={(event) => {
                setValue(event.target.value);
                if (error) setError(null);
              }}
            />
            {error ? (
              <p id={errorId} className="mt-1 break-words text-xs text-danger" role="alert">
                {error}
              </p>
            ) : null}
          </div>
          <div className="flex flex-none gap-2">
            <button type="submit" className="btn btn-primary" disabled={save.isPending || value.trim() === ""}>
              {save.isPending ? "Saving…" : "Save key"}
            </button>
            <button type="button" className="btn" onClick={cancel}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {confirming ? (
        <ConfirmDialog
          title={`Remove the ${name} key?`}
          message={
            inUse
              ? `The saved key will be deleted from this computer. Searching stops working until a new key is saved, because ${name} is the search provider in use.`
              : "The saved key will be deleted from this computer."
          }
          confirmLabel="Remove key"
          danger
          busy={remove.isPending}
          onConfirm={() => remove.mutate()}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </li>
  );
}

function TrySearch({ status }: { status: LookupStatus }) {
  const inputId = useId();
  const [typed, setTyped] = useState("");
  const search = useMutation({ mutationFn: (query: string) => lookupApi.trySearch(query) });
  const words = searchWords(typed);
  const target = status.provider === "none" ? "the search provider once you choose one" : status.provider === "searxng" ? "your own SearXNG server" : status.provider_label;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (words && !search.isPending) search.mutate(words);
  };

  const rows = search.data ? tryRows(search.data) : [];
  return (
    <div className="flex flex-col gap-2 px-4 py-3.5">
      <div className="min-w-0">
        <label htmlFor={inputId} className="text-[13px] font-medium text-text">
          Try a search
        </label>
        <p className="mt-0.5 text-xs text-text-muted">
          Runs one search with what is set up now, even while models are not allowed to look things up. The words you type go to {target}.
        </p>
      </div>
      <form className="flex flex-col gap-2 @md:flex-row @md:items-start" onSubmit={submit} noValidate>
        <input
          id={inputId}
          className="input min-w-0 flex-1"
          type="search"
          autoComplete="off"
          spellCheck={false}
          maxLength={400}
          placeholder="For example: broad beans sowing time"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
        <button type="submit" className="btn btn-primary flex-none" disabled={words === null || search.isPending}>
          <Search size={14} aria-hidden="true" />
          {search.isPending ? "Searching…" : "Search"}
        </button>
      </form>

      {search.isError ? (
        <p role="alert" className="flex items-start gap-2 break-words rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-xs text-text">
          <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
          <span className="min-w-0">{errorDetail(search.error)}</span>
        </p>
      ) : null}
      {search.data ? (
        <div role="status" className="flex min-w-0 flex-col gap-2">
          <p className="text-xs text-text-muted">{tryLine(search.data)}</p>
          {rows.length > 0 ? (
            <ul className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2.5" aria-label="Search results">
              {rows.map((row, at) => {
                const href = safeWebUrl(row.url);
                return (
                  <li key={at} className="flex min-w-0 flex-col gap-0.5">
                    <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer" className="min-w-0 break-words text-[13px] text-text underline underline-offset-2 [overflow-wrap:anywhere]">
                          {row.title}
                        </a>
                      ) : (
                        <span className="min-w-0 break-words text-[13px] text-text [overflow-wrap:anywhere]">{row.title}</span>
                      )}
                      {row.host ? <span className="font-mono text-[10px] text-text-faint">{row.host}</span> : null}
                    </span>
                    {row.snippet ? <span className="break-words text-xs text-text-muted [overflow-wrap:anywhere]">{row.snippet}</span> : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-xs text-text-faint">The search worked but found nothing. Try other words.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
