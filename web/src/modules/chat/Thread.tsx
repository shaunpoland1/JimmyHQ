import { ArrowDown, RotateCw, TriangleAlert } from "lucide-react";
import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { formatEventTime, formatFullTime } from "../../lib/format";
import type { AssistantMessage, StreamStart, UserMessage } from "./api";
import { omittedNote, replyMeta } from "./lib/format";
import { OFF_LABEL, dividerText, threadRows } from "./lib/record";
import { nearBottom } from "./lib/reply";
import { waitNote, waitedSeconds } from "./lib/wait";
import type { ThreadView } from "./lib/reply";
import { CopyButton, Markdown } from "./Markdown";
import { MemoryUsedNote } from "./MemoryUsedNote";
import { SourcesNote } from "./SourcesNote";
import { ActivityLine } from "./ActivityLine";
import type { StepRow } from "./lib/web";

type ThreadProps = {
  /** The open conversation: a reply's memory is read back by its address. */
  conversationId: string;
  view: ThreadView;
  /** There are older messages on the server. */
  hasOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  /** The newest message is the owner's and nothing is answering it. */
  unanswered: boolean;
  /** A new reply to the last message can be asked for right now. */
  canRegenerate: boolean;
  onRegenerate: () => void;
  /** Stop was pressed and the reply is ending. */
  stopping: boolean;
  /** The record switch as it stands: how what is sent next will be kept. */
  onRecord: boolean;
  /** Changes when the owner sends, to go back to following the newest text. */
  follow: number;
  /** What web lookup has done so far in the reply being written. */
  steps: readonly StepRow[];
};

/** The small, quiet mark on a message that is kept off the record. */
function OffMark() {
  return <span className="rounded border border-dashed border-border px-1 font-mono text-[10px] lowercase tracking-wide text-text-faint">{OFF_LABEL}</span>;
}

/** A line across the thread where the record switch was flipped. */
function Divider({ onRecord }: { onRecord: boolean }) {
  return (
    <li role="separator" className="flex items-center gap-2 text-[11px] text-text-faint">
      <span className={`h-px flex-1 ${onRecord ? "bg-border" : "border-t border-dashed border-border"}`} aria-hidden="true" />
      {dividerText(onRecord)}
      <span className={`h-px flex-1 ${onRecord ? "bg-border" : "border-t border-dashed border-border"}`} aria-hidden="true" />
    </li>
  );
}

const UserBubble = memo(function UserBubble({
  text,
  created,
  pending = false,
  onRecord,
}: {
  text: string;
  created: number | null;
  pending?: boolean;
  onRecord: boolean;
}) {
  return (
    <li className="flex flex-col items-end gap-0.5">
      <span className="sr-only">You said:</span>
      {/* The owner's own words: plain text, line breaks kept. */}
      <div
        className={`max-w-[85%] whitespace-pre-wrap break-words rounded-xl border border-border bg-surface-2 px-3 py-2 text-sm text-text [overflow-wrap:anywhere] ${pending ? "opacity-70" : ""}`}
        title={created === null ? undefined : formatFullTime(created)}
      >
        {text}
      </div>
      {pending ? <span className="text-[11px] text-text-faint">Sending…</span> : null}
      {onRecord ? null : <OffMark />}
    </li>
  );
});

function Where({ where }: { where: "local" | "hosted" }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-border px-1 font-mono text-[10px] uppercase tracking-wide text-text-muted">
      <span className={`dot !size-1.5 ${where === "local" ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
      {where}
    </span>
  );
}

const AssistantBlock = memo(function AssistantBlock({
  conversationId,
  message,
  last,
  canRegenerate,
  onRegenerate,
}: {
  conversationId: string;
  message: AssistantMessage;
  last: boolean;
  canRegenerate: boolean;
  onRegenerate: () => void;
}) {
  const meta = replyMeta(message);
  const failed = message.state === "failed";
  return (
    <li className="flex min-w-0 flex-col gap-1.5">
      <span className="sr-only">The model replied:</span>
      {message.text ? (
        <Markdown text={message.text} />
      ) : failed ? null : (
        <p className="text-sm italic text-text-faint">{meta.stopped ? "Stopped before anything was written." : "The model sent back nothing."}</p>
      )}
      {failed ? (
        <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-[13px] text-text">
          <TriangleAlert size={14} className="flex-none text-danger" aria-hidden="true" />
          <p className="min-w-0 flex-1 basis-48 break-words">{message.error ?? "The reply failed."}</p>
          {last && canRegenerate ? (
            <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={onRegenerate}>
              <RotateCw size={12} aria-hidden="true" />
              Retry
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-text-faint">
        {meta.model ? <span className="max-w-full break-all font-mono text-text-muted">{meta.model}</span> : null}
        {meta.where ? <Where where={meta.where} /> : null}
        {meta.stopped ? <span className="rounded border border-border px-1 font-mono text-[10px] uppercase tracking-wide text-warning">stopped</span> : null}
        {message.on_record ? null : <OffMark />}
        {meta.tokens ? <span>{meta.tokens}</span> : null}
        {meta.duration ? <span>{meta.duration}</span> : null}
        <time dateTime={new Date(message.created).toISOString()} title={formatFullTime(message.created)}>
          {formatEventTime(message.created)}
        </time>
        <span className="ml-auto flex items-center gap-0.5">
          {message.text ? <CopyButton label="Copy reply" getText={() => message.text} /> : null}
          {last && canRegenerate && !failed ? (
            <button type="button" className="icon-btn" onClick={onRegenerate} aria-label="Regenerate reply" title="Regenerate">
              <RotateCw size={14} aria-hidden="true" />
            </button>
          ) : null}
        </span>
      </div>
      {/* Product rule: a fallback is always shown. */}
      {meta.fallback ? <p className="break-words text-[11px] text-warning">{meta.fallback}</p> : null}
      {meta.omitted ? <p className="text-[11px] text-text-faint">{meta.omitted}.</p> : null}
      <SourcesNote web={message.web ?? null} />
      <MemoryUsedNote conversationId={conversationId} messageId={message.id} memory={message.memory ?? null} />
    </li>
  );
});

/** Whole seconds since this mounted, ticking once a second while `active`. */
function useWaited(active: boolean): number {
  const [began] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return waitedSeconds(began, now);
}

function WritingBlock({ text, start, stopping, onRecord, steps }: { text: string; start: StreamStart | null; stopping: boolean; onRecord: boolean; steps: readonly StepRow[] }) {
  const waiting = text === "";
  const note = waitNote(useWaited(waiting), { stopping, hasSteps: steps.length > 0 });
  const omitted = start ? omittedNote(start.omitted) : null;
  // Read by destructuring, as the models module does: the public-safe check takes a dotted read of this field for a hostname.
  const { local: startsLocal = false } = start ?? {};
  return (
    <li className="flex min-w-0 flex-col gap-1.5" aria-busy="true">
      <span className="sr-only" role="status">
        {stopping ? "Stopping the reply" : "A reply is being written"}
      </span>
      <ActivityLine rows={steps} answering={text !== ""} stopping={stopping} />
      {text ? <Markdown text={text} /> : null}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-text-faint">
        <span className="dot chat-writing bg-accent" aria-hidden="true" />
        <span>{stopping ? "Stopping…" : text ? "Writing…" : note.label}</span>
        {start ? (
          <>
            <span className="max-w-full break-all font-mono text-text-muted">{start.model}</span>
            <Where where={startsLocal ? "local" : "hosted"} />
          </>
        ) : null}
        {onRecord ? null : <OffMark />}
      </div>
      {waiting && note.hint ? <p className="max-w-prose text-[12px] text-text-muted">{note.hint}</p> : null}
      {omitted ? <p className="text-[11px] text-text-faint">{omitted}.</p> : null}
    </li>
  );
}

/** The messages of the open conversation, following new text unless the owner has scrolled up. */
export function Thread({ conversationId, view, hasOlder, loadingOlder, onLoadOlder, unanswered, canRegenerate, onRegenerate, stopping, onRecord, follow, steps }: ThreadProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  /** Distance from the end to hold when older messages are added above. */
  const hold = useRef<number | null>(null);
  const [away, setAway] = useState(false);

  const toEnd = () => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
    pinned.current = true;
    setAway(false);
  };

  const firstId = view.messages[0]?.id;
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && hold.current !== null) element.scrollTop = element.scrollHeight - hold.current;
    hold.current = null;
  }, [firstId]);

  const writingText = view.writing?.text;
  const writing = view.writing !== null;
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  }, [view.messages, view.pendingText, writingText, writing, unanswered]);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
    pinned.current = true;
    setAway(false);
  }, [follow]);

  // The composer growing, the window narrowing or a wider line wrapping changes the
  // thread's box without any new message: stay at the end if that is where the owner was.
  useEffect(() => {
    const element = scroller.current;
    const content = element?.firstElementChild;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (pinned.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    if (content) observer.observe(content);
    return () => observer.disconnect();
  }, []);

  const lastId = view.messages[view.messages.length - 1]?.id;
  const rows = threadRows(view.messages, !hasOlder, onRecord);
  const settled = !writing && view.pendingText === null;

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scroller}
        tabIndex={0}
        role="log"
        aria-label="Messages"
        aria-live="off"
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden outline-none focus-visible:outline-2 focus-visible:-outline-offset-2"
        onScroll={(event) => {
          const element = event.currentTarget;
          const near = nearBottom(element.scrollTop, element.clientHeight, element.scrollHeight);
          pinned.current = near;
          setAway(!near);
        }}
      >
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-4">
          {hasOlder ? (
            <button
              type="button"
              className="btn mx-auto !h-7 !px-2.5 !text-xs"
              disabled={loadingOlder}
              onClick={() => {
                const element = scroller.current;
                if (element) hold.current = element.scrollHeight - element.scrollTop;
                onLoadOlder();
              }}
            >
              {loadingOlder ? "Loading…" : "Load older messages"}
            </button>
          ) : null}
          <ol className="flex flex-col gap-4">
            {rows.map((row) => {
              if (row.kind === "divider") return <Divider key={row.key} onRecord={row.onRecord} />;
              const { message } = row;
              return message.role === "user" ? (
                <UserBubble key={message.id} text={(message as UserMessage).text} created={message.created} onRecord={message.on_record} />
              ) : (
                <AssistantBlock key={message.id} conversationId={conversationId} message={message} last={settled && message.id === lastId} canRegenerate={canRegenerate} onRegenerate={onRegenerate} />
              );
            })}
            {view.pendingText !== null ? <UserBubble text={view.pendingText} created={null} pending onRecord={onRecord} /> : null}
            {view.writing ? <WritingBlock text={view.writing.text} start={view.writing.start} stopping={stopping} onRecord={onRecord} steps={steps} /> : null}
          </ol>
          {settled && unanswered ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-[13px] text-text-muted">
              <p className="min-w-0 flex-1 basis-48">This message has no reply yet.</p>
              {canRegenerate ? (
                <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={onRegenerate}>
                  <RotateCw size={12} aria-hidden="true" />
                  Get a reply
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      {away ? (
        <button
          type="button"
          className="btn absolute bottom-2 left-1/2 !h-7 -translate-x-1/2 !rounded-full !px-3 !text-xs shadow-pop"
          onClick={toEnd}
        >
          <ArrowDown size={12} aria-hidden="true" />
          Jump to latest
        </button>
      ) : null}
    </div>
  );
}
