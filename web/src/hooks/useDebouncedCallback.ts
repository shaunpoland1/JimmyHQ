import { useEffect, useMemo, useRef } from "react";

export type Debounced<A extends unknown[]> = {
  /** Schedules a call, replacing any pending one. */
  call: (...args: A) => void;
  /** Runs a pending call now. */
  flush: () => void;
  /** Drops a pending call. */
  cancel: () => void;
};

/** Debounces a callback; a pending call is flushed when the component unmounts. */
export function useDebouncedCallback<A extends unknown[]>(callback: (...args: A) => void, delayMs: number): Debounced<A> {
  const latest = useRef(callback);
  latest.current = callback;
  const pending = useRef<{ timer: ReturnType<typeof setTimeout>; args: A } | null>(null);

  const debounced = useMemo<Debounced<A>>(() => {
    const flush = () => {
      const job = pending.current;
      if (!job) return;
      clearTimeout(job.timer);
      pending.current = null;
      latest.current(...job.args);
    };
    const cancel = () => {
      if (pending.current) clearTimeout(pending.current.timer);
      pending.current = null;
    };
    const call = (...args: A) => {
      if (pending.current) clearTimeout(pending.current.timer);
      pending.current = { args, timer: setTimeout(flush, delayMs) };
    };
    return { call, flush, cancel };
  }, [delayMs]);

  useEffect(() => debounced.flush, [debounced]);

  return debounced;
}
