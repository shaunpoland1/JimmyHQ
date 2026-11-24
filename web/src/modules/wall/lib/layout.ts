// How the tiles are laid out on a screen of any shape, how many lines fit in
// a tile, and the small move that protects an always-on panel.

export type WallLayout = {
  /** How many tiles sit in each row, top to bottom. */
  rows: number[];
  /** The most tiles in any one row. */
  cols: number;
};

/** A tile reads best a little taller than it is wide. */
const TILE_SHAPE = 0.9;

/**
 * Splits `count` tiles into rows for a screen of `width` by `height`, picking
 * the number of rows that leaves tiles closest to the preferred shape. A wide,
 * short panel gets one row; a squarer screen gets two; an upright one more.
 */
export function arrange(count: number, width: number, height: number): WallLayout {
  const n = Math.max(0, Math.floor(count));
  if (n === 0) return { rows: [], cols: 0 };
  if (!(width > 0) || !(height > 0)) return { rows: [n], cols: n };
  let best = 1;
  let bestScore = Number.POSITIVE_INFINITY;
  for (let rows = 1; rows <= n; rows += 1) {
    const cols = Math.ceil(n / rows);
    // The same number of columns in fewer rows is always the better fit.
    if (rows > 1 && cols === Math.ceil(n / (rows - 1))) continue;
    const shape = width / cols / (height / rows);
    const score = Math.abs(Math.log(shape / TILE_SHAPE));
    if (score < bestScore) {
      best = rows;
      bestScore = score;
    }
  }
  const cols = Math.ceil(n / best);
  const rows: number[] = [];
  for (let left = n; left > 0; left -= cols) rows.push(Math.min(cols, left));
  return { rows, cols };
}

export type FitPlan = {
  /** How many items to draw. */
  shown: number;
  /** How many are left out; above zero, a "+n more" line is drawn and has been allowed for. */
  more: number;
};

/**
 * How many of `total` lines to draw when at most `cap` are wanted and `room`
 * lines fit (null before the tile has been measured). With `countRest`, one
 * line is kept back for "+n more" whenever something is left out.
 */
export function fitLines(total: number, cap: number, room: number | null, countRest = true): FitPlan {
  const all = Math.max(0, Math.floor(total));
  const wanted = Math.min(all, Math.max(0, Math.floor(cap)));
  const space = room === null ? wanted + 1 : Math.max(0, Math.floor(room));
  if (!countRest) return { shown: Math.min(wanted, space), more: 0 };
  if (wanted === all && all <= space) return { shown: all, more: 0 };
  const shown = Math.max(0, Math.min(wanted, space - 1));
  return { shown, more: all - shown };
}

export type Offset = { x: number; y: number };

/** The most the picture moves from its home, in pixels. The board keeps this much margin. */
export const SHIFT_REACH = 3;

// A slow loop around the home position, one small move at a time.
const SHIFT_PATH: readonly Offset[] = [
  { x: 0, y: 0 },
  { x: 2, y: 0 },
  { x: 3, y: 2 },
  { x: 2, y: 3 },
  { x: 0, y: 3 },
  { x: -2, y: 2 },
  { x: -3, y: 0 },
  { x: -2, y: -2 },
  { x: 0, y: -3 },
  { x: 2, y: -2 },
];

/** Where the picture sits at `now` when it moves once every `everyMs`. Off means home. */
export function pixelShift(now: number, everyMs: number, on: boolean): Offset {
  if (!on || !(everyMs > 0) || !Number.isFinite(now)) return { x: 0, y: 0 };
  const step = Math.floor(now / everyMs);
  const index = ((step % SHIFT_PATH.length) + SHIFT_PATH.length) % SHIFT_PATH.length;
  return SHIFT_PATH[index] ?? { x: 0, y: 0 };
}
