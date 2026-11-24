import type { ReactNode } from "react";

type Option<T extends string> = { value: T; label: string };

/** A segmented choice: one pressed at a time. */
export function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: readonly Option<T>[]; onChange: (value: T) => void }) {
  return (
    <div role="group" aria-label={label} className="inline-flex max-w-full gap-0.5 rounded-lg border border-border bg-surface p-0.5">
      {options.map((option) => {
        const pressed = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={pressed}
            onClick={() => onChange(option.value)}
            className={`h-7 whitespace-nowrap rounded-md px-2.5 text-xs ${pressed ? "bg-surface-2 font-semibold text-text ring-1 ring-inset ring-border" : "text-text-muted hover:text-text"}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
      {children}
    </p>
  );
}
