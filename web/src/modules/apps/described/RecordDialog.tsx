import { useId, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { Modal } from "../../../components/Modal";
import type { DescribedApi, DescribedRecord, Description, FieldSpec, LinkLabels, Value } from "./api";
import { useLinkOptions, useRecordActions } from "./hooks";
import { changesOnly, controlFor, formFor, readForm } from "./lib/form";
import type { FormState } from "./lib/form";
import { MAX_TEXT } from "./lib/form";
import { cleanText, editableFields, fieldLabel, recordNoun } from "./lib/schema";
import { MISSING_RECORD, displayValue, formatStamp } from "./lib/values";
import { ErrorLine } from "./parts";

type Props = {
  appId: string;
  api: DescribedApi;
  description: Description;
  table: string;
  /** The record being edited; none for a new one. */
  record?: DescribedRecord;
  labels: LinkLabels;
  /** Values a new record starts with, such as the day of a calendar cell. */
  preset?: Record<string, Value>;
  timeZone: string;
  onClose: () => void;
};

export function RecordDialog({ appId, api, description, table, record, labels, preset, timeZone, onClose }: Props) {
  const spec = description.tables[table];
  const actions = useRecordActions(appId, api);
  const [state, setState] = useState<FormState>(() => (spec ? formFor(spec, record, preset) : {}));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [deleteFailed, setDeleteFailed] = useState(false);
  const noun = recordNoun(table);
  if (!spec) return null;

  const names = editableFields(spec);
  const title = record ? `Edit ${noun}` : `New ${noun}`;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const read = readForm(spec, state);
    setErrors(read.errors);
    setFailure(null);
    if (Object.keys(read.errors).length > 0) return;
    setBusy(true);
    const outcome = record
      ? await actions.update(table, record.id, changesOnly(record, read.values))
      : await actions.create(table, read.values);
    setBusy(false);
    if (outcome.ok) onClose();
    else setFailure(outcome.message);
  };

  const remove = async (unlink: boolean) => {
    setBusy(true);
    const outcome = record ? await actions.remove(table, record.id, unlink) : null;
    setBusy(false);
    setAsking(false);
    if (outcome?.ok) onClose();
    else if (outcome) {
      setFailure(outcome.message);
      setDeleteFailed(true);
    }
  };

  return (
    <>
      <Modal title={title} onClose={onClose} widthClass="max-w-lg">
        <form onSubmit={save} noValidate className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
            {failure ? <ErrorLine>{failure}</ErrorLine> : null}
            {names.map((name, index) => (
              <Control
                key={name}
                appId={appId}
                api={api}
                description={description}
                table={table}
                name={name}
                value={state[name]}
                error={errors[name]}
                labels={labels}
                autoFocus={index === 0}
                onChange={(value) => setState((s) => ({ ...s, [name]: value }))}
              />
            ))}
            {record ? (
              <p className="text-xs text-text-faint">
                Added {formatStamp(record.created, timeZone)}
                {record.updated !== record.created ? `, changed ${formatStamp(record.updated, timeZone)}` : ""}.
              </p>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3">
            <div>
              {record ? (
                <button type="button" className="btn" onClick={() => setAsking(true)} disabled={busy}>
                  Delete
                </button>
              ) : null}
            </div>
            <div className="flex gap-2">
              {deleteFailed ? (
                <button type="button" className="btn btn-danger" onClick={() => void remove(true)} disabled={busy}>
                  Clear the links and delete
                </button>
              ) : null}
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {record ? "Save" : "Add"}
              </button>
            </div>
          </div>
        </form>
      </Modal>
      {asking && record ? (
        <ConfirmDialog
          title={`Delete this ${noun}?`}
          message={`“${cleanText(record.label, 80)}” will be deleted for good. This cannot be undone.`}
          confirmLabel="Delete"
          danger
          busy={busy}
          onConfirm={() => void remove(false)}
          onCancel={() => setAsking(false)}
        />
      ) : null}
    </>
  );
}

type ControlProps = {
  appId: string;
  api: DescribedApi;
  description: Description;
  table: string;
  name: string;
  value: string | boolean | undefined;
  error?: string;
  labels: LinkLabels;
  autoFocus: boolean;
  onChange: (value: string | boolean) => void;
};

function Control({ appId, api, description, table, name, value, error, labels, autoFocus, onChange }: ControlProps) {
  const tableSpec = description.tables[table];
  const spec = tableSpec?.fields[name];
  const id = useId();
  if (!tableSpec || !spec) return null;
  const control = controlFor(tableSpec, name);
  const label = fieldLabel(name, spec);
  const errorId = `${id}-error`;
  const hint = hintFor(spec);
  const hintId = `${id}-hint`;
  const common = {
    id,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined,
    "aria-required": spec.required ? true : undefined,
    autoFocus: autoFocus ? true : undefined,
  } as const;
  const text = typeof value === "string" ? value : "";

  let input;
  if (control === "checkbox") {
    input = (
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" id={id} checked={value === true} onChange={(e) => onChange(e.target.checked)} className="size-4 accent-[var(--accent)]" aria-describedby={common["aria-describedby"]} />
        <span>{label}</span>
      </label>
    );
  } else if (control === "textarea") {
    input = <textarea {...common} className="input h-auto min-h-[4.5rem] py-2" rows={3} maxLength={MAX_TEXT} value={text} onChange={(e) => onChange(e.target.value)} />;
  } else if (control === "select") {
    input = (
      <select {...common} className="input" value={text} onChange={(e) => onChange(e.target.value)}>
        <option value="">{spec.required ? "Choose one" : "None"}</option>
        {(spec.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  } else if (control === "link") {
    input = <LinkSelect {...common} appId={appId} api={api} spec={spec} field={name} labels={labels} value={text} onChange={onChange} />;
  } else {
    const inputMode = control === "int" ? "numeric" : control === "number" || control === "money" ? "decimal" : undefined;
    input = <input {...common} className="input" type={control === "date" ? "date" : "text"} inputMode={inputMode} maxLength={control === "text" ? MAX_TEXT : 40} value={text} onChange={(e) => onChange(e.target.value)} />;
  }

  return (
    <div className="flex flex-col gap-1.5">
      {control === "checkbox" ? null : (
        <label htmlFor={id} className="text-xs font-medium text-text">
          {label}
          {spec.required ? <span className="text-text-faint"> (needed)</span> : null}
        </label>
      )}
      {input}
      {hint ? (
        <p id={hintId} className="text-xs text-text-faint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function hintFor(spec: FieldSpec): string | null {
  if (spec.type === "money") return `An amount in ${spec.currency ?? "USD"}, such as 12.50.`;
  if (spec.type === "int" || spec.type === "number") {
    if (spec.min !== undefined && spec.max !== undefined) return `From ${spec.min} to ${spec.max}.`;
    if (spec.min !== undefined) return `${spec.min} or more.`;
    if (spec.max !== undefined) return `${spec.max} or less.`;
  }
  return null;
}

type LinkProps = {
  id: string;
  appId: string;
  api: DescribedApi;
  spec: FieldSpec;
  field: string;
  labels: LinkLabels;
  value: string;
  onChange: (value: string) => void;
  "aria-invalid"?: true;
  "aria-describedby"?: string;
  "aria-required"?: true;
  autoFocus?: true;
};

/** A drop-down of the records of the table this field links to. It can only pick a record, never type an address. */
function LinkSelect({ appId, api, spec, field, labels, value, onChange, ...aria }: LinkProps) {
  const options = useLinkOptions(appId, api, spec.table ?? "");
  const rows = useMemo(() => {
    const list = (options.data?.records ?? []).map((r) => ({ id: String(r.id), label: cleanText(r.label, 80) }));
    list.sort((a, b) => a.label.localeCompare(b.label));
    if (value && !list.some((r) => r.id === value)) list.unshift({ id: value, label: displayValue(spec, field, Number(value), { labels }) || MISSING_RECORD });
    return list;
  }, [options.data, value, spec, field, labels]);
  const more = (options.data?.total ?? 0) > rows.length;
  return (
    <>
      <select {...aria} className="input" value={value} onChange={(e) => onChange(e.target.value)} disabled={options.isPending}>
        <option value="">{options.isPending ? "Loading…" : options.isError ? "Could not load the list" : "None"}</option>
        {rows.map((row) => (
          <option key={row.id} value={row.id}>
            {row.label}
          </option>
        ))}
      </select>
      {more ? <p className="text-xs text-text-faint">Showing the first {rows.length} of {options.data?.total}.</p> : null}
      {options.data && options.data.total === 0 ? <p className="text-xs text-text-faint">There are no {recordNoun(spec.table ?? "")} records yet. Add one first.</p> : null}
    </>
  );
}
