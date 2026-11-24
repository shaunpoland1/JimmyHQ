import { MoreHorizontal, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { Conversation } from "./api";
import { BUSY_NOTE, DELETE_FOR_GOOD, DELETE_NOTE, DELETE_OFF_RECORD_ONLY } from "./lib/delete";
import { hasOffRecord } from "./lib/record";

type ConversationMenuProps = {
  conversation: Conversation;
  /** A reply is being written, so nothing can be deleted just now. */
  busy: boolean;
  onDeleteOffRecord: () => void;
  onDeleteForGood: () => void;
};

const ITEM_CLASS =
  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-danger hover:bg-surface-2 disabled:cursor-not-allowed disabled:text-text-faint disabled:hover:bg-transparent";

/** The open conversation's menu: deleting it for good, or only the part that is off the record. */
export function ConversationMenu({ conversation, busy, onDeleteOffRecord, onDeleteForGood }: ConversationMenuProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const item = useRef<HTMLButtonElement>(null);
  const note = useId();
  // For one that was never on the record the two would do the same thing, so only the first is offered.
  const partly = conversation.recorded && hasOffRecord(conversation);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    requestAnimationFrame(() => (item.current?.disabled ? root.current?.querySelector<HTMLElement>('[role="menu"]')?.focus() : item.current?.focus()));
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) requestAnimationFrame(() => opener.current?.focus());
  };

  return (
    <div ref={root} className="relative flex-none">
      <button
        ref={opener}
        type="button"
        className="icon-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More for this conversation"
        title="More"
        onClick={() => setOpen((was) => !was)}
      >
        <MoreHorizontal size={14} aria-hidden="true" />
      </button>
      {open ? (
        <div
          role="menu"
          tabIndex={-1}
          aria-label="More for this conversation"
          className="absolute right-0 top-full z-20 mt-1 flex w-72 max-w-[calc(100vw-2rem)] flex-col gap-1 rounded-lg border border-border bg-surface p-1.5 shadow-pop outline-none"
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            close(true);
          }}
        >
          <button
            ref={item}
            type="button"
            role="menuitem"
            className={ITEM_CLASS}
            disabled={busy}
            aria-describedby={note}
            onClick={() => {
              close(false);
              onDeleteForGood();
            }}
          >
            <Trash2 size={14} className="flex-none" aria-hidden="true" />
            {DELETE_FOR_GOOD}
          </button>
          {partly ? (
            <button
              type="button"
              role="menuitem"
              className={ITEM_CLASS}
              disabled={busy}
              onClick={() => {
                close(false);
                onDeleteOffRecord();
              }}
            >
              <Trash2 size={14} className="flex-none" aria-hidden="true" />
              {DELETE_OFF_RECORD_ONLY}
            </button>
          ) : null}
          <p id={note} className="px-2 pb-1 text-[11px] leading-snug text-text-faint">
            {busy ? BUSY_NOTE : DELETE_NOTE}
          </p>
        </div>
      ) : null}
    </div>
  );
}
