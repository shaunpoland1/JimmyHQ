import { useEffect, useRef, useState } from "react";
import { useDebouncedCallback } from "../hooks/useDebouncedCallback";

type TextControlProps = {
  id: string;
  value: string;
  describedBy?: string;
  onSave: (value: string) => void;
};

const SAVE_DELAY_MS = 600;

/** Text setting: saves a short moment after typing stops, or on blur. */
export function TextControl({ id, value, describedBy, onSave }: TextControlProps) {
  const [draft, setDraft] = useState(value);
  const dirty = useRef(false);
  const save = useDebouncedCallback((next: string) => {
    dirty.current = false;
    onSave(next);
  }, SAVE_DELAY_MS);

  // Follow outside changes (reset, rollback) unless the user is mid-edit.
  useEffect(() => {
    if (!dirty.current) setDraft(value);
  }, [value]);

  return (
    <input
      id={id}
      type="text"
      className="input"
      value={draft}
      aria-describedby={describedBy}
      autoComplete="off"
      onChange={(event) => {
        dirty.current = true;
        setDraft(event.target.value);
        save.call(event.target.value);
      }}
      onBlur={save.flush}
    />
  );
}
