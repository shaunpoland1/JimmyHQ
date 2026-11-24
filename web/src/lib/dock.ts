// Right dock sizing.

export const DOCK_MIN_WIDTH = 260;
export const DOCK_MAX_WIDTH = 640;
export const DOCK_DEFAULT_WIDTH = 320;
export const DOCK_STEP = 16;

export type DockTab = "today" | "tape" | "assistant";
export const DOCK_TABS: readonly { id: DockTab; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "tape", label: "Tape" },
  { id: "assistant", label: "Assistant" },
];

export function parseDockTab(value: unknown): DockTab | undefined {
  return value === "today" || value === "tape" || value === "assistant" ? value : undefined;
}

/** Keeps the dock between its limits and never wider than half the viewport. */
export function clampDockWidth(width: number, viewportWidth?: number): number {
  if (!Number.isFinite(width)) return DOCK_DEFAULT_WIDTH;
  const ceiling =
    viewportWidth === undefined
      ? DOCK_MAX_WIDTH
      : Math.max(DOCK_MIN_WIDTH, Math.min(DOCK_MAX_WIDTH, Math.floor(viewportWidth / 2)));
  return Math.round(Math.min(ceiling, Math.max(DOCK_MIN_WIDTH, width)));
}
