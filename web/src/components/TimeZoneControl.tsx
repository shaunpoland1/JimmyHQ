import { Check, ChevronDown, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { deviceTimeZone, filterTimeZones, formatOffset, offsetMinutes, timeZoneNames, timeZoneOptions, zoneName } from "../lib/timeZones";
import type { TimeZoneOption } from "../lib/timeZones";

type TimeZoneControlProps = {
  id: string;
  /** An IANA name such as `Europe/Paris`, or empty for "not chosen". */
  value: string;
  describedBy?: string;
  onSave: (value: string) => void;
  /**
   * What an empty value means, in words. It is the first choice in the list.
   * The button is named by an element with the id `<id>-label`, if there is one.
   */
  emptyLabel?: string;
};

const EMPTY: TimeZoneOption = { zone: "", name: "", offset: "" };

/** Pick a time zone from a list you can filter by typing. Each name shows its offset from UTC right now. */
export function TimeZoneControl({ id, value, describedBy, onSave, emptyLabel = "Same as the computer JimmyHQ runs on" }: TimeZoneControlProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const listbox = useRef<HTMLDivElement>(null);
  const device = useMemo(() => deviceTimeZone(), []);

  // Offsets are worked out when the list opens, so they are right for today.
  const options = useMemo(() => (open ? timeZoneOptions(timeZoneNames([value, device]), new Date()) : []), [open, value, device]);
  const shown = useMemo(() => {
    const matches = filterTimeZones(options, query);
    return query.trim() ? matches : [EMPTY, ...matches];
  }, [options, query]);

  const currentOffset = useMemo(() => {
    if (!value) return "";
    const minutes = offsetMinutes(value, new Date());
    return minutes === null ? "" : formatOffset(minutes);
  }, [value]);

  const show = () => {
    setQuery("");
    setOpen(true);
  };
  const hide = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const choose = (zone: string) => {
    if (zone !== value) onSave(zone);
    hide();
  };

  // On opening, start at the current choice and put the cursor in the filter.
  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const at = options.findIndex((option) => option.zone === value);
    setActive(value && at >= 0 ? at + 1 : 0);
    // Only when the list opens: typing must not jump back to the stored value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    listbox.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active, shown]);

  const optionId = (index: number) => `${id}-option-${index}`;
  const onSearchKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      hide();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (shown.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + shown.length) % shown.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const option = shown[active];
      if (option) choose(option.zone);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          ref={trigger}
          id={id}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-labelledby={`${id}-label ${id}-value`}
          aria-describedby={describedBy}
          onClick={() => (open ? hide() : show())}
          className="input flex w-auto min-w-[16rem] max-w-full flex-1 items-center gap-2 text-left @md:flex-none"
        >
          <span id={`${id}-value`} className="min-w-0 flex-1 truncate">
            {value ? zoneName(value) : emptyLabel}
          </span>
          {currentOffset ? <span className="flex-none font-mono text-[11px] text-text-faint">{currentOffset}</span> : null}
          <ChevronDown size={14} className="flex-none text-text-faint" aria-hidden="true" />
        </button>
        {device && device !== value ? (
          <button type="button" className="btn" onClick={() => onSave(device)} title={zoneName(device)}>
            Use this device's time zone
          </button>
        ) : null}
      </div>
      {open ? (
        <div className="max-w-md rounded-lg border border-border bg-surface shadow-pop">
          <div className="flex items-center gap-2 border-b border-border px-2.5 py-1.5">
            <Search size={14} className="flex-none text-text-faint" aria-hidden="true" />
            <input
              ref={search}
              type="text"
              role="combobox"
              aria-expanded="true"
              aria-controls={`${id}-list`}
              aria-activedescendant={shown.length > 0 ? optionId(active) : undefined}
              aria-autocomplete="list"
              aria-label="Find a time zone by city, region or offset"
              placeholder="Type a city, a region or an offset such as +2"
              autoComplete="off"
              spellCheck={false}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(0);
              }}
              onKeyDown={onSearchKey}
              className="min-w-0 flex-1 bg-transparent py-1 text-[13px] text-text outline-none placeholder:text-text-faint"
            />
          </div>
          <div ref={listbox} id={`${id}-list`} role="listbox" aria-label="Time zones" className="max-h-64 overflow-y-auto p-1">
            {shown.length === 0 ? <p className="px-2 py-2 text-xs text-text-faint">No time zone matches that. Try a nearby large city.</p> : null}
            {shown.map((option, index) => {
              const selected = option.zone === value;
              return (
                <div
                  key={option.zone || "(empty)"}
                  id={optionId(index)}
                  data-index={index}
                  role="option"
                  aria-selected={selected}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => choose(option.zone)}
                  className={`flex cursor-default items-center gap-2 rounded-md px-2 py-1 text-[13px] ${index === active ? "bg-surface-2 text-text" : "text-text-muted"}`}
                >
                  <span className="grid w-3.5 flex-none place-items-center text-accent">{selected ? <Check size={13} aria-hidden="true" /> : null}</span>
                  <span className="min-w-0 flex-1 truncate">{option.zone ? option.name : emptyLabel}</span>
                  {option.offset ? <span className="flex-none font-mono text-[11px] text-text-faint">{option.offset}</span> : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
