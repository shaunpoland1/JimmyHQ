import { ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { describePick, localityWord, moveHighlight, pickerItems, popupShift, targetKey, typeahead, unsetEffortLabel } from "./lib/picker";
import type { ModelPickerOptions, ModelPickerValue, PickerItem } from "./lib/picker";

export type { ModelPickerOptions, ModelPickerValue, PickerProvider, PickerRole, PickerTarget } from "./lib/picker";

type ModelPickerProps = {
  value: ModelPickerValue;
  onChange: (value: ModelPickerValue) => void;
  options: ModelPickerOptions;
  disabled?: boolean;
  /** Which side the list opens on. A composer sits at the bottom, so the default is above. */
  placement?: "top" | "bottom";
};

const TYPEAHEAD_RESET_MS = 600;
/** The list is `w-72` wide and keeps this far from the window's edges. */
const POPUP_WIDTH_PX = 288;
const POPUP_MARGIN_PX = 12;

function dotTone(isLocal: boolean | null): string {
  return isLocal === null ? "bg-sage" : isLocal ? "bg-success" : "bg-warning";
}

/**
 * A compact control for a composer: pick a role or a specific provider and
 * model, plus an effort, and see whether the choice is local or hosted.
 */
export function ModelPicker({ value, onChange, options, disabled = false, placement = "top" }: ModelPickerProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [shift, setShift] = useState(0);
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: "", at: 0 });

  const items = useMemo(() => pickerItems(options), [options]);
  const currentKey = targetKey(value.target);
  const summary = describePick(value.target, options);
  const optionId = (index: number) => `${id}-option-${index}`;

  const show = () => {
    if (disabled) return;
    const rect = wrapper.current?.getBoundingClientRect();
    setShift(rect ? popupShift(rect.left, window.innerWidth, POPUP_WIDTH_PX, POPUP_MARGIN_PX) : 0);
    setActive(items.findIndex((item) => item.key === currentKey));
    setOpen(true);
  };
  const hide = (refocus: boolean) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const choose = (item: PickerItem) => {
    onChange({ ...value, target: item.target });
    hide(true);
  };

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (open && active >= 0) document.getElementById(`${id}-option-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [open, active, id]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const onListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      hide(true);
      return;
    }
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const item = items[active];
      if (item) choose(item);
      return;
    }
    const moved = moveHighlight(event.key, active, items.length);
    if (moved !== null) {
      event.preventDefault();
      setActive(moved);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      const text = now - typed.current.at > TYPEAHEAD_RESET_MS ? event.key : typed.current.text + event.key;
      typed.current = { text, at: now };
      // A longer run keeps the current match if it still fits; a first letter moves on.
      const found = typeahead(items, text, text.length > 1 ? active - 1 : active);
      if (found !== null) setActive(found);
    }
  };

  const groups: { name: string; entries: { item: PickerItem; index: number }[] }[] = [];
  items.forEach((item, index) => {
    const last = groups[groups.length - 1];
    if (last && last.name === item.group) last.entries.push({ item, index });
    else groups.push({ name: item.group, entries: [{ item, index }] });
  });

  const unsetEffort = unsetEffortLabel(value.target, options);
  const where = localityWord(summary.isLocal);
  const description = `${value.target.kind === "role" ? `${summary.label} role` : summary.label}, ${summary.detail}, ${where}`;

  return (
    <div ref={wrapper} className="relative inline-flex w-[14.75rem] max-w-full items-center gap-1 align-middle">
      <button
        ref={trigger}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-label={`Model: ${description}`}
        title={description}
        onClick={() => (open ? hide(false) : show())}
        onKeyDown={(event) => {
          if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
            event.preventDefault();
            show();
          }
        }}
        className="flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-md border border-border bg-surface px-2 text-xs text-text hover:border-sage"
      >
        <span className={`dot ${dotTone(summary.isLocal)}`} aria-hidden="true" />
        <span className={`min-w-0 flex-1 truncate text-left ${value.target.kind === "model" ? "font-mono" : "font-medium"}`}>{summary.label}</span>
        <span className="flex-none font-mono text-[10px] text-text-faint">{summary.isLocal === null ? "" : where}</span>
        <ChevronDown size={12} className="flex-none text-text-faint" aria-hidden="true" />
      </button>
      {options.efforts.length > 0 ? (
        <select
          aria-label="Effort"
          title={value.effort ? `Effort: ${value.effort}, for this conversation` : unsetEffort === "effort" ? "Effort" : `${unsetEffort}, as set up for this choice. Pick another to change it for this conversation.`}
          disabled={disabled}
          value={value.effort ?? ""}
          onChange={(event) => onChange({ ...value, effort: event.target.value || null })}
          className="h-7 w-[7.5rem] flex-none rounded-md border border-border bg-surface px-1 text-xs text-text hover:border-sage disabled:opacity-55"
        >
          <option value="">{unsetEffort}</option>
          {value.effort && !options.efforts.includes(value.effort) ? <option value={value.effort}>{value.effort}</option> : null}
          {options.efforts.map((effort) => (
            <option key={effort} value={effort}>
              {effort}
            </option>
          ))}
        </select>
      ) : null}
      {open ? (
        <div
          ref={list}
          id={`${id}-list`}
          role="listbox"
          tabIndex={-1}
          aria-label="Choose a role or a model"
          aria-activedescendant={active >= 0 ? optionId(active) : undefined}
          onKeyDown={onListKeyDown}
          style={{ left: shift }}
          className={`absolute z-40 max-h-72 w-72 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-pop ${placement === "top" ? "bottom-full mb-1" : "top-full mt-1"}`}
        >
          {items.length === 0 ? <p className="px-2 py-2 text-xs text-text-faint">No roles or models are set up yet. Add a provider in Settings, Models and roles.</p> : null}
          {groups.map((group) => (
            // Two providers may share a label, so the heading is not a safe key.
            <div key={group.entries[0]?.item.key ?? group.name} role="group" aria-label={group.name}>
              <div className="px-2 pb-0.5 pt-1.5 font-mono text-[10px] uppercase tracking-widest text-text-faint" aria-hidden="true">
                {group.name}
              </div>
              {group.entries.map(({ item, index }) => {
                const selected = item.key === currentKey;
                return (
                  <div
                    key={item.key}
                    id={optionId(index)}
                    role="option"
                    aria-selected={selected}
                    onMouseEnter={() => setActive(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => choose(item)}
                    className={`flex items-center gap-2 rounded-md px-2 py-1 text-xs ${index === active ? "bg-surface-2 text-text" : "text-text-muted"}`}
                  >
                    <span className={`dot ${dotTone(item.isLocal)}`} aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className={`block truncate text-text ${item.target.kind === "model" ? "font-mono" : "font-medium"} ${selected ? "underline decoration-accent underline-offset-2" : ""}`}>{item.label}</span>
                      {item.target.kind === "role" ? <span className="block truncate text-[11px] text-text-faint">{item.detail}</span> : null}
                    </span>
                    <span className="flex-none font-mono text-[10px] text-text-faint">{item.isLocal === null ? "" : localityWord(item.isLocal)}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
