import { useEffect, useRef, useState } from "react";
import type { Setting } from "../api";
import { useDebouncedCallback } from "../hooks/useDebouncedCallback";
import { coerceSettingValue } from "../lib/settings";

type NumberControlProps = {
  id: string;
  setting: Setting;
  describedBy?: string;
  onSave: (value: number) => void;
};

const SAVE_DELAY_MS = 600;

/** Number setting honouring the schema's min/max; invalid input is not saved. */
export function NumberControl({ id, setting, describedBy, onSave }: NumberControlProps) {
  const current = String(setting.value);
  const [draft, setDraft] = useState(current);
  const [error, setError] = useState<string | null>(null);
  const dirty = useRef(false);
  const errorId = `${id}-error`;
  const save = useDebouncedCallback((next: number) => {
    dirty.current = false;
    onSave(next);
  }, SAVE_DELAY_MS);

  useEffect(() => {
    if (!dirty.current) {
      setDraft(current);
      setError(null);
    }
  }, [current]);

  const range =
    setting.min !== undefined && setting.max !== undefined
      ? `${setting.min} to ${setting.max}`
      : setting.min !== undefined
        ? `${setting.min} or more`
        : setting.max !== undefined
          ? `${setting.max} or less`
          : null;

  return (
    <div className="flex flex-col items-start gap-1 sm:items-end">
      <input
        id={id}
        type="number"
        inputMode={setting.type === "int" ? "numeric" : "decimal"}
        className="input w-32 font-mono"
        value={draft}
        min={setting.min}
        max={setting.max}
        step={setting.type === "int" ? 1 : "any"}
        aria-invalid={error ? true : undefined}
        aria-describedby={[describedBy, error ? errorId : null].filter(Boolean).join(" ") || undefined}
        onChange={(event) => {
          const raw = event.target.value;
          setDraft(raw);
          const result = coerceSettingValue(setting, raw);
          if (result.ok && typeof result.value === "number") {
            dirty.current = true;
            setError(null);
            save.call(result.value);
          } else {
            dirty.current = true;
            save.cancel();
            setError(result.ok ? "Enter a number." : result.error);
          }
        }}
        onBlur={() => {
          save.flush();
          if (error) {
            // Leave the field showing the last saved value rather than a bad draft.
            dirty.current = false;
            setDraft(current);
            setError(null);
          }
        }}
      />
      {error ? (
        <p id={errorId} role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : range ? (
        <p className="text-[11px] text-text-faint">{range}</p>
      ) : null}
    </div>
  );
}
