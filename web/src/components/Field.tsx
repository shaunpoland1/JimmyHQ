import { useId } from "react";
import type { InputHTMLAttributes } from "react";

type FieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "className"> & {
  label: string;
  hint?: string;
  error?: string;
};

/** A labelled text input with an optional hint and error message. */
export function Field({ label, hint, error, ...input }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-text">
        {label}
      </label>
      <input id={id} className="input" aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...input} />
      {hint ? (
        <p id={hintId} className="text-xs text-text-faint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
