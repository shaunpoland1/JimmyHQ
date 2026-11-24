import { ArrowUp, Square } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { Switch } from "../../components/Switch";
import { ModelPicker } from "../models/ModelPicker";
import type { ModelPickerOptions, ModelPickerValue } from "../models/ModelPicker";
import { DictateButton } from "../speech/DictateButton";
import { canSend, composerCommand, counterFor, counterText, insertAtCursor } from "./lib/compose";
import { LOCAL_LABEL, MEMORY_LABEL, explained, localHelp, memoryHelp } from "./lib/memory";
import type { SwitchName } from "./lib/memory";
import { ON_LABEL, recordHelp } from "./lib/record";
import { WEB_LABEL } from "./lib/web";
import type { LocalToggle } from "./lib/memory";
import type { WebToggle } from "./lib/web";

type ComposerProps = {
  text: string;
  onText: (text: string) => void;
  onSend: () => void;
  onStop: () => void;
  /** A reply is being written in this conversation, here or in another tab. */
  busy: boolean;
  stopping: boolean;
  /** Something else stands in the way of sending for now (still loading, starting the conversation). */
  blocked: boolean;
  pickerOptions: ModelPickerOptions;
  /** The choices are still loading or cannot be changed right now. */
  pickerDisabled: boolean;
  value: ModelPickerValue;
  onValue: (value: ModelPickerValue) => void;
  localOnly: boolean;
  onLocalOnly: (localOnly: boolean) => void;
  /** The record switch: how what is sent from here will be kept. */
  onRecord: boolean;
  onOnRecord: (onRecord: boolean) => void;
  /** The record switch cannot be flipped right now (a reply is being written, or still loading). */
  recordDisabled: boolean;
  /** Memory is searched before each reply in this conversation. */
  useMemory: boolean;
  onUseMemory: (useMemory: boolean) => void;
  /** Whether the model chosen may be sent memory; null while that is not known. */
  memoryAllowed: boolean | null;
  /** The Web switch: the next message may search the web and read pages. Always false while it cannot be used. */
  web: boolean;
  onWeb: (web: boolean) => void;
  /** Whether the switch can be used, and if not, the sentence that says why. */
  webToggle: WebToggle;
  /** Whether Local only has anything to do, and if not, why. */
  localToggle: LocalToggle;
  /** The sentence that says what the switch means as it stands, and what leaves the machine. */
  webHelpText: string;
  /** Where Settings opens at Web lookup. */
  webSettingsHref: string;
  /** The server's limit on one message, in characters. */
  maxChars: number;
  placeholder: string;
  inputRef: RefObject<HTMLTextAreaElement | null>;
};

const MAX_HEIGHT_PX = 240;
/** How long the line under the composer keeps explaining a switch that was just flipped. */
const EXPLAIN_MS = 6000;

type ChipProps = {
  name: SwitchName;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled: boolean;
  /** The id of the sentence that says what the switch means as it stands. */
  describedBy: string;
  /** Something about this switch wants a second look (it is on, and cannot take effect). */
  warn?: boolean;
  /** Why the switch is inactive, shown as a tooltip. */
  title?: string | null;
  onHover: (name: SwitchName | null) => void;
  onFocus: (name: SwitchName | null) => void;
};

/**
 * One switch with its words, as a compact chip. The chip is the switch's
 * label, so pressing anywhere on it, the words included, flips the switch.
 */
function Chip({ name, label, checked, onChange, disabled, describedBy, warn = false, title = null, onHover, onFocus }: ChipProps) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      title={title ?? undefined}
      onPointerEnter={() => onHover(name)}
      onPointerLeave={() => onHover(null)}
      onFocus={() => onFocus(name)}
      onBlur={() => onFocus(null)}
      className={`inline-flex h-8 select-none items-center gap-1.5 whitespace-nowrap rounded-full border py-0 pl-1.5 pr-2.5 text-xs transition-colors ${
        disabled ? "cursor-default opacity-60" : "cursor-pointer hover:bg-surface-2"
      } ${checked ? "border-border text-text" : "border-border text-text-muted"}`}
    >
      <Switch id={id} checked={checked} onChange={onChange} describedBy={describedBy} disabled={disabled} />
      {label}
      {warn ? <span className="dot !size-1.5 bg-warning" aria-hidden="true" /> : null}
    </label>
  );
}

/** Where a message is written: text, the model choice, the four switches, dictation and Send or Stop. */
export function Composer({
  text,
  onText,
  onSend,
  onStop,
  busy,
  stopping,
  blocked,
  pickerOptions,
  pickerDisabled,
  value,
  onValue,
  localOnly,
  onLocalOnly,
  onRecord,
  onOnRecord,
  recordDisabled,
  useMemory,
  onUseMemory,
  memoryAllowed,
  web,
  onWeb,
  webToggle,
  localToggle,
  webHelpText,
  webSettingsHref,
  maxChars,
  placeholder,
  inputRef,
}: ComposerProps) {
  const localNote = useId();
  const recordNote = useId();
  const memoryNoteId = useId();
  const webNoteId = useId();
  const counterId = useId();
  // Which switch the one line under the composer is explaining.
  const [hovered, setHovered] = useState<SwitchName | null>(null);
  const [focused, setFocused] = useState<SwitchName | null>(null);
  const [changed, setChanged] = useState<SwitchName | null>(null);
  useEffect(() => {
    if (changed === null) return;
    const timer = setTimeout(() => setChanged(null), EXPLAIN_MS);
    return () => clearTimeout(timer);
  }, [changed, localOnly, onRecord, useMemory, web]);
  const flipped = (name: SwitchName, apply: (next: boolean) => void) => (next: boolean) => {
    setChanged(name);
    apply(next);
  };
  const help: Record<SwitchName, string> = {
    local: localToggle.reason ?? localHelp(localOnly),
    record: recordHelp(onRecord),
    memory: memoryHelp(useMemory, memoryAllowed),
    web: webToggle.reason ?? webHelpText,
  };
  // Read by destructuring: the public-safe check takes a dotted read of "local" for a hostname.
  const { local: localText, record: recordText, memory: memoryText, web: webText } = help;
  const memoryBlocked = useMemory && memoryAllowed === false;
  const showing = explained(hovered, focused, changed);
  /** Where the cursor goes once dictated words have been put in. */
  const cursor = useRef<number | null>(null);

  // Grow with the text, up to a limit; scroll inside after that.
  useLayoutEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = "auto";
    const height = Math.min(element.scrollHeight, MAX_HEIGHT_PX);
    element.style.height = `${height}px`;
    element.style.overflowY = element.scrollHeight > MAX_HEIGHT_PX ? "auto" : "hidden";
    if (cursor.current !== null) {
      element.setSelectionRange(cursor.current, cursor.current);
      cursor.current = null;
    }
  }, [text, inputRef]);

  const counter = counterFor(text, maxChars);
  const sendable = !busy && !blocked && canSend(text, maxChars);

  const dictated = (words: string) => {
    const element = inputRef.current;
    const at = element ? { start: element.selectionStart, end: element.selectionEnd } : { start: text.length, end: text.length };
    const placed = insertAtCursor(text, at.start, at.end, words);
    cursor.current = placed.cursor;
    onText(placed.text);
    element?.focus();
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="rounded-xl border border-border bg-surface has-[textarea:focus-visible]:border-accent has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-accent">
        <textarea
          ref={inputRef}
          rows={1}
          value={text}
          onChange={(event) => onText(event.target.value)}
          onKeyDown={(event) => {
            const command = composerCommand(
              { key: event.key, shiftKey: event.shiftKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey, altKey: event.altKey, composing: event.nativeEvent.isComposing, repeat: event.repeat },
              busy,
            );
            if (command === "stop") {
              event.preventDefault();
              if (!stopping) onStop();
            } else if (command === "ignore") {
              event.preventDefault();
            } else if (command === "send") {
              // Enter never adds a line; it sends when there is something to send.
              event.preventDefault();
              if (sendable) onSend();
            }
          }}
          placeholder={placeholder}
          aria-label="Message"
          aria-describedby={counter ? counterId : undefined}
          aria-invalid={counter?.over ? true : undefined}
          className="block max-h-60 w-full resize-none bg-transparent px-3 pb-1 pt-2.5 text-sm text-text outline-none"
        />
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-2 pb-2 pt-1">
          <ModelPicker value={value} onChange={onValue} options={pickerOptions} disabled={pickerDisabled} />
          {/* The switches wrap as one group, so the row stays tidy at any width. */}
          <div role="group" aria-label="How this conversation works" className="flex min-w-0 flex-wrap items-center gap-1.5">
            <Chip name="local" label={LOCAL_LABEL} checked={localOnly && !localToggle.disabled} onChange={flipped("local", onLocalOnly)} disabled={localToggle.disabled || pickerDisabled} title={localToggle.reason} describedBy={localNote} onHover={setHovered} onFocus={setFocused} />
            <Chip name="record" label={ON_LABEL} checked={onRecord} onChange={flipped("record", onOnRecord)} disabled={recordDisabled} describedBy={recordNote} onHover={setHovered} onFocus={setFocused} />
            <Chip name="memory" label={MEMORY_LABEL} checked={useMemory} onChange={flipped("memory", onUseMemory)} disabled={pickerDisabled} describedBy={memoryNoteId} warn={memoryBlocked} onHover={setHovered} onFocus={setFocused} />
            <Chip name="web" label={WEB_LABEL} checked={web && !webToggle.disabled} onChange={flipped("web", onWeb)} disabled={webToggle.disabled || pickerDisabled} describedBy={webNoteId} onHover={setHovered} onFocus={setFocused} />
          </div>
          <span className="ml-auto flex items-center gap-1.5">
            {counter ? (
              <span id={counterId} role="status" className={`font-mono text-[11px] tabular-nums ${counter.over ? "text-danger" : "text-text-faint"}`}>
                {counterText(counter)}
                <span className="sr-only">{counter.over ? " characters: over the limit" : " characters used"}</span>
              </span>
            ) : null}
            <DictateButton onText={dictated} caller="chat" />
            {busy ? (
              <button type="button" className="btn btn-danger !h-8 !px-2.5" onClick={onStop} disabled={stopping} aria-label="Stop the reply" title="Stop (Escape)">
                <Square size={12} fill="currentColor" aria-hidden="true" />
                {stopping ? "Stopping…" : "Stop"}
              </button>
            ) : (
              <button type="button" className="btn btn-primary !h-8 !px-2.5"
                onClick={() => {
                  // Send becomes Stop, a different button: keep the focus in the message box rather than losing it.
                  inputRef.current?.focus();
                  onSend();
                }} disabled={!sendable} aria-label="Send message" title="Send (Enter)">
                <ArrowUp size={14} aria-hidden="true" />
                Send
              </button>
            )}
          </span>
        </div>
      </div>
      {/* One line, for the switch being pointed at, focused or just flipped. Each switch's own
          sentence is always there for a screen reader, which reads it with the switch. */}
      <p className="min-h-4 px-1 text-[11px] text-text-faint" aria-hidden="true">
        {showing ? (
          help[showing]
        ) : memoryBlocked ? (
          <span className="text-warning">{memoryText}</span>
        ) : (
          <>
            {onRecord ? null : "Off the record. "}
            <span className="hidden @md:inline">Enter sends, Shift+Enter starts a new line.</span>
          </>
        )}
      </p>
      {webToggle.kind === "off" || webToggle.kind === "unready" ? (
        <p id={webNoteId} className="px-1 text-[11px] text-text-faint">
          {webToggle.reason}{" "}
          <a href={webSettingsHref} className="text-text-muted underline underline-offset-2 hover:text-text">
            Open Web lookup settings
          </a>
        </p>
      ) : null}
      <span id={localNote} className="sr-only">
        {localText}
      </span>
      <span id={recordNote} className="sr-only">
        {recordText}
      </span>
      <span id={memoryNoteId} className="sr-only">
        {memoryText}
      </span>
      {/* The visible line above is the sentence when the switch cannot be used yet: say it once. */}
      {webToggle.kind === "off" || webToggle.kind === "unready" ? null : (
        <span id={webNoteId} className="sr-only">
          {webText}
        </span>
      )}
    </div>
  );
}
