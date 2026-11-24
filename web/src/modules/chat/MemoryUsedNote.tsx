import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import { ApiError, errorDetail } from "../../api";
import { chatApi } from "./api";
import type { MemoryUsed } from "./api";
import { memoryKey } from "./keys";
import { memoryNote, noteHref } from "./lib/memory";

type MemoryUsedNoteProps = { conversationId: string; messageId: string; memory: MemoryUsed | null };

/**
 * Under a reply: which memories it was given, or why none were. The reply
 * keeps only where its passages are; their words are fetched when the list is
 * opened, as they stand now, and are not kept once it is closed.
 */
export function MemoryUsedNote({ conversationId, messageId, memory }: MemoryUsedNoteProps) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const note = memoryNote(memory);
  const passages = useQuery({
    queryKey: memoryKey(conversationId, messageId),
    queryFn: () => chatApi.memoryPassages(conversationId, messageId),
    enabled: open && note?.kind === "used",
    staleTime: 0,
    gcTime: 0,
    retry: (count, error) => !(error instanceof ApiError) && count < 1,
  });
  if (!note) return null;
  if (note.kind === "skipped") return <p className="break-words text-[11px] text-text-faint">{note.reason}</p>;
  return (
    <div className="flex min-w-0 flex-col gap-1 text-[11px] text-text-faint">
      <button
        type="button"
        className="-ml-1 inline-flex w-fit items-center gap-0.5 rounded px-1 py-0.5 text-text-faint hover:text-text-muted"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((was) => !was)}
      >
        <ChevronRight size={12} className={`transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
        {note.label}
      </button>
      {open ? (
        <div id={listId} className="flex min-w-0 flex-col gap-1.5 border-l border-border pl-3">
          {passages.isPending ? <p role="status">Reading memory…</p> : null}
          {passages.isError ? (
            <p role="alert" className="break-words text-danger">
              {errorDetail(passages.error)}{" "}
              <button type="button" className="underline underline-offset-2" onClick={() => void passages.refetch()}>
                Try again
              </button>
            </p>
          ) : null}
          {passages.data ? (
            <ul className="flex min-w-0 flex-col gap-1.5">
              {passages.data.passages.map((passage) => (
                <li key={passage.id} className="flex min-w-0 flex-col gap-0.5">
                  <span className="flex min-w-0 flex-wrap items-center gap-x-2">
                    <a href={noteHref(passage.note)} className="min-w-0 break-all font-mono text-text-muted underline underline-offset-2 hover:text-text">
                      {passage.note}
                    </a>
                    {passage.forgotten ? (
                      <span className="rounded border border-dashed border-border px-1 font-mono text-[10px] lowercase tracking-wide">forgotten since</span>
                    ) : null}
                  </span>
                  {passage.forgotten ? (
                    <span className="italic">This is no longer in memory, so its words are not shown.</span>
                  ) : (
                    <span className="whitespace-pre-wrap break-words text-[12px] text-text-muted [overflow-wrap:anywhere]">{passage.text}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : null}
          {note.detail ? <p>{note.detail}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
