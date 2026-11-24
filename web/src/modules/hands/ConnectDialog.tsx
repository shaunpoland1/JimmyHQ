import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import { useEffect, useReducer, useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { handsApi } from "./api";
import type { Provider } from "./api";
import { START, accountNameProblem, accountSlug, connectReducer, keyProblem, labelProblem, pollDelay, waitingLine } from "./lib/connect";
import { connectShape, inboxNameProblem } from "./lib/mail";
import { redirectSentence } from "./lib/wording";
import { HANDS_KEYS, useTypesQuery } from "./queries";
import { Notice } from "./ui";

type Props = { provider: Provider; takenNames: string[]; label: string; onClose: () => void };

/**
 * Connect an account of one provider. The provider answers with the account itself, with a
 * page of its own for the owner to sign in at, or with a request for its key first.
 */
export function ConnectDialog({ provider, takenNames, label: providerLabel, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const kind = useTypesQuery().data?.types.find((item) => item.type === provider.type);
  const [state, dispatch] = useReducer(connectReducer, START);
  const [name, setName] = useState("");
  const [short, setShort] = useState("");
  const [shortEdited, setShortEdited] = useState(false);
  const [app, setApp] = useState("");
  const [ownInbox, setOwnInbox] = useState(false);
  const [key, setKey] = useState("");
  const [showErrors, setShowErrors] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  /** The server holds a half-made connection that must be dropped when the owner leaves. */
  const loose = useRef(false);
  const slug = (shortEdited ? short : accountSlug(name)).trim();
  const shape = connectShape(provider.type);
  const appError = shape.appInSight ? inboxNameProblem(app) : null;

  const drop = () => {
    if (!loose.current) return;
    loose.current = false;
    // Best effort: only the half-made connection is dropped. Nothing connected is touched.
    void handsApi.cancelConnect(slug).catch(() => undefined);
  };

  const refresh = () => {
    void client.invalidateQueries({ queryKey: HANDS_KEYS.accounts });
    void client.invalidateQueries({ queryKey: HANDS_KEYS.providers });
  };

  const begin = async () => {
    dispatch({ type: "start" });
    try {
      const answer = await handsApi.connect({ provider_id: provider.id, account: slug, label: name.trim(), app: (shape.appInSight ? app.trim().toLowerCase() : app.trim()) || null, own_inbox: shape.ownInboxFixed || ownInbox });
      if (answer.status === "redirect") loose.current = true;
      dispatch({ type: "answer", answer, now: Date.now() });
      if (answer.status === "ready") {
        refresh();
        toast.success(`Connected ${answer.account?.label ?? name.trim()}. Switch on actions for an agent from its Integrations tab.`);
      }
    } catch (error) {
      dispatch({ type: "start_failed", error: errorDetail(error) });
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setShowErrors(true);
    if (labelProblem(name) || accountNameProblem(slug, takenNames) || appError) return;
    void begin();
  };

  // While waiting: ask the server whether the account has shown up, and watch the clock.
  const waiting = state.phase === "waiting" ? state : null;
  const polls = waiting?.polls ?? 0;
  const checking = waiting?.checking ?? false;
  useEffect(() => {
    if (state.phase !== "waiting") return;
    const clock = setInterval(() => {
      setNow(Date.now());
      dispatch({ type: "tick", now: Date.now() });
    }, 1000);
    return () => clearInterval(clock);
  }, [state.phase]);

  const check = async () => {
    dispatch({ type: "checking" });
    try {
      const answer = await handsApi.finish(slug);
      dispatch({ type: "checked", answer });
      if (answer.status === "ready") {
        loose.current = false;
        refresh();
        toast.success(`Connected ${answer.account?.label ?? name.trim()}. Switch on actions for an agent from its Integrations tab.`);
      }
    } catch (error) {
      dispatch({ type: "check_failed", error: errorDetail(error) });
    }
  };
  const checkRef = useRef(check);
  checkRef.current = check;

  useEffect(() => {
    if (state.phase !== "waiting" || checking) return;
    const timer = setTimeout(() => void checkRef.current(), pollDelay(polls));
    return () => clearTimeout(timer);
  }, [state.phase, polls, checking]);

  // A connection that ended without an account is dropped on the server.
  useEffect(() => {
    if (state.phase === "cancelled" || state.phase === "timeout" || state.phase === "failed") drop();
  }, [state.phase]);

  useEffect(() => {
    if (state.phase === "ready") onClose();
  }, [state.phase]);

  const leave = () => {
    drop();
    refresh();
    onClose();
  };

  const saveKey = async (event: FormEvent) => {
    event.preventDefault();
    const problem = keyProblem(key);
    if (problem) {
      setKeyError(problem);
      return;
    }
    setKeyBusy(true);
    try {
      await handsApi.setKey(provider.id, key.trim());
      setKey("");
      setKeyError(null);
      refresh();
      await begin();
    } catch (error) {
      setKeyError(errorDetail(error));
    } finally {
      setKeyBusy(false);
    }
  };

  const nameError = showErrors ? labelProblem(name) : null;
  const shortError = showErrors || shortEdited ? accountNameProblem(slug, takenNames) : null;
  const busy = state.phase === "starting";

  return (
    <Modal title={`Connect an account to ${providerLabel}`} onClose={leave} widthClass="max-w-lg">
      <div className="@container flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
        {state.phase === "waiting" ? (
          <div className="flex flex-col gap-3" role="status" aria-live="polite">
            <p className="flex items-center gap-2 text-sm font-medium text-text">
              <Loader2 size={15} className="animate-spin text-accent" aria-hidden="true" />
              Waiting for you to finish there
            </p>
            <p className="text-xs text-text-muted">{redirectSentence(kind)}</p>
            <a className="btn btn-primary self-start no-underline" href={state.url} target="_blank" rel="noopener noreferrer" data-autofocus>
              Open {providerLabel}'s page
              <ExternalLink size={13} aria-hidden="true" />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
            <p className="text-xs text-text-faint">{waitingLine(state, now)}</p>
            {state.lastError ? <Notice tone="warn">{state.lastError}</Notice> : null}
          </div>
        ) : state.phase === "needs_key" ? (
          <form className="flex flex-col gap-3" onSubmit={(event) => void saveKey(event)} noValidate>
            <p className="text-sm text-text-muted">{providerLabel} needs its key before an account can be connected. Paste it here. It is saved on this computer in the secret store and never shown again.</p>
            <Field label="Key" type="password" value={key} onChange={(event) => setKey(event.target.value)} error={keyError ?? undefined} autoComplete="off" spellCheck={false} data-autofocus />
            <div className="flex justify-end">
              <button type="submit" className="btn btn-primary" disabled={keyBusy}>
                {keyBusy ? "Saving…" : "Save key and connect"}
              </button>
            </div>
          </form>
        ) : state.phase === "timeout" ? (
          <Notice tone="warn" live>
            Nothing was connected: it was not finished at the provider within ten minutes. Start again when you are ready.
          </Notice>
        ) : state.phase === "cancelled" ? (
          <Notice live>Cancelled. Nothing was connected. If you already approved it at the provider's page, remove the access there.</Notice>
        ) : (
          <form id="hands-connect-form" className="flex flex-col gap-4" onSubmit={submit} noValidate>
            <p className="text-sm text-text-muted">
              {shape.intro ?? `${providerLabel} may sign you in on its own page, or may just need its key. Either way nothing is switched on for any agent until you do it.`}
            </p>
            <Field
              label="Name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              error={nameError ?? undefined}
              hint="What you call this account, for example Work mail."
              maxLength={80}
              autoComplete="off"
              data-autofocus
            />
            <Field
              label="Short name"
              value={shortEdited ? short : accountSlug(name)}
              onChange={(event) => {
                setShortEdited(true);
                setShort(event.target.value.toLowerCase());
              }}
              error={shortError ?? undefined}
              hint="Agents' tools start with this, for example mail_messages_list. Lower-case letters, digits and _."
              maxLength={24}
              autoComplete="off"
              spellCheck={false}
            />
            {shape.appInSight ? (
              <Field label={shape.appLabel} value={app} onChange={(event) => setApp(event.target.value)} error={showErrors ? (appError ?? undefined) : undefined} hint={shape.appHint} maxLength={64} autoComplete="off" spellCheck={false} />
            ) : (
              <details className="rounded-lg border border-border">
                <summary className="cursor-pointer rounded-lg px-3 py-2 text-xs font-medium text-text">Advanced</summary>
                <div className="flex flex-col gap-3 border-t border-border px-3 py-3">
                  <Field label={shape.appLabel} value={app} onChange={(event) => setApp(event.target.value)} hint={shape.appHint} maxLength={80} autoComplete="off" spellCheck={false} />
                  <label className="flex items-start gap-2 text-xs text-text">
                    <input type="checkbox" className="mt-0.5" checked={ownInbox} onChange={(event) => setOwnInbox(event.target.checked)} />
                    <span>
                      This is an agent's own inbox. Addresses in its answers are then shown to that agent by default. You can change this for each action.
                    </span>
                  </label>
                </div>
              </details>
            )}
            {state.phase === "failed" ? <Notice tone="bad">{state.error}</Notice> : null}
          </form>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
        {state.phase === "waiting" ? (
          <>
            <button type="button" className="btn" onClick={() => dispatch({ type: "cancel" })}>
              Cancel
            </button>
            <button type="button" className="btn" onClick={() => void check()} disabled={checking}>
              {checking ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : null}
              Check now
            </button>
          </>
        ) : state.phase === "timeout" || state.phase === "cancelled" ? (
          <>
            <button type="button" className="btn" onClick={leave}>
              Close
            </button>
            <button type="button" className="btn btn-primary" onClick={() => dispatch({ type: "again" })}>
              Start again
            </button>
          </>
        ) : state.phase === "needs_key" ? (
          <button type="button" className="btn" onClick={leave}>
            Cancel
          </button>
        ) : (
          <>
            <button type="button" className="btn" onClick={leave}>
              Cancel
            </button>
            <button type="submit" form="hands-connect-form" className="btn btn-primary" disabled={busy}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
