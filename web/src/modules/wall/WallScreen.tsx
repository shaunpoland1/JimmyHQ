import "./wall.css";

import { ArrowLeft, Maximize, Minimize } from "lucide-react";
import { useCallback, useEffect, useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import { errorDetail } from "../../api";
import { useSettingsQuery } from "../../hooks/useSettings";
import { useToast } from "../../hooks/useToast";
import { THEME_SETTING_KEY } from "../../lib/theme";
import { useAwake, useFullscreen, useNow, useViewport, useWallLive, useWallTheme } from "./hooks";
import { arrange, pixelShift } from "./lib/layout";
import { readWallSettings } from "./lib/settings";
import type { TileId, WallSettings } from "./lib/settings";
import { BenchTile, ClockTile, FeedTile, ModelsTile, TodayTile, UsageTile } from "./tiles";

const MINUTE = 60_000;

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
}

/** The full-screen status board at /wall. Owned by the wall module. */
export function WallScreen() {
  const settingsQuery = useSettingsQuery();
  const list = settingsQuery.data?.settings;
  const settings = useMemo(() => readWallSettings(list), [list]);
  const themeSetting = list?.find((setting) => setting.key === THEME_SETTING_KEY)?.value;
  useWallTheme(themeSetting, settings.forceDark);

  const down = useWallLive(settings.refreshMs);
  const now = useNow(settings.showSeconds && settings.tiles.includes("clock"));
  const viewport = useViewport();
  const { awake, wake } = useAwake();
  const toast = useToast();
  const refused = useCallback(() => toast.error("This browser did not allow fullscreen. Try its own fullscreen command instead."), [toast]);
  const fullscreen = useFullscreen(refused);
  const { toggle, leave } = fullscreen;

  // F toggles fullscreen; Escape leaves it (the browser does that too, where it can).
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return;
      if (event.key === "f" || event.key === "F") {
        event.preventDefault();
        toggle();
      } else if (event.key === "Escape") {
        leave();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggle, leave]);

  useEffect(() => {
    const before = document.title;
    document.title = "The Wall · JimmyHQ";
    return () => {
      document.title = before;
    };
  }, []);

  const tiles = settings.tiles;
  const layout = useMemo(() => arrange(tiles.length, viewport.width, viewport.height), [tiles.length, viewport.width, viewport.height]);
  const shift = pixelShift(now, settings.pixelShiftMs, settings.pixelShift);
  const minute = Math.floor(now / MINUTE) * MINUTE;

  let board: ReactNode;
  if (!list && settingsQuery.isError) {
    board = (
      <div className="wall-note" data-tone="bad" role="alert">
        <strong>The Wall cannot read its settings</strong>
        <p className="wall-note-detail">{errorDetail(settingsQuery.error)}</p>
        <button type="button" className="wall-btn" onClick={() => void settingsQuery.refetch()}>
          Try again
        </button>
      </div>
    );
  } else if (!list) {
    board = (
      <div className="wall-note" role="status">
        <p>Loading…</p>
      </div>
    );
  } else if (tiles.length === 0) {
    board = (
      <div className="wall-note">
        <strong>No tiles are switched on</strong>
        <p className="wall-note-detail">Choose which tiles to show under Settings, The Wall.</p>
      </div>
    );
  } else {
    let start = 0;
    board = layout.rows.map((count) => {
      const row = tiles.slice(start, start + count);
      start += count;
      return (
        <div className="wall-row" key={row.join("+")}>
          {row.map((id) => (
            <WallTile key={id} id={id} settings={settings} now={now} minute={minute} live={!down} />
          ))}
        </div>
      );
    });
  }

  const style = {
    "--wall-cols": Math.max(1, layout.cols),
    "--wall-rows": Math.max(1, layout.rows.length),
    "--wall-x": `${shift.x}px`,
    "--wall-y": `${shift.y}px`,
  } as CSSProperties;

  return (
    <main className="wall" aria-label="The Wall" data-awake={awake} style={style} onPointerMove={wake} onPointerDown={wake}>
      <div className="wall-board">{board}</div>
      {down ? (
        <p className="wall-marker" role="status">
          <span className="wall-dot" aria-hidden="true" />
          Reconnecting…
        </p>
      ) : null}
      <nav className="wall-controls" aria-label="Wall controls">
        <a className="btn" href="/">
          <ArrowLeft size={14} aria-hidden="true" />
          Back to app
        </a>
        {fullscreen.supported ? (
          <button
            type="button"
            className="btn"
            onClick={toggle}
            aria-keyshortcuts="F"
            title={fullscreen.active ? "Leave fullscreen (F or Escape)" : "Fullscreen (F)"}
          >
            {fullscreen.active ? <Minimize size={14} aria-hidden="true" /> : <Maximize size={14} aria-hidden="true" />}
            {fullscreen.active ? "Leave fullscreen" : "Fullscreen"}
          </button>
        ) : null}
      </nav>
    </main>
  );
}

type WallTileProps = { id: TileId; settings: WallSettings; now: number; minute: number; live: boolean };

function WallTile({ id, settings, now, minute, live }: WallTileProps) {
  switch (id) {
    case "clock":
      return <ClockTile now={now} hour24={settings.clock24h} seconds={settings.showSeconds} />;
    case "usage":
      return <UsageTile />;
    case "bench":
      return <BenchTile minute={minute} />;
    case "today":
      return <TodayTile lines={settings.taskLines} hour24={settings.clock24h} />;
    case "models":
      return <ModelsTile />;
    case "feed":
      return <FeedTile lines={settings.feedLines} hour24={settings.clock24h} live={live} />;
  }
}
