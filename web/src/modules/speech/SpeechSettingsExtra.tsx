import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Cloud, Copy, Download, HardDrive, LoaderCircle, Mic, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useSettingActions, useSettingsQuery } from "../../hooks/useSettings";
import { useToast } from "../../hooks/useToast";
import { SPEECH_STATUS_KEY, speechApi } from "./api";
import type { SpeechStatus } from "./api";
import { DictateButton } from "./DictateButton";
import { hostedBlocked } from "./lib/problems";
import { KEY_MAX_LENGTH, checkKey, commandIn, downloadState, limitsLine, privacyLine, readiness, withCaller, withoutTicks } from "./lib/settings";
import { DEFAULT_CALLER } from "./types";
import { useSpeechStatus } from "./useSpeechStatus";

const CARD = "@container rounded-xl border border-border bg-surface";
const TONE_DOT = { ok: "bg-success", warn: "bg-warning", off: "bg-sage" } as const;
const HOSTED_SETTING = "speech.hosted_callers";

/** Shown above the generated Voice settings: engine status, the endpoint key and a test. Owned by the speech module. */
export function SpeechSettingsExtra() {
  const query = useSpeechStatus();

  // A failed refresh keeps the last good answer on screen rather than dropping the card.
  if (query.data) return <StatusCard status={query.data} />;
  if (query.isPending) {
    return (
      <p className={`${CARD} px-4 py-4 text-sm text-text-faint`} role="status">
        Checking dictation…
      </p>
    );
  }
  return (
    <div className={`${CARD} flex flex-wrap items-center justify-between gap-3 px-4 py-3.5`} role="alert">
      <p className="min-w-0 flex-1 text-sm text-text-muted">
        <span className="font-medium text-text">Dictation status is unavailable.</span> {errorDetail(query.error)}
      </p>
      <button type="button" className="btn" onClick={() => void query.refetch()} disabled={query.isFetching}>
        Try again
      </button>
    </div>
  );
}

function StatusCard({ status }: { status: SpeechStatus }) {
  const client = useQueryClient();
  const settings = useSettingsQuery().data?.settings;
  const actions = useSettingActions();
  const engineSetting = settings?.find((item) => item.key === "speech.engine");
  const engineLabel = engineSetting?.choices?.find((choice) => choice.value === status.engine)?.label ?? status.engine;
  const hostedLabel = settings?.find((item) => item.key === HOSTED_SETTING)?.label ?? "Allowed to use a hosted speech server";
  const state = readiness(status);
  const privacy = privacyLine(status);
  const command = commandIn(status.problem);
  const blocked = status.ready && hostedBlocked(status, DEFAULT_CALLER);
  const showKey = status.engine === "endpoint" || status.key_set;

  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
            <Mic size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-[13px] font-semibold text-text">Dictation</h3>
            <p className="break-words text-xs text-text-muted">
              {engineLabel}
              {status.model ? (
                <>
                  {" · "}
                  <span className="font-mono text-[11px]">{status.model}</span>
                </>
              ) : null}
              {status.engine !== "off" ? ` · ${status.language ? `language ${status.language}` : "language detected each time"}` : null}
            </p>
          </div>
          <div className="flex flex-none flex-wrap items-center gap-2">
            {status.location ? (
              <span className="chip gap-1 font-sans">
                {status.hosted ? <Cloud size={11} aria-hidden="true" /> : <HardDrive size={11} aria-hidden="true" />}
                {status.hosted ? "Hosted" : "Local"}
              </span>
            ) : null}
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-text" role="status">
              <span className={`dot ${TONE_DOT[state.tone]}`} aria-hidden="true" />
              {state.label}
            </span>
          </div>
        </div>

        {status.problem ? (
          <div className="rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <p className="flex items-start gap-2 text-xs text-text">
              {status.engine === "off" ? null : <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />}
              <span className="min-w-0 break-words">{withoutTicks(status.problem)}</span>
            </p>
            {command ? <CommandLine command={command} /> : null}
          </div>
        ) : null}

        {blocked ? (
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2.5 @md:flex-row @md:items-center @md:justify-between">
            <p className="flex min-w-0 items-start gap-2 text-xs text-text">
              <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
              <span className="min-w-0 break-words">
                Dictation is not allowed to use this hosted speech server yet, so the microphone button will not record. Add {DEFAULT_CALLER} to
                '{hostedLabel}' below.
              </span>
            </p>
            <button
              type="button"
              className="btn flex-none self-start @md:self-auto"
              onClick={() =>
                // The live feed normally refreshes the status; ask again anyway in case the feed is down.
                void actions
                  .save(HOSTED_SETTING, withCaller(status.hosted_callers, DEFAULT_CALLER))
                  .then(() => client.invalidateQueries({ queryKey: SPEECH_STATUS_KEY }))
              }
            >
              Allow dictation
            </button>
          </div>
        ) : null}

        {privacy ? <p className="text-xs text-text-muted">{privacy}</p> : null}
        <p className="text-xs text-text-faint">
          {limitsLine(status)} No audio and no words are kept: the only record is a receipt on the Tape.
        </p>
      </div>

      {status.download ? <DownloadSection status={status} /> : null}
      {showKey ? <KeySection status={status} /> : null}
      <TestSection />
    </div>
  );
}

function CommandLine({ command }: { command: string }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      toast.success("Command copied.");
    } catch {
      toast.error("The command could not be copied. Select it and copy it by hand.");
    }
  };
  return (
    <div className="mt-2 flex items-center gap-2">
      <code className="min-w-0 flex-1 select-all overflow-x-auto whitespace-nowrap rounded-md border border-border bg-surface px-2 py-1 font-mono text-xs text-text">
        {command}
      </code>
      <button type="button" className="icon-btn" onClick={() => void copy()} aria-label="Copy the install command" title="Copy">
        <Copy size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

/** Parakeet's model: whether it is downloaded, and a button to fetch it before the first recording. */
function DownloadSection({ status }: { status: SpeechStatus }) {
  const client = useQueryClient();
  const toast = useToast();
  const state = downloadState(status);
  const prepare = useMutation({
    mutationFn: () => speechApi.prepare(),
    onSuccess: (next) => client.setQueryData<SpeechStatus>(SPEECH_STATUS_KEY, next),
    onError: (failure) => toast.error(errorDetail(failure)),
  });
  if (!state) return null;
  const busy = state.tone === "busy" || prepare.isPending;

  return (
    <div className="flex flex-col gap-2 px-4 py-3.5 @md:flex-row @md:items-center @md:justify-between @md:gap-6">
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-text">Parakeet model</p>
        <p className="mt-0.5 flex items-start gap-1.5 break-words text-xs text-text-muted" role="status">
          {state.tone === "busy" ? <LoaderCircle size={13} className="mt-0.5 flex-none animate-spin text-accent" aria-hidden="true" /> : null}
          {state.tone === "warn" && status.download?.problem ? (
            <TriangleAlert size={13} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
          ) : null}
          <span className="min-w-0">{state.text}</span>
        </p>
      </div>
      {state.action ? (
        <button type="button" className="btn flex-none self-start @md:self-auto" disabled={busy} onClick={() => prepare.mutate()}>
          <Download size={14} aria-hidden="true" />
          {prepare.isPending ? "Starting…" : state.action}
        </button>
      ) : null}
    </div>
  );
}

function KeySection({ status }: { status: SpeechStatus }) {
  const client = useQueryClient();
  const toast = useToast();
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const input = useRef<HTMLInputElement>(null);
  const replaceButton = useRef<HTMLButtonElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  /** Set when the focused control is about to leave the page: where focus should land once that is on screen. */
  const refocus = useRef<"input" | "buttons" | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [replacing, setReplacing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const setKeyFlag = (keySet: boolean) => {
    client.setQueryData<SpeechStatus>(SPEECH_STATUS_KEY, (current) => (current ? { ...current, key_set: keySet } : current));
    // Whether the engine is ready can depend on the key, so ask again.
    void client.invalidateQueries({ queryKey: SPEECH_STATUS_KEY });
  };

  const save = useMutation({
    mutationFn: (key: string) => speechApi.saveKey(key),
    onSuccess: (result) => {
      setValue("");
      setError(null);
      setReplacing(false);
      refocus.current = "buttons";
      setKeyFlag(result.key_set);
      toast.success("Speech server key saved.");
    },
    onError: (failure) => setError(errorDetail(failure)),
  });

  const remove = useMutation({
    mutationFn: () => speechApi.removeKey(),
    onSuccess: (result) => {
      setConfirming(false);
      refocus.current = "input";
      setKeyFlag(result.key_set);
      toast.success("Speech server key removed.");
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

  const editing = !status.key_set || replacing;
  const unused = status.engine !== "endpoint";

  // Saving, cancelling and removing each take away the control that had focus.
  // The new status can arrive a render later, so wait until the target is there.
  useEffect(() => {
    if (!refocus.current) return;
    const target = refocus.current === "input" ? (editing ? input.current : null) : editing ? null : (replaceButton.current ?? removeButton.current);
    if (!target) return;
    refocus.current = null;
    target.focus();
  });

  return (
    <div className="flex flex-col gap-2.5 px-4 py-3.5">
      <div className="flex flex-col gap-2 @md:flex-row @md:items-center @md:justify-between @md:gap-6">
        <div className="min-w-0 flex-1">
          <label htmlFor={editing ? inputId : undefined} className="text-[13px] font-medium text-text">
            Speech server key
          </label>
          <p className="mt-0.5 text-xs text-text-muted">
            {status.key_set
              ? unused
                ? "A key is saved but not in use, because speech to text is not set to a speech server. It is never shown again."
                : "A key is saved. It is sent only to the speech server and is never shown again."
              : "The API key for the speech server. A hosted server needs one; one on your own network usually does not. It is stored on this computer and never shown again."}
          </p>
        </div>
        {status.key_set && !replacing ? (
          <div className="flex flex-none flex-wrap gap-2">
            {unused ? null : (
              <button ref={replaceButton} type="button" className="btn" onClick={() => setReplacing(true)}>
                Replace
              </button>
            )}
            <button
              ref={removeButton}
              type="button"
              className="btn"
              onClick={() => setConfirming(true)}
              aria-label="Remove the speech server key"
            >
              Remove
            </button>
          </div>
        ) : null}
      </div>

      {editing ? (
        <form className="flex flex-col gap-2 @md:flex-row @md:items-start" onSubmit={submit} noValidate>
          <div className="min-w-0 flex-1">
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
              placeholder={status.key_set ? "Paste the new key" : "Paste the key"}
              value={value}
              autoFocus={replacing}
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
            {replacing ? (
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setReplacing(false);
                  setValue("");
                  setError(null);
                  refocus.current = "buttons";
                }}
              >
                Cancel
              </button>
            ) : null}
          </div>
        </form>
      ) : null}

      {confirming ? (
        <ConfirmDialog
          title="Remove the speech server key?"
          message={
            unused
              ? "The saved key will be deleted from this computer."
              : "The saved key will be deleted from this computer. A hosted speech server will stop working until a new key is saved."
          }
          confirmLabel="Remove key"
          danger
          busy={remove.isPending}
          onConfirm={() => remove.mutate()}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </div>
  );
}

function TestSection() {
  const [text, setText] = useState("");
  const labelId = useId();
  return (
    <div className="flex flex-col gap-2 px-4 py-3.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p id={labelId} className="text-[13px] font-medium text-text">
            Test dictation
          </p>
          <p className="mt-0.5 text-xs text-text-muted">Press the microphone, say a sentence, then press it again. The words appear below.</p>
        </div>
        <div className="flex flex-none items-center gap-1">
          {text ? (
            <button type="button" className="btn h-7 px-2 text-xs" onClick={() => setText("")}>
              Clear
            </button>
          ) : null}
          <DictateButton caller={DEFAULT_CALLER} onText={(heard) => setText((current) => (current ? `${current} ${heard}` : heard))} />
        </div>
      </div>
      <textarea
        className="input h-auto min-h-16 resize-y py-2 leading-relaxed"
        rows={2}
        readOnly
        value={text}
        aria-labelledby={labelId}
        aria-live="polite"
        placeholder="Nothing dictated yet."
      />
    </div>
  );
}
