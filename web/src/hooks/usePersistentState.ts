import { useCallback, useState } from "react";

function read(key: string): unknown {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? undefined : JSON.parse(raw);
  } catch {
    return undefined;
  }
}

/**
 * State remembered in localStorage. `parse` validates whatever was stored and
 * returns undefined to fall back to `initial`.
 */
export function usePersistentState<T>(
  key: string,
  initial: T,
  parse: (stored: unknown) => T | undefined,
): [T, (next: T | ((previous: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => parse(read(key)) ?? initial);

  const update = useCallback(
    (next: T | ((previous: T) => T)) => {
      setValue((previous) => {
        const resolved = typeof next === "function" ? (next as (previous: T) => T)(previous) : next;
        try {
          window.localStorage.setItem(key, JSON.stringify(resolved));
        } catch {
          // Storage can be unavailable (private mode); the state still works in memory.
        }
        return resolved;
      });
    },
    [key],
  );

  return [value, update];
}

export function parseBoolean(stored: unknown): boolean | undefined {
  return typeof stored === "boolean" ? stored : undefined;
}

export function parseNumber(stored: unknown): number | undefined {
  return typeof stored === "number" && Number.isFinite(stored) ? stored : undefined;
}
