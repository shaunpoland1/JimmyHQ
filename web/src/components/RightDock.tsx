import { Sparkles, X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import type { TapeView } from "../hooks/useTape";
import { DOCK_MAX_WIDTH, DOCK_MIN_WIDTH, DOCK_STEP, clampDockWidth } from "../lib/dock";
import type { DockTab } from "../lib/dock";
import { stepIndex } from "../lib/palette";
import { EmptyState } from "./EmptyState";
import { TodayPanel } from "../modules/tasks/TodayPanel";
import { TapePanel } from "./TapePanel";

type RightDockProps = {
  /** Narrow screens: render as an overlay instead of a column. */
  overlay: boolean;
  width: number;
  onWidth: (width: number) => void;
  /** The panels to show, already filtered and ordered by the owner's setting. */
  tabs: readonly { id: DockTab; label: string }[];
  tab: DockTab;
  onTab: (tab: DockTab) => void;
  onClose: () => void;
  tape: TapeView;
};

/** Resizable right-hand dock with the Today, Tape and Assistant panels. */
export function RightDock({ overlay, width, onWidth, tabs: dockTabs, tab, onTab, onClose, tape }: RightDockProps) {
  const baseId = useId();
  const tabs = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  useEffect(() => {
    if (!overlay) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [overlay, onClose]);

  const onHandleDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  const onHandleMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    // The dock sits against the right edge, so its width is the distance to it.
    onWidth(clampDockWidth(window.innerWidth - event.clientX, window.innerWidth));
  };
  const onHandleUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const onHandleKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const delta = event.key === "ArrowLeft" ? DOCK_STEP : event.key === "ArrowRight" ? -DOCK_STEP : 0;
    if (delta === 0) return;
    event.preventDefault();
    onWidth(clampDockWidth(width + delta, window.innerWidth));
  };

  const onTabKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const position = dockTabs.findIndex((item) => item.id === tab);
    let next = position;
    if (event.key === "ArrowRight") next = stepIndex(position, 1, dockTabs.length);
    else if (event.key === "ArrowLeft") next = stepIndex(position, -1, dockTabs.length);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = dockTabs.length - 1;
    else return;
    event.preventDefault();
    const target = dockTabs[next];
    if (!target) return;
    onTab(target.id);
    tabs.current?.querySelector<HTMLElement>(`[data-tab="${target.id}"]`)?.focus();
  };

  const panel = (
    <aside
      aria-label="Side panel"
      style={overlay ? undefined : { width }}
      className={`relative flex h-full flex-none flex-col border-l border-border bg-surface ${
        overlay ? "z-10 ml-auto w-[min(100vw,22rem)] shadow-pop" : ""
      }`}
    >
      {overlay ? null : (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize side panel"
          aria-valuenow={width}
          aria-valuemin={DOCK_MIN_WIDTH}
          aria-valuemax={DOCK_MAX_WIDTH}
          tabIndex={0}
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
          onKeyDown={onHandleKey}
          className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize touch-none hover:bg-accent/20 focus-visible:bg-accent/30"
        />
      )}
      <div className="flex h-11 flex-none items-center gap-1 border-b border-border px-2">
        <div ref={tabs} role="tablist" aria-label="Side panel" onKeyDown={onTabKey} className="flex min-w-0 flex-1 items-center gap-0.5">
          {dockTabs.map((item) => {
            const selected = item.id === tab;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                data-tab={item.id}
                id={`${baseId}-tab-${item.id}`}
                aria-selected={selected}
                aria-controls={`${baseId}-panel`}
                tabIndex={selected ? 0 : -1}
                onClick={() => onTab(item.id)}
                className={`h-7 rounded-md px-2.5 text-xs transition-colors ${
                  selected ? "bg-surface-2 font-medium text-text" : "text-text-muted hover:text-text"
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Hide side panel">
          <X size={15} />
        </button>
      </div>
      <div id={`${baseId}-panel`} role="tabpanel" aria-labelledby={`${baseId}-tab-${tab}`} className="min-h-0 flex-1 overflow-hidden">
        {tab === "today" ? (
          <TodayPanel />
        ) : tab === "tape" ? (
          <TapePanel tape={tape} />
        ) : (
          <EmptyState icon={Sparkles} title="Assistant" description="A quick way to ask your assistant about whatever is on screen will live here." compact />
        )}
      </div>
    </aside>
  );

  if (!overlay) return panel;

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="absolute inset-0 bg-black/35" onClick={onClose} aria-hidden="true" />
      {panel}
    </div>
  );
}
