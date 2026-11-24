import { ChevronLeft, ChevronRight } from "lucide-react";
import type { DescribedRecord, Description, ViewSpec } from "./api";
import type { Records } from "./lib/pages";
import { bucketByDay, dayOfMonth, inMonth, longDay, monthGrid, monthTitle, shiftMonth, weekdayNames } from "./lib/calendar";
import type { Month } from "./lib/calendar";
import { calendarField } from "./lib/layout";
import { fieldLabel } from "./lib/schema";

type Props = {
  description: Description;
  view: ViewSpec;
  data: Records;
  month: Month;
  weekStart: number;
  /** Today in the owner's time zone, "YYYY-MM-DD". */
  today: string;
  locale?: string;
  onMonth: (month: Month) => void;
  onOpen: (record: DescribedRecord) => void;
  onAdd: (field: string, day: string) => void;
};

/** A month by one date field. Days are as written; "today" and the week's first day are the owner's. */
export function CalendarLayout({ description, view, data, month, weekStart, today, locale, onMonth, onOpen, onAdd }: Props) {
  const field = calendarField(description, view);
  const spec = field ? description.tables[view.table]?.fields[field] : undefined;
  if (!field || !spec) return null;
  const weeks = monthGrid(month, weekStart);
  const { days } = bucketByDay(data.records, field);
  const names = weekdayNames(weekStart, locale);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold" aria-live="polite">
          {monthTitle(month, locale)} <span className="font-normal text-text-faint">by {fieldLabel(field, spec).toLowerCase()}</span>
        </h3>
        <div className="flex items-center gap-1">
          <button type="button" className="icon-btn" aria-label="Previous month" onClick={() => onMonth(shiftMonth(month, -1))}>
            <ChevronLeft size={16} />
          </button>
          <button type="button" className="btn h-7 px-2 text-xs" onClick={() => onMonth({ year: Number(today.slice(0, 4)), month: Number(today.slice(5, 7)) })}>
            Today
          </button>
          <button type="button" className="icon-btn" aria-label="Next month" onClick={() => onMonth(shiftMonth(month, 1))}>
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
      <div role="grid" aria-label={monthTitle(month, locale)} className="overflow-hidden rounded-lg border border-border bg-surface">
        <div role="row" className="grid grid-cols-7 border-b border-border bg-surface-2 text-center text-xs text-text-muted">
          {names.map((name) => (
            <div key={name} role="columnheader" className="py-1.5 font-medium">
              {name}
            </div>
          ))}
        </div>
        {weeks.map((week) => (
          <div key={week[0]} role="row" className="grid grid-cols-7 border-b border-border last:border-b-0">
            {week.map((day) => {
              const records = days.get(day) ?? [];
              const outside = !inMonth(day, month);
              return (
                <div key={day} role="gridcell" aria-label={longDay(day, locale)} className={`group relative flex min-h-16 min-w-0 flex-col gap-0.5 border-r border-border p-1 last:border-r-0 sm:min-h-24 ${outside ? "bg-surface-2/50 text-text-faint" : ""}`}>
                  <div className="flex items-center justify-between">
                    <span className={`grid size-5 place-items-center rounded-full text-[11px] ${day === today ? "bg-accent font-semibold text-accent-fg" : ""}`} aria-current={day === today ? "date" : undefined}>
                      {dayOfMonth(day)}
                    </span>
                    <button type="button" className="icon-btn size-5 opacity-0 focus-visible:opacity-100 group-hover:opacity-100" aria-label={`Add on ${longDay(day, locale)}`} onClick={() => onAdd(field, day)}>
                      <span aria-hidden="true">+</span>
                    </button>
                  </div>
                  {records.map((record) => (
                    <button key={record.id} type="button" onClick={() => onOpen(record)} title={record.label} className="truncate rounded bg-accent/15 px-1 py-px text-left text-[11px] text-text hover:bg-accent/25">
                      {record.label}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
