import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useHashRoute } from "../hooks/useHashRoute";
import { useIsNarrow } from "../hooks/useMediaQuery";
import { parseBoolean, parseNumber, usePersistentState } from "../hooks/usePersistentState";
import { SETTINGS_QUERY_KEY, useSettingActions, useSettingsQuery } from "../hooks/useSettings";
import { useTape } from "../hooks/useTape";
import { useTheme } from "../hooks/useTheme";
import { DOCK_DEFAULT_WIDTH, DOCK_TABS, clampDockWidth, parseDockTab } from "../lib/dock";
import { pickOrdered, settingFlag } from "../lib/layout";
import { settingsChangedSince } from "../lib/settingsFile";
import type { DockTab } from "../lib/dock";
import { DEFAULT_SCREEN } from "../lib/screens";
import type { Route } from "../lib/route";
import { AppScreen } from "../modules/apps/AppFrame";
import { NAV_SCREENS, SETTINGS_SCREEN } from "../lib/screens";
import { THEME_PREFS, THEME_SETTING_KEY, parseThemePref, themeLabel } from "../lib/theme";
import type { ThemePref } from "../lib/theme";
import { AppsScreen } from "../screens/AppsScreen";
import { BenchScreen } from "../screens/BenchScreen";
import { ChatScreen } from "../screens/ChatScreen";
import { MemoryScreen } from "../screens/MemoryScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
import { TasksScreen } from "../screens/TasksScreen";
import { VillageScreen } from "../screens/VillageScreen";
import { CommandPalette } from "./CommandPalette";
import { Modal } from "./Modal";
import type { PaletteCommand } from "./CommandPalette";
import { ConfirmDialog } from "./ConfirmDialog";
import { RightDock } from "./RightDock";
import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import { TopStrip } from "./TopStrip";

type ShellProps = { ownerName: string | null; onSignOut: () => Promise<void> };

const IS_APPLE = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const SHORTCUT_LABEL = IS_APPLE ? "⌘K" : "Ctrl K";

/** The signed-in app: sidebar, top strip, current screen, right dock, status bar. */
export function Shell({ ownerName, onSignOut }: ShellProps) {
  const [route, navigate] = useHashRoute();
  const narrow = useIsNarrow();
  const theme = useTheme();
  const settings = useSettingsQuery();
  const { save } = useSettingActions();
  const tape = useTape();

  const [collapsed, setCollapsed] = usePersistentState("jimmyhq.sidebar.collapsed", false, parseBoolean);
  const [dockOpen, setDockOpen] = usePersistentState("jimmyhq.dock.open", true, parseBoolean);
  const [dockWidth, setDockWidth] = usePersistentState("jimmyhq.dock.width", DOCK_DEFAULT_WIDTH, (stored) => {
    const width = parseNumber(stored);
    return width === undefined ? undefined : clampDockWidth(width);
  });
  const [dockTab, setDockTab] = usePersistentState<DockTab>("jimmyhq.dock.tab", "tape", parseDockTab);

  // On narrow screens the sidebar and dock are overlays that start closed.
  const [navDrawer, setNavDrawer] = useState(false);
  const [dockDrawer, setDockDrawer] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  // Layout the owner controls from Settings. Until settings load, show everything.
  const settingValue = (key: string) => settings.data?.settings.find((setting) => setting.key === key)?.value;
  const sidebarOrder = settingValue("appearance.sidebar_order");
  const dockPanels = settingValue("appearance.dock_panels");
  const navScreens = useMemo(() => pickOrdered(NAV_SCREENS, sidebarOrder), [sidebarOrder]);
  const dockTabs = useMemo(() => pickOrdered(DOCK_TABS, dockPanels), [dockPanels]);
  const showUsage = settingFlag(settingValue("appearance.show_usage_strip"), true);
  const showStatusBar = settingFlag(settingValue("statusbar.show"), true);
  const activeDockTab = dockTabs.some((item) => item.id === dockTab) ? dockTab : dockTabs[0]?.id;

  // Keep the usage line in step with the live feed.
  const client = useQueryClient();
  const newestSeq = tape.events[0]?.seq;
  useEffect(() => {
    if (newestSeq === undefined) return;
    // A burst of events (a reply being written, a busy agent) is one refetch.
    const timer = setTimeout(() => void client.invalidateQueries({ queryKey: ["tape", "stats"] }), 400);
    return () => clearTimeout(timer);
  }, [newestSeq, client]);

  // A setting changed elsewhere (the command line, another tab, an import) shows up here.
  const seenSeq = useRef<number | null>(null);
  useEffect(() => {
    if (newestSeq === undefined) return;
    const before = seenSeq.current;
    seenSeq.current = newestSeq;
    if (before === null || !settingsChangedSince(tape.events, before)) return;
    const timer = setTimeout(() => void client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY }), 400);
    return () => clearTimeout(timer);
    // `tape.events` is read only when a newer event has arrived.
  }, [newestSeq, client]);

  // The theme switch reads and writes the `appearance.theme` setting; the
  // localStorage copy only prevents a flash before settings load.
  const themeSetting = settings.data?.settings.find((setting) => setting.key === THEME_SETTING_KEY);
  const serverPref = parseThemePref(themeSetting?.value);
  const hasThemeSetting = themeSetting !== undefined;
  const { setPref } = theme;
  useEffect(() => {
    if (serverPref) setPref(serverPref);
  }, [serverPref, setPref]);
  const changeTheme = useCallback(
    (pref: ThemePref) => {
      setPref(pref);
      if (hasThemeSetting) void save(THEME_SETTING_KEY, pref);
    },
    [setPref, hasThemeSetting, save],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const dockVisible = activeDockTab !== undefined && (narrow ? dockDrawer : dockOpen);
  const toggleDock = useCallback(() => {
    if (narrow) setDockDrawer((open) => !open);
    else setDockOpen((open) => !open);
  }, [narrow, setDockOpen]);
  const closeDock = useCallback(() => {
    if (narrow) setDockDrawer(false);
    else setDockOpen(false);
  }, [narrow, setDockOpen]);
  const closeNav = useCallback(() => setNavDrawer(false), []);

  const commands = useMemo<PaletteCommand[]>(
    () => [
      ...[...navScreens, SETTINGS_SCREEN].map((screen) => ({
        id: `go:${screen.id}`,
        title: `Go to ${screen.label}`,
        group: "Navigate",
        keywords: [screen.id, "open", "screen"],
        run: () => navigate({ screen: screen.id, sub: null }),
      })),
      ...THEME_PREFS.map((pref) => ({
        id: `theme:${pref}`,
        title: `Theme: ${themeLabel(pref)}`,
        group: "Theme",
        keywords: ["appearance", "colour", "color", "mode", pref],
        run: () => changeTheme(pref),
      })),
      {
        id: "view:dock",
        title: "Toggle side panel",
        group: "View",
        keywords: ["dock", "today", "tape", "assistant"],
        run: toggleDock,
      },
      {
        id: "view:sidebar",
        title: "Toggle sidebar",
        group: "View",
        keywords: ["navigation", "collapse", "expand"],
        run: () => (narrow ? setNavDrawer((open) => !open) : setCollapsed((value) => !value)),
      },
      { id: "account:sign-out", title: "Sign out", group: "Account", keywords: ["log out", "logout"], run: () => setConfirmSignOut(true) },
    ],
    [navScreens, navigate, changeTheme, toggleDock, narrow, setCollapsed],
  );

  const signOut = async () => {
    setSigningOut(true);
    try {
      await onSignOut();
    } finally {
      setSigningOut(false);
      setConfirmSignOut(false);
    }
  };

  // Settings is a dialog, so the screen behind it is the last one visited.
  // An app's screen is kept whole, so the same app is still there afterwards.
  const [behind, setBehind] = useState<Route>({ screen: DEFAULT_SCREEN, sub: null });
  const settingsOpen = route.screen === "settings";
  useEffect(() => {
    if (route.screen !== "settings") setBehind({ screen: route.screen, sub: route.screen === "app" ? route.sub : null });
  }, [route.screen, route.sub]);
  const closeSettings = useCallback(() => navigate(behind), [navigate, behind]);
  const shown = settingsOpen ? behind : route;

  let screen;
  switch (shown.screen) {
    case "bench":
      screen = <BenchScreen />;
      break;
    case "tasks":
      screen = <TasksScreen />;
      break;
    case "chat":
      screen = <ChatScreen />;
      break;
    case "village":
      screen = <VillageScreen />;
      break;
    case "memory":
      screen = <MemoryScreen />;
      break;
    case "apps":
      screen = <AppsScreen />;
      break;
    case "app":
      screen = <AppScreen sub={shown.sub} />;
      break;
    case "settings":
      screen = null;
      break;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-bg text-text">
      <a
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[70] focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:shadow-pop"
      >
        Skip to content
      </a>
      <div className="flex min-h-0 flex-1">
        <Sidebar
          screens={navScreens}
          current={shown.screen}
          currentSub={shown.sub}
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((value) => !value)}
          drawer={narrow}
          drawerOpen={navDrawer}
          onCloseDrawer={closeNav}
          ownerName={ownerName}
          onSignOut={() => setConfirmSignOut(true)}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopStrip
            showUsage={showUsage}
            narrow={narrow}
            onOpenNav={() => setNavDrawer(true)}
            onOpenPalette={() => setPaletteOpen(true)}
            shortcutLabel={SHORTCUT_LABEL}
            theme={theme.pref}
            onTheme={changeTheme}
            dockOpen={dockVisible}
            onToggleDock={toggleDock}
          />
          <div className="flex min-h-0 flex-1">
            <main id="main" tabIndex={-1} className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden outline-none">
              {screen}
            </main>
            {dockVisible && activeDockTab ? (
              <RightDock
                tabs={dockTabs}
                overlay={narrow}
                width={dockWidth}
                onWidth={setDockWidth}
                tab={activeDockTab}
                onTab={setDockTab}
                onClose={closeDock}
                tape={tape}
              />
            ) : null}
          </div>
        </div>
      </div>
      {showStatusBar ? <StatusBar /> : null}
      {settingsOpen ? (
        <Modal
          title="Settings"
          onClose={closeSettings}
          panelClass="h-[80vh] w-[80vw] max-w-[1200px] max-sm:h-full max-sm:w-full max-sm:rounded-none"
        >
          <div className="min-h-0 flex-1 overflow-y-auto bg-bg">
            <SettingsScreen sectionId={route.sub} />
          </div>
        </Modal>
      ) : null}
      {paletteOpen ? <CommandPalette commands={commands} onClose={() => setPaletteOpen(false)} /> : null}
      {confirmSignOut ? (
        <ConfirmDialog
          title="Sign out?"
          message="You will need your password to sign back in."
          confirmLabel="Sign out"
          busy={signingOut}
          onConfirm={() => void signOut()}
          onCancel={() => setConfirmSignOut(false)}
        />
      ) : null}
    </div>
  );
}
