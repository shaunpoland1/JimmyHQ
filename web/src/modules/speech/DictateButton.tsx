import { Cloud, LoaderCircle, Mic, RotateCw, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { formatElapsed, meterHeights } from "./lib/problems";
import { elapsedMs, escapeCancels, isCancellable, remainingMs } from "./lib/recorder";
import { useDictation } from "./useDictation";

type DictateButtonProps = {
  /** Called with the transcribed text when a recording has been turned into words. */
  onText: (text: string) => void;
  /** Which surface is asking, for the server's hosted-engine permission rule. */
  caller?: string;
  disabled?: boolean;
};

const BARS = 4;
const REST = meterHeights(0, BARS);
/** The clock turns to the warning colour this close to the automatic stop. */
const CLOSING_MS = 10_000;

/**
 * A microphone button that records and transcribes. Press to start, press again
 * to stop and send; Escape cancels. Owned by the speech module.
 */
export function DictateButton({ onText, caller, disabled = false }: DictateButtonProps) {
  const { state, dictation } = useDictation({ onText, caller });
  const [now, setNow] = useState(() => Date.now());
  const bars = useRef<(HTMLSpanElement | null)[]>([]);
  const button = useRef<HTMLButtonElement>(null);
  const phase = state.phase;
  const cancellable = isCancellable(state);

  // The clock.
  useEffect(() => {
    if (phase !== "recording") return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [phase]);

  // The level meter is drawn straight onto the bars: sixty renders a second would be wasteful.
  useEffect(() => {
    if (phase !== "recording") return;
    dictation.setLevelListener((level) => {
      const heights = meterHeights(level, BARS);
      bars.current.forEach((bar, index) => {
        if (bar) bar.style.transform = `scaleY(${heights[index] ?? REST[index] ?? 0.15})`;
      });
    });
    return () => dictation.setLevelListener(null);
  }, [phase, dictation]);

  // Escape cancels. Caught on the way down so a dialog around the button stays open;
  // a dialog opened over the button keeps its own Escape.
  useEffect(() => {
    if (!cancellable) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const dialogs = document.querySelectorAll('[aria-modal="true"]');
      const top = dialogs[dialogs.length - 1];
      const underDialog = top !== undefined && !top.contains(button.current);
      if (!escapeCancels(dictation.getState(), event, underDialog)) return;
      event.preventDefault();
      event.stopPropagation();
      dictation.cancel();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [cancellable, dictation]);

  // A button that becomes disabled mid-recording lets go of the microphone.
  useEffect(() => {
    if (disabled) dictation.cancel();
  }, [disabled, dictation]);

  const retry = phase === "error" && state.problem.retry;
  const busy = phase === "requesting" || phase === "uploading";
  let label = "Dictate";
  let announce = "";
  if (phase === "requesting") {
    label = "Starting the microphone. Press Escape to cancel.";
    announce = "Starting the microphone";
  } else if (phase === "recording") {
    label = "Stop dictation and transcribe. Press Escape to cancel.";
    announce = "Recording";
  } else if (phase === "uploading") {
    label = "Transcribing. Press Escape to cancel.";
    announce = "Transcribing";
  } else if (retry) {
    label = "Send the recording again. Press Escape to discard it.";
  }
  const title =
    phase === "recording"
      ? dictation.hosted()
        ? "Stop and transcribe (audio goes to a hosted speech server)"
        : "Stop and transcribe"
      : phase === "error"
        ? retry
          ? "Send the recording again"
          : state.problem.message
        : phase === "idle"
          ? "Dictate"
          : undefined;

  const recording = phase === "recording";
  const closing = recording && remainingMs(state, now) <= CLOSING_MS;

  return (
    <>
      <button
        ref={button}
        type="button"
        className={
          recording
            ? "inline-flex h-7 flex-none items-center gap-1.5 rounded-md bg-danger/10 px-1.5 text-danger transition-colors hover:bg-danger/15"
            : `icon-btn ${retry ? "text-warning" : ""}`
        }
        onClick={() => {
          if (!busy) dictation.press();
        }}
        disabled={disabled}
        aria-disabled={busy || undefined}
        aria-busy={busy || undefined}
        aria-pressed={recording}
        aria-label={label}
        title={title}
      >
        {recording ? (
          <>
            <Square size={12} fill="currentColor" aria-hidden="true" />
            <span className="flex h-3.5 items-center gap-0.5" aria-hidden="true">
              {REST.map((height, index) => (
                <span
                  key={index}
                  ref={(node) => {
                    bars.current[index] = node;
                  }}
                  className="h-full w-0.5 rounded-full bg-current"
                  style={{ transform: `scaleY(${height})` }}
                />
              ))}
            </span>
            <span className={`font-mono text-[11px] tabular-nums ${closing ? "text-warning" : ""}`} aria-hidden="true">
              {formatElapsed(elapsedMs(state, now))}
            </span>
            {dictation.hosted() ? <Cloud size={12} aria-hidden="true" /> : null}
          </>
        ) : busy ? (
          <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
        ) : retry ? (
          <RotateCw size={16} aria-hidden="true" />
        ) : (
          <Mic size={16} aria-hidden="true" />
        )}
      </button>
      <span className="sr-only" role="status">
        {announce}
      </span>
    </>
  );
}
