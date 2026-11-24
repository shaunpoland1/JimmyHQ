import { Cloud, LoaderCircle, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { formatElapsed } from "../speech/lib/problems";
import { elapsedMs, remainingMs } from "../speech/lib/recorder";
import { DESK } from "./dictation";

/** The clock turns to the warning colour this close to the automatic stop. */
const CLOSING_MS = 10_000;

/**
 * The shell's own recording surface, drawn over an app's screen while that app's page
 * waits for `hq.speech.dictate`. JimmyHQ draws it, never the app: it names the app, shows
 * the page's hint as plain text, the clock, Stop and Cancel; Escape cancels. It covers
 * the frame, so the page cannot be clicked while it records. Only the screen whose frame
 * asked (`owner`) draws it.
 */
export function DictateSurface({ owner }: { owner: object }) {
  const view = useSyncExternalStore(DESK.subscribe, DESK.current);
  const mine = view !== null && view.owner === owner ? view : null;
  const phase = mine?.state.phase ?? null;
  const active = mine !== null;
  const [now, setNow] = useState(() => Date.now());
  const box = useRef<HTMLDivElement>(null);
  const stop = useRef<HTMLButtonElement>(null);

  // The clock.
  useEffect(() => {
    if (phase !== "recording") return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [phase]);

  // The keyboard comes out of the frame onto the surface, so Escape reaches the shell.
  useEffect(() => {
    if (!active) return;
    (stop.current ?? box.current)?.focus();
  }, [active, phase]);

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      event.preventDefault();
      event.stopPropagation();
      DESK.cancel(owner);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [active, owner]);

  if (!mine) return null;
  const state = mine.state;
  const recording = state.phase === "recording";
  const retry = state.phase === "error" && state.problem.retry;
  const closing = recording && remainingMs(state, now) <= CLOSING_MS;
  let line: string;
  if (state.phase === "recording") line = "Recording… press Stop when you are done.";
  else if (state.phase === "uploading") line = "Turning what you said into text…";
  else if (state.phase === "error") line = state.problem.message;
  else line = "Starting the microphone…";

  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-[1px]">
      <div
        ref={box}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`${mine.appName} asks you to dictate`}
        className="flex w-full max-w-sm flex-col gap-3 rounded-xl border border-border bg-surface p-4 text-sm shadow-lg outline-none"
      >
        <div className="flex items-center gap-2">
          {recording ? (
            <span className="inline-block h-2.5 w-2.5 flex-none animate-pulse rounded-full bg-danger" aria-hidden="true" />
          ) : state.phase === "error" ? (
            <Mic size={14} className="flex-none text-warning" aria-hidden="true" />
          ) : (
            <LoaderCircle size={14} className="flex-none animate-spin text-text-muted" aria-hidden="true" />
          )}
          <h2 className="min-w-0 flex-1 truncate font-medium text-text">{mine.appName} asks you to dictate</h2>
          {recording ? (
            <span className={`flex-none font-mono text-xs tabular-nums ${closing ? "text-warning" : "text-text-muted"}`}>
              {formatElapsed(elapsedMs(state, now))} / {formatElapsed(mine.maxSeconds * 1000)}
            </span>
          ) : null}
        </div>
        {mine.hint ? <p className="break-words text-text">{mine.hint}</p> : null}
        <p className="text-text-muted" role="status">
          {line}
        </p>
        <p className="text-xs text-text-faint">
          {mine.hosted ? (
            <>
              <Cloud size={12} className="mr-1 inline align-[-2px]" aria-hidden="true" />
              The audio goes to a hosted speech server.{" "}
            </>
          ) : null}
          {mine.appName} gets only the words, once you stop.
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={() => DESK.cancel(owner)}>
            Cancel
          </button>
          {recording || retry ? (
            <button ref={stop} type="button" className={recording ? "btn btn-danger" : "btn btn-primary"} onClick={() => DESK.stop()}>
              {recording ? <Square size={12} fill="currentColor" aria-hidden="true" /> : null}
              {recording ? "Stop" : "Send again"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
