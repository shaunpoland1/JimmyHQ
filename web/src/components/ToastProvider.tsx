import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api as server } from "../api";
import { SETTINGS_QUERY_KEY } from "../hooks/useSettings";
import { ToastContext } from "../hooks/useToast";
import type { ToastAction, ToastApi, ToastItem, ToastKind } from "../hooks/useToast";
import { actionSeconds, leftAt, lifetimeMs, pauseCountdown, resumeCountdown, startCountdown } from "../lib/toast";
import type { Countdown } from "../lib/toast";

const MAX_VISIBLE = 4;

const ICONS = { info: Info, success: CircleCheck, error: CircleAlert } as const;
const TONES: Record<ToastKind, string> = { info: "text-accent", success: "text-success", error: "text-danger" };

/** Provides `useToast()` and renders the stack of toasts. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const countdowns = useRef(new Map<number, Countdown>());
  const stack = useRef<HTMLDivElement>(null);
  // The countdowns wait while the pointer or the keyboard is on a toast.
  const held = useRef({ hover: false, focus: false });
  const holdRef = useRef<(change: Partial<{ hover: boolean; focus: boolean }>) => void>(() => {});

  // Reads the settings other screens have fetched; never asks the server itself, so
  // nothing is requested before the owner has signed in.
  const settings = useQuery({ queryKey: SETTINGS_QUERY_KEY, queryFn: server.settings, enabled: false });
  const seconds = useRef(actionSeconds(undefined));
  seconds.current = actionSeconds(settings.data?.settings.find((setting) => setting.key === "appearance.action_message_seconds")?.value);

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    countdowns.current.delete(id);
    // A toast that holds the focus hands it to the screen, so the keyboard is not left on
    // nothing. The stack's own blur handler then lets the other countdowns run again.
    const active = document.activeElement;
    if (active instanceof HTMLElement && active.closest(`[data-toast-id="${id}"]`)) {
      document.getElementById("main")?.focus({ preventScroll: true });
      if (document.activeElement === active) holdRef.current({ focus: false });
    }
    // A toast removed from under the pointer sends no "leave"; the next move over one holds again.
    holdRef.current({ hover: false });
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const arm = useCallback(
    (id: number) => {
      const countdown = countdowns.current.get(id);
      const old = timers.current.get(id);
      if (old) clearTimeout(old);
      timers.current.delete(id);
      if (!countdown || countdown.since === null) return;
      timers.current.set(id, setTimeout(() => dismiss(id), leftAt(countdown, Date.now())));
    },
    [dismiss],
  );

  const hold = useCallback(
    (change: Partial<{ hover: boolean; focus: boolean }>) => {
      const was = held.current.hover || held.current.focus;
      held.current = { ...held.current, ...change };
      const now = held.current.hover || held.current.focus;
      if (was === now) return;
      const at = Date.now();
      for (const [id, countdown] of countdowns.current) {
        countdowns.current.set(id, now ? pauseCountdown(countdown, at) : resumeCountdown(countdown, at));
        arm(id);
      }
    },
    [arm],
  );
  holdRef.current = hold;

  const show = useCallback(
    (kind: ToastKind, message: string, action?: ToastAction) => {
      const id = nextId.current++;
      setItems((current) => [...current, { id, kind, message, action }].slice(-MAX_VISIBLE));
      const waiting = held.current.hover || held.current.focus;
      countdowns.current.set(id, startCountdown(lifetimeMs(kind, action !== undefined, seconds.current), Date.now(), waiting));
      arm(id);
    },
    [arm],
  );

  // The last toast went while the pointer was on it: nothing is left to leave, so nothing is held.
  useEffect(() => {
    if (items.length === 0) held.current = { hover: false, focus: false };
  }, [items.length]);

  useEffect(() => {
    const active = timers.current;
    return () => active.forEach((timer) => clearTimeout(timer));
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      show,
      error: (message, action) => show("error", message, action),
      success: (message, action) => show("success", message, action),
    }),
    [show],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-10 z-[60] flex flex-col items-center gap-2 px-4 sm:inset-x-auto sm:right-4 sm:items-end"
        aria-label="Notifications"
        ref={stack}
        onMouseOver={() => hold({ hover: true })}
        onMouseLeave={() => hold({ hover: false })}
        onFocus={() => hold({ focus: true })}
        onBlur={(event) => {
          if (!(event.relatedTarget instanceof Node) || !stack.current?.contains(event.relatedTarget)) hold({ focus: false });
        }}
      >
        {items.map((item) => {
          const Icon = ICONS[item.kind];
          return (
            <div
              key={item.id}
              data-toast-id={item.id}
              role={item.kind === "error" ? "alert" : "status"}
              className="pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm shadow-pop"
            >
              <Icon size={16} className={`mt-0.5 flex-none ${TONES[item.kind]}`} aria-hidden="true" />
              <p className="min-w-0 flex-1 break-words text-text">{item.message}</p>
              {item.action ? (
                <button
                  type="button"
                  className="flex-none rounded px-1.5 text-[13px] font-medium text-accent hover:underline"
                  onClick={() => {
                    item.action?.run();
                    dismiss(item.id);
                  }}
                >
                  {item.action.label}
                </button>
              ) : null}
              <button type="button" className="icon-btn -mr-1 -mt-0.5" onClick={() => dismiss(item.id)} aria-label="Dismiss notification">
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
