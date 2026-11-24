// The Wall's server settings (section "wall"), read from the settings the
// whole app already loads. Every value has a fallback so the Wall still draws
// if a setting is missing or of the wrong type.

import type { Setting } from "../../../api.ts";

export const TILE_IDS = ["clock", "usage", "bench", "today", "models", "feed"] as const;
export type TileId = (typeof TILE_IDS)[number];

export type WallSettings = {
  /** The tiles to show, in order. */
  tiles: TileId[];
  clock24h: boolean;
  showSeconds: boolean;
  forceDark: boolean;
  /** How often everything is read again, whatever the feed says. */
  refreshMs: number;
  pixelShift: boolean;
  pixelShiftMs: number;
  feedLines: number;
  taskLines: number;
};

export const WALL_DEFAULTS: WallSettings = {
  tiles: [...TILE_IDS],
  clock24h: false,
  showSeconds: false,
  forceDark: false,
  refreshMs: 60_000,
  pixelShift: false,
  pixelShiftMs: 5 * 60_000,
  feedLines: 8,
  taskLines: 8,
};

export function isTileId(value: unknown): value is TileId {
  return typeof value === "string" && (TILE_IDS as readonly string[]).includes(value);
}

/** The known tile ids in a setting's list, in its order, each once. Anything else is ignored. */
export function tileOrder(value: unknown): TileId[] {
  if (!Array.isArray(value)) return [...TILE_IDS];
  const seen = new Set<TileId>();
  for (const item of value) {
    const id = typeof item === "string" ? item.trim().toLowerCase() : "";
    if (isTileId(id)) seen.add(id);
  }
  return [...seen];
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function whole(value: unknown, fallback: number, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.round(value)));
}

/** Reads the Wall's settings out of the server's list. */
export function readWallSettings(settings: readonly Pick<Setting, "key" | "value">[] | undefined): WallSettings {
  const values = new Map<string, unknown>();
  for (const setting of settings ?? []) values.set(setting.key, setting.value);
  const has = (key: string) => values.has(key);
  const d = WALL_DEFAULTS;
  return {
    tiles: has("wall.tiles") ? tileOrder(values.get("wall.tiles")) : [...d.tiles],
    clock24h: bool(values.get("wall.clock_24h"), d.clock24h),
    showSeconds: bool(values.get("wall.show_seconds"), d.showSeconds),
    forceDark: bool(values.get("wall.force_dark"), d.forceDark),
    refreshMs: whole(values.get("wall.refresh_s"), d.refreshMs / 1000, 10, 3600) * 1000,
    pixelShift: bool(values.get("wall.pixel_shift"), d.pixelShift),
    pixelShiftMs: whole(values.get("wall.pixel_shift_minutes"), d.pixelShiftMs / 60_000, 1, 120) * 60_000,
    feedLines: whole(values.get("wall.feed_lines"), d.feedLines, 1, 50),
    taskLines: whole(values.get("wall.task_lines"), d.taskLines, 1, 50),
  };
}
