import { GripVertical, Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { addItem, dropIndex, moveAnnouncement, moveItem, removeItem } from "../lib/settings";
import type { DropSide } from "../lib/settings";

type ListControlProps = {
  id: string;
  label: string;
  items: string[];
  describedBy?: string;
  onSave: (items: string[]) => void;
  /** When given, items are picked from these instead of typed. */
  options?: { value: string; label: string }[];
  /** No value twice: an option already in the list is not offered again. */
  unique?: boolean;
};

type Drag = { from: number; over: number | null; side: DropSide };

/**
 * A list of strings you can reorder, add to and remove from. Drag an item by
 * its grip, or focus the grip and press the up and down arrow keys.
 */
export function ListControl({ id, label, items, describedBy, onSave, options, unique = false }: ListControlProps) {
  const [draft, setDraft] = useState("");
  const [drag, setDrag] = useState<Drag | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const list = useRef<HTMLOListElement>(null);
  // After a keyboard move the grip of the moved item takes focus again, at its new place.
  const refocus = useRef<number | null>(null);
  const labels = new Map((options ?? []).map((option) => [option.value, option.label]));
  const offered = (options ?? []).filter((option) => !unique || !items.includes(option.value));
  const nameOf = (item: string) => labels.get(item) ?? item;
  const single = items.length < 2;

  const add = () => {
    const next = addItem(items, draft);
    setDraft("");
    if (next.length !== items.length) onSave(next);
  };

  const move = (from: number, to: number) => {
    const item = items[from];
    if (item === undefined || to < 0 || to >= items.length || to === from) return;
    onSave(moveItem(items, from, to));
    setAnnouncement(moveAnnouncement(nameOf(item), to, items.length));
  };

  useEffect(() => {
    const index = refocus.current;
    if (index === null) return;
    refocus.current = null;
    list.current?.querySelector<HTMLElement>(`[data-list-grip="${index}"]`)?.focus();
  }, [items]);

  // ---- drag to reorder (pointer); the keyboard uses the arrow keys on the grip ----
  const onGripDown = (event: ReactPointerEvent<HTMLElement>, index: number) => {
    if (event.button !== 0 || single) return;
    event.preventDefault();
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ from: index, over: null, side: "before" });
  };
  const onGripMove = (event: ReactPointerEvent<HTMLElement>) => {
    if (!drag) return;
    const row = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-list-row]");
    if (!row || !list.current?.contains(row)) {
      if (drag.over !== null) setDrag({ ...drag, over: null });
      return;
    }
    const box = row.getBoundingClientRect();
    const side: DropSide = event.clientY < box.top + box.height / 2 ? "before" : "after";
    const over = Number(row.dataset.listRow);
    if (over !== drag.over || side !== drag.side) setDrag({ ...drag, over, side });
  };
  const release = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const onGripUp = (event: ReactPointerEvent<HTMLElement>) => {
    release(event);
    if (!drag) return;
    setDrag(null);
    if (drag.over === null) return;
    const to = dropIndex(items.length, drag.from, drag.over, drag.side);
    if (to !== null) {
      refocus.current = to;
      move(drag.from, to);
    }
  };
  const onGripCancel = (event: ReactPointerEvent<HTMLElement>) => {
    release(event);
    setDrag(null);
  };
  const onGripKey = (event: ReactKeyboardEvent<HTMLElement>, index: number) => {
    if (event.key === "Escape" && drag) {
      event.stopPropagation();
      setDrag(null);
      return;
    }
    const to = event.key === "ArrowUp" ? index - 1 : event.key === "ArrowDown" ? index + 1 : event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : null;
    if (to === null) return;
    event.preventDefault();
    if (to < 0 || to >= items.length || to === index) return;
    refocus.current = to;
    move(index, to);
  };

  // Where the line goes: only where dropping would really move the item.
  const lineAt = drag && drag.over !== null && dropIndex(items.length, drag.from, drag.over, drag.side) !== null ? drag : null;
  // The same text twice (a free list may hold it) still needs its own key.
  const seen = new Map<string, number>();

  return (
    <div className="flex flex-col gap-2">
      {items.length === 0 ? (
        <p className="text-xs text-text-faint">Nothing in this list.</p>
      ) : (
        <ol ref={list} aria-label={label} className="flex flex-col divide-y divide-border rounded-lg border border-border bg-surface">
          {items.map((item, index) => {
            const count = seen.get(item) ?? 0;
            seen.set(item, count + 1);
            const name = nameOf(item);
            return (
              <li
                key={`${item}\u0000${count}`}
                data-list-row={index}
                data-dragging={drag?.from === index || undefined}
                data-drop={lineAt?.over === index ? lineAt.side : undefined}
                className="group flex items-center gap-1 py-1 pl-1 pr-1 data-[dragging=true]:opacity-50 data-[drop=after]:shadow-[inset_0_-2px_0_var(--accent)] data-[drop=before]:shadow-[inset_0_2px_0_var(--accent)]"
              >
                <button
                  type="button"
                  data-list-grip={index}
                  disabled={single}
                  aria-label={`Reorder ${name}, position ${index + 1} of ${items.length}. Press the up or down arrow key to move it.`}
                  title={single ? undefined : "Drag to reorder, or press the up and down arrow keys"}
                  onPointerDown={(event) => onGripDown(event, index)}
                  onPointerMove={onGripMove}
                  onPointerUp={onGripUp}
                  onPointerCancel={onGripCancel}
                  onKeyDown={(event) => onGripKey(event, index)}
                  className="grid h-7 w-6 flex-none cursor-grab touch-none place-items-center rounded text-text-faint hover:text-text active:cursor-grabbing disabled:cursor-default disabled:opacity-40"
                >
                  <GripVertical size={14} aria-hidden="true" />
                </button>
                <span className="w-5 flex-none font-mono text-[11px] text-text-faint" aria-hidden="true">
                  {index + 1}
                </span>
                <span className="min-w-0 flex-1 break-words text-[13px]">{name}</span>
                <button type="button" className="icon-btn" onClick={() => onSave(removeItem(items, index))} aria-label={`Remove ${name}`}>
                  <X size={14} aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ol>
      )}
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      {options ? (
        offered.length === 0 ? null : (
          <select
            id={id}
            className="input w-auto max-w-full pr-8"
            value=""
            aria-label={`Add to ${label}`}
            aria-describedby={describedBy}
            onChange={(event) => {
              if (event.target.value) onSave([...items, event.target.value]);
            }}
          >
            <option value="">Add…</option>
            {offered.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        )
      ) : (
        <div className="flex gap-2">
          <input
            id={id}
            type="text"
            className="input"
            value={draft}
            placeholder="Add an item"
            aria-describedby={describedBy}
            autoComplete="off"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
          />
          <button type="button" className="btn" onClick={add} disabled={!draft.trim()}>
            <Plus size={14} aria-hidden="true" />
            Add
          </button>
        </div>
      )}
    </div>
  );
}
