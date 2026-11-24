import { useId } from "react";
import type { ArgMode } from "./api";
import { MODE_LABELS, kindLabel, modeSentence, modesFor, notPlain } from "./lib/args";
import type { ArgDraft, ArgRow, ArgsDraft } from "./lib/args";
import { AreaField, Tag } from "./ui";

type Props = {
  rows: ArgRow[];
  value: ArgsDraft;
  errors: Record<string, string> | undefined;
  onChange: (next: ArgsDraft) => void;
};

/** Each argument of an action: the agent fills it in, it always has one value, or it is not used. */
export function ArgumentsEditor({ rows, value, errors, onChange }: Props) {
  if (rows.length === 0) return <p className="text-xs text-text-muted">This action takes no arguments.</p>;
  const set = (name: string, patch: Partial<ArgDraft>) => onChange({ ...value, [name]: { ...(value[name] ?? { mode: "hidden", text: "" }), ...patch } });
  return (
    <ul className="flex flex-col gap-2.5">
      {rows.map((row) => (
        <ArgumentRow key={row.name} row={row} entry={value[row.name] ?? { mode: row.required ? "open" : "hidden", text: "" }} error={errors?.[row.name]} onChange={(patch) => set(row.name, patch)} />
      ))}
    </ul>
  );
}

function ArgumentRow({ row, entry, error, onChange }: { row: ArgRow; entry: ArgDraft; error: string | undefined; onChange: (patch: Partial<ArgDraft>) => void }) {
  const id = useId();
  const plain = notPlain(row) === null;
  const described = `${id}-about${error ? ` ${id}-error` : ""}`;
  return (
    <li className="rounded-lg border border-border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-xs font-semibold text-text">{row.name}</span>
        {row.required ? <Tag title="The action cannot work without it">required</Tag> : null}
        <span className="text-xs text-text-faint">{kindLabel(row.kind)}</span>
      </div>
      {row.description ? <p className="mt-0.5 break-words text-xs text-text-muted">{row.description}</p> : null}
      <div className="mt-2 grid grid-cols-1 gap-2 @md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-mode`} className="sr-only">
            How {row.name} is set
          </label>
          <select id={`${id}-mode`} className="input pr-8" value={entry.mode} onChange={(event) => onChange({ mode: event.target.value as ArgMode })} aria-describedby={described}>
            {modesFor(row).map((mode) => (
              <option key={mode} value={mode} disabled={mode === "open" && !plain && entry.mode !== "open"}>
                {MODE_LABELS[mode]}
              </option>
            ))}
          </select>
        </div>
        {entry.mode === "fixed" ? <FixedValue row={row} id={id} text={entry.text} error={error} onChange={(text) => onChange({ text })} /> : <p id={`${id}-about`} className="self-center text-xs text-text-faint">{modeSentence(entry.mode)}</p>}
      </div>
      {entry.mode === "fixed" ? (
        <p id={`${id}-about`} className="mt-1 text-xs text-text-faint">
          {modeSentence("fixed")}
        </p>
      ) : null}
      {!plain ? <p className="mt-1 text-xs text-text-muted">{entry.mode === "open" ? notPlain(row) : "An agent cannot fill this one in, so give it a fixed value or leave it out."}</p> : null}
      {error && entry.mode !== "fixed" ? (
        <p id={`${id}-error`} className="mt-1 text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </li>
  );
}

function FixedValue({ row, id, text, error, onChange }: { row: ArgRow; id: string; text: string; error: string | undefined; onChange: (text: string) => void }) {
  const label = `Value of ${row.name}`;
  const common = { "aria-invalid": error ? true : undefined, "aria-describedby": `${id}-about${error ? ` ${id}-error` : ""}` } as const;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      {row.kind === "enum" ? (
        <select className="input pr-8" aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} {...common}>
          <option value="">Choose…</option>
          {row.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : row.kind === "boolean" ? (
        <select className="input pr-8" aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} {...common}>
          <option value="">Choose…</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      ) : row.kind === "strings" || row.kind === null ? (
        <AreaField label={label} value={text} onChange={(event) => onChange(event.target.value)} hint={row.kind === "strings" ? "One item on each line." : "Written as JSON."} error={error} rows={3} spellCheck={false} />
      ) : (
        <input className="input" aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} inputMode={row.kind === "string" ? undefined : "decimal"} autoComplete="off" spellCheck={false} {...common} />
      )}
      {error && row.kind !== "strings" && row.kind !== null ? (
        <p id={`${id}-error`} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
