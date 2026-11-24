import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { useMediaQuery } from "../../../hooks/useMediaQuery";
import type { DescribedRecord, Description, ViewSpec } from "./api";
import { Cell } from "./Cell";
import type { Records } from "./lib/pages";
import { groupRecords } from "./lib/layout";
import { ariaSort, directionOf } from "./lib/sort";
import { fieldLabel, viewFields } from "./lib/schema";
import { displayTotal, totalWord } from "./lib/values";

type Props = {
  description: Description;
  view: ViewSpec;
  data: Records;
  sort: string[] | undefined;
  onSort: (field: string) => void;
  onOpen: (record: DescribedRecord) => void;
};

/** The records as a table with sortable headings, or as stacked cards on a narrow screen. */
export function TableLayout({ description, view, data, sort, onSort, onOpen }: Props) {
  const narrow = useMediaQuery("(max-width: 639px)");
  const table = description.tables[view.table];
  if (!table) return null;
  const fields = viewFields(description, view);
  const groups = view.group ? groupRecords(table, view.group, data.records, data.groups) : [{ value: null, label: "", records: data.records, count: data.records.length }];
  const grouped = Boolean(view.group);
  const totals = view.totals ? fields.filter((f) => view.totals?.[f]) : [];

  if (narrow) {
    return (
      <div className="flex flex-col gap-4">
        {groups.map((group) => (
          <section key={String(group.value)} aria-label={grouped ? group.label : undefined} className="flex flex-col gap-2">
            {grouped ? <GroupHeading label={group.label} count={group.count} /> : null}
            <ul className="flex flex-col gap-2">
              {group.records.map((record) => (
                <li key={record.id} className="rounded-lg border border-border bg-surface">
                  <button type="button" onClick={() => onOpen(record)} className="flex w-full flex-col gap-1.5 p-3 text-left" aria-label={`Open ${record.label}`}>
                    <span className="text-sm font-medium">{record.label}</span>
                    <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
                      {fields.map((name) => {
                        const spec = table.fields[name];
                        if (!spec) return null;
                        return (
                          <div key={name} className="contents">
                            <dt className="text-text-faint">{fieldLabel(name, spec)}</dt>
                            <dd className="min-w-0">
                              <Cell field={name} spec={spec} value={record.values[name]} labels={data.labels} />
                            </dd>
                          </div>
                        );
                      })}
                    </dl>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {totals.length ? <Totals description={description} view={view} data={data} fields={totals} /> : null}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full min-w-max border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-surface-2 text-left text-xs text-text-muted">
            {fields.map((name) => {
              const spec = table.fields[name];
              if (!spec) return null;
              const direction = directionOf(sort, name);
              return (
                <th key={name} scope="col" aria-sort={ariaSort(direction)} className="px-3 py-2 font-medium">
                  <button type="button" onClick={() => onSort(name)} className="inline-flex items-center gap-1 hover:text-text">
                    {fieldLabel(name, spec)}
                    {direction === "asc" ? <ArrowUp size={12} aria-hidden="true" /> : direction === "desc" ? <ArrowDown size={12} aria-hidden="true" /> : <ChevronsUpDown size={12} className="opacity-40" aria-hidden="true" />}
                    <span className="sr-only">{direction === "asc" ? ", sorted ascending" : direction === "desc" ? ", sorted descending" : ", sort"}</span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        {groups.map((group) => (
          <tbody key={String(group.value)}>
            {grouped ? (
              <tr className="border-b border-border bg-surface-2/60">
                <th scope="colgroup" colSpan={fields.length} className="px-3 py-1.5 text-left">
                  <GroupHeading label={group.label} count={group.count} />
                </th>
              </tr>
            ) : null}
            {group.records.map((record) => (
              <tr key={record.id} className="border-b border-border last:border-b-0 hover:bg-surface-2/50">
                {fields.map((name, column) => {
                  const spec = table.fields[name];
                  if (!spec) return null;
                  const cell = <Cell field={name} spec={spec} value={record.values[name]} labels={data.labels} />;
                  return (
                    <td key={name} className="max-w-[22rem] px-3 py-2 align-top">
                      {column === 0 ? (
                        <button type="button" onClick={() => onOpen(record)} className="block max-w-full text-left font-medium text-accent hover:underline" aria-label={`Open ${record.label}`}>
                          {cell}
                        </button>
                      ) : (
                        cell
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        ))}
        {totals.length ? (
          <tfoot>
            <tr className="border-t border-border bg-surface-2 text-xs">
              {fields.map((name, column) => {
                const kind = view.totals?.[name];
                const spec = table.fields[name];
                return (
                  <td key={name} className="px-3 py-2 align-top tabular-nums text-text-muted">
                    {kind && spec ? (
                      <span>
                        <span className="text-text-faint">{totalWord(kind, spec)} </span>
                        <span className="font-medium text-text">{displayTotal(kind, spec, data.totals?.[name]) || "–"}</span>
                      </span>
                    ) : column === 0 ? (
                      <span className="text-text-faint">All {data.total}</span>
                    ) : null}
                  </td>
                );
              })}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

function GroupHeading({ label, count }: { label: string; count: number }) {
  return (
    <span className="text-xs font-semibold text-text">
      {label} <span className="font-normal text-text-faint">{count}</span>
    </span>
  );
}

function Totals({ description, view, data, fields }: { description: Description; view: ViewSpec; data: Records; fields: string[] }) {
  const table = description.tables[view.table];
  return (
    <dl className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-surface-2 p-3 text-xs">
      {fields.map((name) => {
        const kind = view.totals?.[name];
        const spec = table?.fields[name];
        if (!kind || !spec) return null;
        return (
          <div key={name}>
            <dt className="text-text-faint">{`${totalWord(kind, spec)} ${fieldLabel(name, spec).toLowerCase()}`}</dt>
            <dd className="font-medium tabular-nums">{displayTotal(kind, spec, data.totals?.[name]) || "–"}</dd>
          </div>
        );
      })}
    </dl>
  );
}
