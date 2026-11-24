import { Menu, PanelRight, Search } from "lucide-react";
import type { ThemePref } from "../lib/theme";
import { ThemeSwitch } from "./ThemeSwitch";
import { UsageLine } from "./UsageLine";

type TopStripProps = {
  /** Whether to show the usage line (the `appearance.show_usage_strip` setting). */
  showUsage: boolean;
  narrow: boolean;
  onOpenNav: () => void;
  onOpenPalette: () => void;
  shortcutLabel: string;
  theme: ThemePref;
  onTheme: (pref: ThemePref) => void;
  dockOpen: boolean;
  onToggleDock: () => void;
};

/** The slim row above the current screen. */
export function TopStrip({ showUsage, narrow, onOpenNav, onOpenPalette, shortcutLabel, theme, onTheme, dockOpen, onToggleDock }: TopStripProps) {
  return (
    <header className="flex h-11 flex-none items-center gap-2 border-b border-border bg-bg px-2 sm:px-3">
      {narrow ? (
        <button type="button" className="icon-btn" onClick={onOpenNav} aria-label="Open navigation">
          <Menu size={16} />
        </button>
      ) : null}
      {showUsage ? <UsageLine /> : <span className="min-w-0 flex-1" />}
      <button
        type="button"
        onClick={onOpenPalette}
        aria-label={`Search and commands (${shortcutLabel})`}
        aria-keyshortcuts="Meta+K Control+K"
        className="flex h-7 flex-none items-center gap-2 rounded-lg border border-border bg-surface px-2 text-xs text-text-muted transition-colors hover:text-text"
      >
        <Search size={13} aria-hidden="true" />
        <span className="hidden sm:inline">Search</span>
        <kbd className="rounded border border-border bg-bg px-1 font-mono text-[10px] text-text-faint">{shortcutLabel}</kbd>
      </button>
      <ThemeSwitch value={theme} onChange={onTheme} />
      <button
        type="button"
        className="icon-btn"
        onClick={onToggleDock}
        aria-label={dockOpen ? "Hide side panel" : "Show side panel"}
        aria-expanded={dockOpen}
        title={dockOpen ? "Hide side panel" : "Show side panel"}
      >
        <PanelRight size={16} />
      </button>
    </header>
  );
}
