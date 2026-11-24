// The Wall's moving parts: the clock tick, the screen size, the live feed that
// drives refreshes (with a slow safety refetch behind it), fullscreen, the
// forced-dark theme and the controls that hide until someone is there.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TapeEvent } from "../../api";
import { SETTINGS_QUERY_KEY } from "../../hooks/useSettings";
import { useTheme } from "../../hooks/useTheme";
import { subscribeFeed } from "../../lib/feed";
import { mergeEvents } from "../../lib/tape";
import { parseThemePref } from "../../lib/theme";
import { healthPollMs } from "../models/lib/indicators";
import { WALL_KEY, WALL_KEYS, wallApi } from "./api";
import { untilNextTick } from "./lib/clock";
import { staleAfter } from "./lib/tiles";
import type { WallPart } from "./lib/tiles";

/** The time now, renewed as each second (or minute) begins. */
export function useNow(seconds: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const arm = () => {
      // A few milliseconds late, so the new second has certainly begun.
      timer = setTimeout(() => {
        setNow(Date.now());
        arm();
      }, untilNextTick(Date.now(), seconds) + 4);
    };
    setNow(Date.now());
    arm();
    // A sleeping tab's timers run late: catch up the moment it is seen again.
    const onVisible = () => {
      if (document.visibilityState === "visible") setNow(Date.now());
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [seconds]);
  return now;
}

export type Viewport = { width: number; height: number };

function readViewport(): Viewport {
  return { width: window.innerWidth, height: window.innerHeight };
}

/** The window's size, followed as it changes. */
export function useViewport(): Viewport {
  const [size, setSize] = useState(readViewport);
  useEffect(() => {
    const onResize = () =>
      setSize((current) => {
        const next = readViewport();
        return current.width === next.width && current.height === next.height ? current : next;
      });
    window.addEventListener("resize", onResize);
    onResize();
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return size;
}

/** Events land in bursts (one action prints several); read again once they settle. */
const SETTLE_MS = 400;
/** How long the feed may be down before the Wall says so. */
const DOWN_GRACE_MS = 3000;

function keyOf(part: WallPart): readonly string[] {
  return part === "settings" ? SETTINGS_QUERY_KEY : WALL_KEYS[part];
}

/**
 * Keeps the Wall current: a Tape event marks what it touches stale, a
 * reconnect marks everything stale, and everything is read again every
 * `refreshMs` regardless. Returns true while the live feed is down.
 */
export function useWallLive(refreshMs: number): boolean {
  const client = useQueryClient();
  const [down, setDown] = useState(false);

  useEffect(() => {
    const pending = new Set<WallPart>();
    let settle: ReturnType<typeof setTimeout> | null = null;
    const everything = () => {
      void client.invalidateQueries({ queryKey: WALL_KEY });
      void client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
    };
    // The feed cannot say why it is down. Asking the server once can: if the
    // session has ended the answer is a 401, and the app shows the sign-in
    // screen now rather than at the next safety refetch.
    let grace: ReturnType<typeof setTimeout> | null = null;
    const wentDown = () => {
      grace = null;
      setDown(true);
      everything();
    };
    grace = setTimeout(wentDown, DOWN_GRACE_MS);
    const flush = () => {
      settle = null;
      pending.forEach((part) => void client.invalidateQueries({ queryKey: keyOf(part) }));
      pending.clear();
    };
    const stop = subscribeFeed({
      onEvent: (event) => {
        staleAfter(event.kind, event.payload ?? {}).forEach((part) => pending.add(part));
        settle ??= setTimeout(flush, SETTLE_MS);
      },
      // Anything may have happened while the feed was away.
      onReconnect: everything,
      onStatus: (live) => {
        if (grace) clearTimeout(grace);
        grace = null;
        if (live) setDown(false);
        else grace = setTimeout(wentDown, DOWN_GRACE_MS);
      },
    });
    return () => {
      stop();
      if (settle) clearTimeout(settle);
      if (grace) clearTimeout(grace);
    };
  }, [client]);

  // The safety net: read everything again now and then, whatever the feed says.
  // A read that fails because the session ended shows the sign-in screen.
  useEffect(() => {
    const timer = setInterval(() => {
      void client.invalidateQueries({ queryKey: WALL_KEY });
      void client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
    }, refreshMs);
    return () => clearInterval(timer);
  }, [client, refreshMs]);

  return down;
}

export function useUsage() {
  return useQuery({ queryKey: WALL_KEYS.usage, queryFn: wallApi.usage });
}

export function useTapeStats() {
  return useQuery({ queryKey: WALL_KEYS.tape, queryFn: wallApi.tapeStats });
}

export function useBench() {
  return useQuery({ queryKey: WALL_KEYS.bench, queryFn: wallApi.bench });
}

export function useToday() {
  return useQuery({ queryKey: WALL_KEYS.today, queryFn: wallApi.tasks });
}

/**
 * Provider health. The server checks a provider when its health is read, at
 * most once per interval and without printing an event, so this is read as
 * often as the server says it checks.
 */
export function useProviders() {
  return useQuery({
    queryKey: WALL_KEYS.models,
    queryFn: wallApi.health,
    refetchInterval: (query) => healthPollMs(query.state.data?.interval_s),
  });
}

/** How many live events the feed tile keeps beyond its first page. */
const FEED_KEEP = 60;

/** The newest Tape events: one page from the server, then live from the shared feed. */
export function useRecentEvents(lines: number) {
  // One more than is shown, so the tile is still full if the newest is a duplicate.
  const limit = Math.min(200, lines + 1);
  const page = useQuery({ queryKey: [...WALL_KEYS.feed, limit], queryFn: () => wallApi.recent(limit) });
  const [streamed, setStreamed] = useState<TapeEvent[]>([]);
  useEffect(
    () => subscribeFeed({ onEvent: (event) => setStreamed((current) => mergeEvents([event], current, FEED_KEEP)) }),
    [],
  );
  const events = useMemo(() => mergeEvents(streamed, page.data?.events ?? [], FEED_KEEP), [streamed, page.data]);
  return { page, events };
}

export type Fullscreen = {
  /** False where the browser cannot do it (some phones, some embedded views). */
  supported: boolean;
  active: boolean;
  toggle: () => void;
  leave: () => void;
};

/** The browser's fullscreen mode for the whole page. `onRefused` hears why it could not start. */
export function useFullscreen(onRefused: () => void): Fullscreen {
  const supported = typeof document !== "undefined" && document.fullscreenEnabled === true;
  const [active, setActive] = useState(() => supported && document.fullscreenElement !== null);
  const refused = useRef(onRefused);
  refused.current = onRefused;

  useEffect(() => {
    const onChange = () => setActive(document.fullscreenElement !== null);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const leave = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
  }, []);

  const toggle = useCallback(() => {
    if (!supported) return;
    if (document.fullscreenElement) leave();
    else void document.documentElement.requestFullscreen().catch(() => refused.current());
  }, [supported, leave]);

  return { supported, active, toggle, leave };
}

/** How long the controls stay after the pointer last moved. */
const AWAKE_MS = 3500;

/** True for a few seconds after the pointer moves or is pressed: someone is at the screen. */
export function useAwake(): { awake: boolean; wake: (event?: { type: string; clientX: number; clientY: number }) => void } {
  const [awake, setAwake] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const last = useRef<{ x: number; y: number } | null>(null);
  const wake = useCallback((event?: { type: string; clientX: number; clientY: number }) => {
    // Some browsers report a "move" when the page changes under a pointer that
    // is standing still, which a ticking clock does all day. Only a real move counts.
    if (event?.type === "pointermove") {
      const was = last.current;
      last.current = { x: event.clientX, y: event.clientY };
      if (was && was.x === event.clientX && was.y === event.clientY) return;
    }
    setAwake(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setAwake(false), AWAKE_MS);
  }, []);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return { awake, wake };
}

/**
 * The Wall's theme. It follows the app's theme setting, as the shell does;
 * with `forceDark` the page is held dark while the Wall is open, without
 * changing the app's own preference.
 */
export function useWallTheme(themeSetting: unknown, forceDark: boolean): void {
  const theme = useTheme();
  const { setPref, resolved } = theme;
  const serverPref = parseThemePref(themeSetting);
  useEffect(() => {
    if (serverPref) setPref(serverPref);
  }, [serverPref, setPref]);

  // Remembered for the pre-load script (public/theme-boot.js), so the next
  // load of a forced-dark Wall starts dark instead of flashing the app theme.
  useEffect(() => {
    try {
      if (forceDark) localStorage.setItem("jimmyhq.wall.force_dark", "1");
      else localStorage.removeItem("jimmyhq.wall.force_dark");
    } catch {
      // Storage can be unavailable; the Wall still turns dark once it loads.
    }
  }, [forceDark]);

  const appTheme = useRef(resolved);
  appTheme.current = resolved;
  useEffect(() => {
    if (!forceDark) return;
    const root = document.documentElement;
    const hold = () => {
      if (root.getAttribute("data-theme") !== "dark") root.setAttribute("data-theme", "dark");
    };
    hold();
    // The app sets the attribute again when its own theme changes: put dark back.
    const observer = new MutationObserver(hold);
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      observer.disconnect();
      root.setAttribute("data-theme", appTheme.current);
    };
  }, [forceDark]);
}
