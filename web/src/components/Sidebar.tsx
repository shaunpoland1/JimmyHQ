import { LogOut, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { useEffect } from "react";
import { useNavBadges } from "../hooks/useNavBadges";
import { appIcon } from "../modules/apps/appIcons";
import type { AppIconName } from "../modules/apps/lib/installed";
import { useInstalledApps } from "../modules/apps/useInstalledApps";
import { formatHash } from "../lib/route";
import { SETTINGS_SCREEN } from "../lib/screens";
import type { ScreenId, ScreenMeta } from "../lib/screens";
import { Logo } from "./Logo";
import { SCREEN_ICONS } from "./screenIcons";

type SidebarProps = {
  /** The main screens to list, already filtered and ordered by the owner's setting. */
  screens: readonly ScreenMeta[];
  current: ScreenId;
  /** The rest of the address, which says which app screen is open. */
  currentSub?: string | null;
  /** Icons-only mode (ignored when shown as a drawer). */
  collapsed: boolean;
  onToggleCollapsed: () => void;
  /** Narrow screens: render as an overlay drawer. */
  drawer: boolean;
  drawerOpen: boolean;
  onCloseDrawer: () => void;
  ownerName: string | null;
  onSignOut: () => void;
};

export function Sidebar({
  screens,
  current,
  currentSub = null,
  collapsed,
  onToggleCollapsed,
  drawer,
  drawerOpen,
  onCloseDrawer,
  ownerName,
  onSignOut,
}: SidebarProps) {
  const compact = collapsed && !drawer;
  const badges = useNavBadges();
  const installed = useInstalledApps();

  useEffect(() => {
    if (!drawer || !drawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseDrawer();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawer, drawerOpen, onCloseDrawer]);

  if (drawer && !drawerOpen) return null;

  const link = (screen: ScreenMeta, app?: { key: string; sub: string; title: string; badge: number | undefined; icon: AppIconName | null }) => {
    const Icon = app ? appIcon(app.icon) : SCREEN_ICONS[screen.id];
    const active = screen.id === current && (!app || app.sub === currentSub);
    const badge = app ? app.badge : badges[screen.id];
    const name = app ? app.title : screen.label;
    return (
      <li key={app ? app.key : screen.id}>
        <a
          href={formatHash({ screen: screen.id, sub: app ? app.sub : null })}
          aria-current={active ? "page" : undefined}
          aria-label={compact ? name : undefined}
          title={compact || app ? name : undefined}
          onClick={drawer ? onCloseDrawer : undefined}
          className={`flex h-8 items-center gap-2.5 rounded-lg text-[13px] transition-colors ${compact ? "justify-center px-0" : "px-2.5"} ${
            active ? "bg-surface font-medium text-text shadow-sm ring-1 ring-border" : "text-text-muted hover:bg-surface/60 hover:text-text"
          }`}
        >
          <Icon size={16} strokeWidth={1.75} className="flex-none" aria-hidden="true" />
          {compact ? null : <span className="min-w-0 flex-1 truncate">{screen.label}</span>}
          {!compact && badge ? (
            <span className="flex-none rounded-full bg-surface-2 px-1.5 font-mono text-[10px] text-text-muted ring-1 ring-border" aria-label={`${badge} waiting`}>
              {badge}
            </span>
          ) : null}
        </a>
      </li>
    );
  };

  const body = (
    <nav
      aria-label="Main"
      className={`flex h-full flex-none flex-col border-r border-border bg-bg ${compact ? "w-14" : "w-56"} ${
        drawer ? "relative z-10 w-64 max-w-[85vw] shadow-pop" : ""
      }`}
    >
      <div className={`flex h-11 flex-none items-center gap-2 ${compact ? "justify-center" : "pl-3 pr-2"}`}>
        {compact ? null : (
          <>
            <Logo size={22} />
            <span className="flex-1 truncate text-sm font-semibold tracking-tight">JimmyHQ</span>
          </>
        )}
        {drawer ? (
          <button type="button" className="icon-btn" onClick={onCloseDrawer} aria-label="Close navigation" autoFocus>
            <X size={16} />
          </button>
        ) : compact ? (
          // Collapsed: the logo stays visible and doubles as the way back.
          // It turns into the expand icon when pointed at or focused.
          <button
            type="button"
            className="group icon-btn"
            onClick={onToggleCollapsed}
            aria-label="Expand sidebar"
            aria-expanded={false}
            title="Expand sidebar"
          >
            <Logo size={22} className="group-hover:hidden group-focus-visible:hidden" />
            <PanelLeftOpen size={16} className="hidden group-hover:block group-focus-visible:block" />
          </button>
        ) : (
          <button
            type="button"
            className="icon-btn"
            onClick={onToggleCollapsed}
            aria-label="Collapse sidebar"
            aria-expanded
            title="Collapse sidebar"
          >
            <PanelLeftClose size={16} />
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        <ul className="flex flex-col gap-0.5">{screens.map((screen) => link(screen))}</ul>
        <hr className="my-3 border-border" />
        <section aria-label="Apps">
          {compact ? null : (
            <h2 className="px-2.5 text-[11px] font-medium uppercase tracking-wider text-text-faint">Apps</h2>
          )}
          {installed.items.length > 0 ? (
            <ul className={`flex flex-col gap-0.5 ${compact ? "" : "mt-1.5"}`}>
              {installed.items.map((item) => link({ id: "app", label: item.label, description: "" }, item))}
            </ul>
          ) : compact ? null : (
            <p className="mt-1.5 px-2.5 text-xs text-text-faint">No apps installed yet.</p>
          )}
        </section>
      </div>

      <div className="flex-none border-t border-border p-2">
        <ul>{link(SETTINGS_SCREEN)}</ul>
        <div className={`mt-1 flex items-center gap-2 ${compact ? "justify-center" : "pl-2.5"}`}>
          {compact ? null : (
            <span className="min-w-0 flex-1 truncate text-xs text-text-muted" title={ownerName ?? undefined}>
              {ownerName ?? "Signed in"}
            </span>
          )}
          <button type="button" className="icon-btn" onClick={onSignOut} aria-label="Sign out" title="Sign out">
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </nav>
  );

  if (!drawer) return body;

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="absolute inset-0 bg-black/35" onClick={onCloseDrawer} aria-hidden="true" />
      {body}
    </div>
  );
}
