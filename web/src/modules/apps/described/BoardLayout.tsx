import { useId } from "react";
import type { DescribedRecord, Description, Value, ViewSpec } from "./api";
import { Cell } from "./Cell";
import type { Records } from "./lib/pages";
import { boardField, cardFields, groupRecords } from "./lib/layout";
import { fieldLabel } from "./lib/schema";
import { displayTotal, totalWord } from "./lib/values";

type Props = {
  description: Description;
  view: ViewSpec;
  data: Records;
  onOpen: (record: DescribedRecord) => void;
  /** Move a card to another column: the group field's new value. */
  onMove: (record: DescribedRecord, field: string, to: Value) => void;
  onAdd: (field: string, to: Value) => void;
};

/** Cards in columns, one for each choice of the view's group field. A card moves with its own drop-down. */
export function BoardLayout({ description, view, data, onOpen, onMove, onAdd }: Props) {
  const field = boardField(description, view);
  const table = description.tables[view.table];
  if (!field || !table) return null;
  const spec = table.fields[field];
  const columns = groupRecords(table, field, data.records, data.groups);
  const shown = cardFields(description, view, [field]);
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:overflow-x-auto sm:pb-2">
      {columns.map((column) => (
        <section key={String(column.value)} aria-label={column.label} className="flex min-w-0 flex-1 flex-col gap-2 rounded-lg border border-border bg-surface-2/60 p-2 sm:w-72 sm:min-w-[16rem] sm:flex-none">
          <header className="flex items-center justify-between gap-2 px-1">
            <h3 className="truncate text-xs font-semibold">
              {column.label} <span className="font-normal text-text-faint">{column.count}</span>
            </h3>
            <button type="button" className="icon-btn" aria-label={`Add to ${column.label}`} onClick={() => onAdd(field, column.value)}>
              <span aria-hidden="true">+</span>
            </button>
          </header>
          {column.totals && view.totals
            ? Object.entries(view.totals).map(([name, kind]) => {
                const totalSpec = table.fields[name];
                const text = totalSpec ? displayTotal(kind, totalSpec, column.totals?.[name]) : "";
                return text && totalSpec ? (
                  <p key={name} className="px-1 text-xs text-text-faint">
                    {totalWord(kind, totalSpec)} {fieldLabel(name, totalSpec).toLowerCase()}: <span className="tabular-nums text-text-muted">{text}</span>
                  </p>
                ) : null;
              })
            : null}
          {column.records.length === 0 ? <p className="px-1 py-3 text-center text-xs text-text-faint">Nothing here.</p> : null}
          <ul className="flex flex-col gap-2">
            {column.records.map((record) => (
              <li key={record.id}>
                <Card record={record} description={description} view={view} data={data} shown={shown} field={field} options={spec?.options ?? []} required={spec?.required === true} onOpen={onOpen} onMove={onMove} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

type CardProps = {
  record: DescribedRecord;
  description: Description;
  view: ViewSpec;
  data: Records;
  shown: string[];
  field: string;
  options: string[];
  required: boolean;
  onOpen: (record: DescribedRecord) => void;
  onMove: (record: DescribedRecord, field: string, to: Value) => void;
};

function Card({ record, description, view, data, shown, field, options, required, onOpen, onMove }: CardProps) {
  const table = description.tables[view.table];
  const id = useId();
  const current = record.values[field] ?? "";
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-2.5">
      <button type="button" onClick={() => onOpen(record)} className="text-left text-sm font-medium hover:underline" aria-label={`Open ${record.label}`}>
        {record.label}
      </button>
      {shown.length ? (
        <dl className="flex flex-col gap-1 text-xs">
          {shown.map((name) => {
            const spec = table?.fields[name];
            const value = record.values[name];
            if (!spec || value === null || value === undefined) return null;
            return (
              <div key={name} className="flex min-w-0 items-baseline gap-2">
                <dt className="flex-none text-text-faint">{fieldLabel(name, spec)}</dt>
                <dd className="min-w-0">
                  <Cell field={name} spec={spec} value={value} labels={data.labels} />
                </dd>
              </div>
            );
          })}
        </dl>
      ) : null}
      <div className="flex items-center gap-2">
        <label htmlFor={id} className="text-xs text-text-faint">
          Move to
        </label>
        <select id={id} className="input h-7 min-w-0 flex-1 text-xs" value={String(current)} onChange={(e) => onMove(record, field, e.target.value === "" ? null : e.target.value)}>
          {required ? null : <option value="">None</option>}
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
