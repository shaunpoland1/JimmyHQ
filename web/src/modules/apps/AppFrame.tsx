import { AppWindow, RotateCw } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { errorDetail, request } from "../../api";
import { EmptyState } from "../../components/EmptyState";
import { useTheme } from "../../hooks/useTheme";
import { useToast } from "../../hooks/useToast";
import { subscribeFeed } from "../../lib/feed";
import { formatHash } from "../../lib/route";
import {
  BRIDGE_VERSION,
  FRAME_ALLOW,
  FRAME_REFERRER_POLICY,
  FRAME_SANDBOX,
  badgeCount,
  bridgeBody,
  bridgePath,
  createRelay,
  eventFromFeed,
  isFromFrame,
  isHello,
  mountPath,
  readMount,
  readyEvent,
  themeEvent,
  toastText,
  visibleEvent,
} from "./lib/bridge";
import type { BridgeEvent, Mounted } from "./lib/bridge";
import { DescribedScreen } from "./described";
import { findTarget } from "./lib/installed";
import { ownSettingsTarget } from "./lib/ownSettings";
import { APPS_PATHS } from "./lib/requests";
import { appsApi, fullPictureBlob, pictureBlob, uploadPicture } from "./api";
import { createQueue, noGestureRefusal, readUploadArgs, uploadAllowed } from "./lib/uploads";
import { browserSaveEnv, browserThumbEnv } from "./browserEnv";
import { deliver, newLedger, newRun, noteSaved, readSaveArgs, recordSave, savedLine, savesInMinute } from "./lib/saving";
import { readThumbArgs } from "./lib/thumbnail";
import { readPictureArgs, takePicture } from "./lib/picture";
import { createTouch, focusEntered, hint, observe, shellInput } from "./lib/touch";
import { availability, dictateRefusal, readDictateArgs } from "./lib/dictate";
import { DESK, appRecorder, browserEnvironment, speechStatus } from "./dictation";
import { DictateSurface } from "./DictateSurface";
import { OPEN_RECORDINGS } from "../speech/lib/open";
import type { Activation } from "./lib/uploads";
import { EMITTED_POLL_MS } from "./lib/emitted";
import { reloadsFrame } from "./lib/events";
import type { AppScreen as Screen, InstalledApp } from "./lib/installed";
import { releaseAppBadge, setAppBadge, useInstalledApps } from "./useInstalledApps";

/** What the browser says about the owner's touch, where it can say. */
// Every app's saved files of the last minute, and which apps have saved a web page, for
// this tab: shared by all frames of an app, so reloading its page does not reset the count.
const SAVES = newLedger();

function activation(): Activation {
  const found = (navigator as Navigator & { userActivation?: { isActive: boolean; hasBeenActive: boolean } }).userActivation;
  return found ? { isActive: found.isActive, hasBeenActive: found.hasBeenActive } : null;
}

/** Sends one bridge call to the server. The path is built from the shell's own app id and a listed call. */
function send(appId: string, call: string, args: unknown): Promise<unknown> {
  const path = bridgePath(appId, call);
  if (!path) return Promise.reject(new Error("Not a bridge call."));
  return request<unknown>("POST", path, { body: bridgeBody(call, args) });
}

type Mount = { state: "asking" } | { state: "ready"; mounted: Mounted } | { state: "failed"; message: string };

/** Asks the server for a mount: a random address under which this app version's files are served. */
function useMount(app: InstalledApp, screen: Screen, attempt: number): Mount {
  const [mount, setMount] = useState<Mount>({ state: "asking" });
  useEffect(() => {
    const path = mountPath(app.id);
    if (!path) return;
    const controller = new AbortController();
    setMount({ state: "asking" });
    request<unknown>("POST", path, { body: { screen: screen.id }, signal: controller.signal })
      .then((reply) => {
        const mounted = readMount(reply);
        setMount(mounted ? { state: "ready", mounted } : { state: "failed", message: "JimmyHQ could not open this app's files. Nothing was loaded. Try again, or reinstall the app." });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setMount({ state: "failed", message: errorDetail(error) });
      });
    return () => controller.abort();
  }, [app.id, app.version, screen.id, attempt]);
  return mount;
}

type AppFrameProps = { app: InstalledApp; screen: Screen };

/**
 * One app screen in a sandboxed frame, and the relay between it and the server.
 *
 * The frame has an opaque origin: no cookies, no reach into this page, no
 * popups, no top navigation. It gets one MessagePort and everything it says
 * arrives on that port, where `createRelay` checks it. The shell listens for
 * a single window message, the frame's "hello", and only from the frame's own
 * window. Nothing the app sends is ever put into this page as markup, run as
 * code or used as an address.
 */
export function AppFrame({ app, screen }: AppFrameProps) {
  const [attempt, setAttempt] = useState(0);
  const [gone, setGone] = useState(false);
  const mount = useMount(app, screen, attempt);
  const mounted = mount.state === "ready" ? mount.mounted : null;

  const frameRef = useRef<HTMLIFrameElement>(null);
  const portRef = useRef<MessagePort | null>(null);
  const toast = useToast();
  const theme = useTheme().resolved;
  const client = useQueryClient();
  // Names this screen to the dictation desk: only the frame that asked draws the surface.
  const [owner] = useState(() => ({}));

  // The newest values, read when the frame connects; a change alone never remounts it.
  const live = useRef({ app, theme, toast, client });
  live.current = { app, theme, toast, client };

  const emit = useCallback((event: BridgeEvent) => {
    try {
      portRef.current?.postMessage(event);
    } catch {
      // The frame is gone.
    }
  }, []);

  useLayoutEffect(() => {
    const frame = frameRef.current;
    if (!frame || !mounted) return;
    // What the owner granted and what the page is told come from the mount's answer.
    const told = {
      id: app.id,
      version: mounted.version,
      permissions: mounted.grant.permissions,
      // An owner who has set no time zone uses this computer's.
      timezone: mounted.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    let port: MessagePort | null = null;
    let loaded = false;
    let over = false;
    // When the owner last touched this app's page, as far as the shell can tell
    // (lib/touch.ts): an activation while the frame holds the focus and the shell has had
    // no click or key of its own since, one touch per activation.
    const touch = createTouch();
    const noteTouch = () => observe(touch, activation(), document.activeElement === frame, Date.now());
    // When a picture last arrived safely. It renews the window for the next one of the
    // same batch, within the limit `uploadAllowed` keeps from the touch itself.
    let renewedAt: number | null = null;
    // The files saved with ui.save since the owner's last touch: one touch may save a few
    // in a row, up to the owner's limit (lib/saving.ts).
    let saved = newRun();
    // When the page last began a dictation: one touch is one dictation.
    let dictatedAt: number | null = null;
    const dictating = { id: app.id, private: mounted.private, maxSeconds: mounted.dictateMaxS };
    const queue = createQueue();
    // Full views (`files.picture`): when each was asked, and their own one-at-a-time queue.
    let fullViews: number[] = [];
    const pictureQueue = createQueue();

    const relay = createRelay({
      appId: app.id,
      grant: mounted.grant,
      send,
      post: (response) => port?.postMessage(response),
      shell: {
        "ui.toast": (args) => {
          live.current.toast.show("info", toastText(live.current.app.name, args));
          return null;
        },
        "ui.badge": (args) => {
          setAppBadge(app.id, badgeCount(args));
          return null;
        },
        // The owner picked files in the page: the shell sends each, one at a time, for the
        // app it mounted. The frame never names the app or the address.
        "files.upload": (args) => {
          const read = readUploadArgs(args, mounted.uploadMaxBytes);
          if (!read.ok) throw read.error;
          if (!uploadAllowed(activation(), touch.touchedAt, renewedAt, Date.now())) throw noGestureRefusal();
          return queue(() => uploadPicture(app.id, read.file)).then((kept) => {
            renewedAt = Date.now();
            return kept;
          });
        },
        // One of this app's pictures, made small in the shell (lib/thumbnail.ts).
        "files.thumbnail": (args) => {
          const read = readThumbArgs(args, mounted.thumbnailPx);
          if (!read.ok) throw read.error;
          return pictureBlob(app.id, read.ref, read.px, browserThumbEnv());
        },
        // The same picture large, for a full view: counted against the owner's rate for
        // this screen, read with a cap on its bytes, made one at a time (lib/picture.ts).
        "files.picture": (args) => {
          const read = readPictureArgs(args, mounted.picture.px);
          if (!read.ok) throw read.error;
          const taken = takePicture(fullViews, Date.now(), mounted.picture.perMinute);
          fullViews = taken.log;
          if (!taken.ok) throw taken.error;
          return pictureQueue(() => fullPictureBlob(app.id, read.ref, read.px, mounted.picture.maxBytes, browserThumbEnv()));
        },
        // A file for the owner to keep, on the owner's touch; the shell makes the Blob
        // and the save, never the frame (lib/saving.ts).
        "ui.save": (args) => {
          const now = Date.now();
          const read = readSaveArgs(args, touch.touchedAt, saved, now, mounted.save, savesInMinute(SAVES, app.id, now));
          if (!read.ok) throw read.error;
          saved = recordSave(saved, touch.touchedAt ?? now, now);
          const firstPage = noteSaved(SAVES, app.id, read.save, now);
          deliver(read.save, browserSaveEnv());
          // The owner sees each file as it goes: what it is called and how big it is.
          live.current.toast.show("info", savedLine(live.current.app.name, read.save, saved.count, mounted.save.maxParts, firstPage));
          return { name: read.save.name, bytes: read.save.bytes };
        },
        // Only this app's own page under Settings, and only on the owner's touch.
        "ui.settings": () => {
          const target = ownSettingsTarget(live.current.app, activation(), touch.touchedAt, Date.now());
          if (!target.ok) throw target.error;
          window.location.hash = target.hash;
          return null;
        },
        "files.picking": () => {
          noteTouch();
          return null;
        },
        // The page says the owner just clicked or pressed a key in it. That is only a nudge to
        // look at the browser's activation now: the page's word is never proof of a new touch,
        // since any page can say it without a click (lib/touch.ts).
        "ui.touch": () => {
          hint(touch, activation(), document.activeElement === frame, Date.now());
          return null;
        },
        // The owner dictates on the shell's surface (DictateSurface); the page gets only
        // `{text}`, once the words are ready (lib/dictate.ts).
        "speech.dictate": (args) => {
          const read = readDictateArgs(args);
          if (!read.ok) throw read.error;
          const now = Date.now();
          const refusal = dictateRefusal(touch.touchedAt, dictatedAt, now, OPEN_RECORDINGS.count());
          if (refusal) throw refusal;
          const { client: queries, toast: toasts, app: shown } = live.current;
          const answer = DESK.open(
            { owner, appName: shown.name, hint: read.hint, maxSeconds: dictating.maxSeconds },
            appRecorder(dictating, queries, (kind, message, action) => toasts.show(kind, message, action)),
          );
          dictatedAt = now;
          return answer;
        },
        "speech.available": async () => availability(await speechStatus(live.current.client), dictating, browserEnvironment()),
      },
    });

    const connect = () => {
      const target = frame.contentWindow;
      if (!target || port) return;
      const channel = new MessageChannel();
      port = channel.port1;
      portRef.current = port;
      port.onmessage = (event) => {
        noteTouch();
        void relay.handle(event.data);
      };
      // An opaque origin cannot be named, so the target is "*". The message
      // holds nothing but the port, and only this frame's window receives it.
      target.postMessage({ jimmyhq: BRIDGE_VERSION, port: channel.port2 }, "*", [channel.port2]);
      port.postMessage(readyEvent(told, live.current.theme, told.timezone, mounted.weekStart, mounted.save));
    };

    const end = () => {
      over = true;
      // A page that goes away takes its dictation with it: the microphone is let go.
      DESK.cancel(owner);
      relay.close();
      port?.close();
      port = null;
      portRef.current = null;
    };

    // The first load is the app's page. A second load means the page moved
    // itself somewhere else: whatever is there now is not what was mounted,
    // so the channel is closed and the frame is taken down.
    const onLoad = () => {
      if (over) return;
      if (loaded) {
        end();
        setGone(true);
        return;
      }
      loaded = true;
      connect();
    };

    // The page may say hello before it has finished loading. Only this
    // frame's own window is heard, only once, and never after the first load.
    const onMessage = (event: MessageEvent) => {
      if (over || loaded || port) return;
      if (!isFromFrame(event, frame.contentWindow) || !isHello(event.data)) return;
      connect();
    };

    // The owner clicking into the frame takes the focus from this page.
    const onBlur = () => {
      if (document.activeElement === frame) focusEntered(touch, activation(), Date.now());
    };
    // A click or key the shell's own page hears was not in the frame: events inside the
    // frame never reach this document.
    const onShellInput = () => shellInput(touch, Date.now());

    frame.addEventListener("load", onLoad);
    window.addEventListener("message", onMessage);
    window.addEventListener("blur", onBlur);
    window.addEventListener("pointerdown", onShellInput, true);
    window.addEventListener("keydown", onShellInput, true);
    return () => {
      frame.removeEventListener("load", onLoad);
      window.removeEventListener("message", onMessage);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("pointerdown", onShellInput, true);
      window.removeEventListener("keydown", onShellInput, true);
      end();
      // The page's number stays on the sidebar after the owner leaves the app only when the
      // app's program says none of its own; otherwise the program's number shows again.
      releaseAppBadge(app.id);
    };
  }, [mounted, app.id, owner]);

  useEffect(() => emit(themeEvent(theme)), [theme, emit]);

  useEffect(() => {
    const onVisible = () => emit(visibleEvent(document.visibilityState === "visible"));
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [emit]);

  useEffect(
    () =>
      subscribeFeed({
        onEvent: (event) => {
          if (reloadsFrame(app.id, event)) {
            setGone(false);
            setAttempt((count) => count + 1);
            return;
          }
          const forFrame = eventFromFeed(app.id, event);
          if (forFrame) emit(forFrame);
        },
      }),
    [app.id, emit],
  );

  // A full app's program can nudge its own open pages (`app.emit`). Only while this frame
  // is connected, and only this app's events reach it.
  useEffect(() => {
    if (app.tier !== "full" || !mounted) return;
    let after = -1;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const ask = async () => {
      try {
        const found = await appsApi.emitted(app.id, after);
        if (stopped) return;
        if (after >= 0) found.events.forEach(emit);
        after = found.next;
      } catch {
        // Asked again at the next turn.
      }
      if (!stopped) timer = setTimeout(() => void ask(), EMITTED_POLL_MS);
    };
    void ask();
    return () => {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [app.id, app.tier, mounted, emit]);

  const reopen = () => {
    setGone(false);
    setAttempt((count) => count + 1);
  };

  const title = screen.label === app.name ? app.name : `${app.name}: ${screen.label}`;
  let body;
  if (gone) {
    body = (
      <EmptyState icon={AppWindow} title={`${app.name} left its page`} description="The app tried to go somewhere else, so JimmyHQ closed it. Nothing was sent. You can open it again.">
        <button type="button" className="btn" onClick={reopen}>
          Open again
        </button>
      </EmptyState>
    );
  } else if (mount.state === "failed") {
    body = (
      <EmptyState icon={AppWindow} title={`${app.name} did not open`} description={mount.message}>
        <button type="button" className="btn" onClick={reopen}>
          Try again
        </button>
      </EmptyState>
    );
  } else if (mount.state === "asking") {
    body = (
      <p className="p-6 text-sm text-text-muted" role="status">
        Opening {app.name}…
      </p>
    );
  } else {
    body = (
      <iframe
        // A new frame for every mount: nothing carries over from the last one.
        key={mount.mounted.src}
        ref={frameRef}
        src={mount.mounted.src}
        title={title}
        sandbox={FRAME_SANDBOX}
        allow={FRAME_ALLOW}
        referrerPolicy={FRAME_REFERRER_POLICY}
        className="min-h-0 w-full flex-1 border-0 bg-bg"
      />
    );
  }

  return (
    <AppStrip title={title} appName={app.name} onReload={reopen}>
      {body}
      <DictateSurface owner={owner} />
    </AppStrip>
  );
}

/** The strip above every app screen. It is drawn by JimmyHQ, never by the app: it always says whose screen this is. */
function AppStrip({ title, appName, onReload, children }: { title: string; appName: string; onReload: () => void; children: ReactNode }) {
  return (
    <section aria-label={title} className="relative flex h-full min-h-0 flex-col">
      <header className="flex h-9 flex-none items-center gap-2 border-b border-border bg-surface px-3">
        <AppWindow size={14} strokeWidth={1.75} className="flex-none text-text-faint" aria-hidden="true" />
        <h1 className="min-w-0 truncate text-[13px] font-medium text-text">{title}</h1>
        <span className="flex-none rounded-full bg-surface-2 px-1.5 text-[10px] uppercase tracking-wider text-text-muted ring-1 ring-border">App</span>
        <span className="flex-1" />
        <button type="button" className="icon-btn" onClick={onReload} aria-label={`Reload ${appName}`} title="Reload">
          <RotateCw size={14} />
        </button>
      </header>
      {children}
    </section>
  );
}

/**
 * A described screen. There is no frame: a described app has no code of its own, only
 * tables and views, so there is nothing to sandbox. JimmyHQ's own renderer draws it here
 * in the shell and shows every value as text. Its records still go through the bridge,
 * as `app:<id>`, under the same permissions and limits as any app.
 */
export function DescribedAppScreen({ app, screen }: AppFrameProps) {
  const query = useQuery({
    queryKey: ["apps", "described", app.id, app.version],
    queryFn: () => request<unknown>("GET", APPS_PATHS.described(app.id)),
    staleTime: 60_000,
  });
  const title = screen.label === app.name ? app.name : `${app.name}: ${screen.label}`;
  let body;
  if (query.isPending) {
    body = (
      <p className="p-6 text-sm text-text-muted" role="status">
        Opening {app.name}…
      </p>
    );
  } else if (query.isError) {
    body = (
      <EmptyState icon={AppWindow} title={`${app.name} did not open`} description={errorDetail(query.error)}>
        <button type="button" className="btn" onClick={() => void query.refetch()}>
          Try again
        </button>
      </EmptyState>
    );
  } else {
    body = (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <DescribedScreen appId={app.id} description={query.data} />
      </div>
    );
  }
  return (
    <AppStrip title={title} appName={app.name} onReload={() => void query.refetch()}>
      {body}
    </AppStrip>
  );
}

const APPS_HREF = formatHash({ screen: "apps", sub: null });

/** What `#/app/<id>/<screen>` shows: the app's frame, a described app's records, or a plain sentence when there is nothing to show. */
export function AppScreen({ sub }: { sub: string | null }) {
  const installed = useInstalledApps();
  if (installed.loading) {
    return (
      <p className="p-6 text-sm text-text-muted" role="status">
        Loading…
      </p>
    );
  }
  const manage = (
    <a className="btn" href={APPS_HREF}>
      Go to Apps
    </a>
  );
  if (installed.failed) {
    return (
      <EmptyState icon={AppWindow} title="Apps could not be loaded" description="JimmyHQ could not read the list of installed apps. Nothing was opened. Check that the server is running and try again." level={1}>
        <button type="button" className="btn" onClick={installed.retry} disabled={installed.retrying}>
          {installed.retrying ? "Trying…" : "Try again"}
        </button>
        {manage}
      </EmptyState>
    );
  }
  const target = findTarget(installed.apps, sub);
  switch (target.state) {
    case "ok": {
      const key = `${target.app.id}/${target.app.version}/${target.screen.id}`;
      return target.screen.described ? (
        <DescribedAppScreen key={key} app={target.app} screen={target.screen} />
      ) : (
        <AppFrame key={key} app={target.app} screen={target.screen} />
      );
    }
    case "disabled":
      return (
        <EmptyState icon={AppWindow} title={`${target.app.name} is switched off`} description="Nothing from this app is running. Switch it on in Apps to use it again." level={1}>
          {manage}
        </EmptyState>
      );
    case "no_screen":
      return (
        <EmptyState icon={AppWindow} title="No such screen" description={`${target.app.name} does not have this screen. Pick one of its screens from the sidebar.`} level={1}>
          {manage}
        </EmptyState>
      );
    case "missing":
      return (
        <EmptyState icon={AppWindow} title="This app is not installed" description="There is no app at this address. It may have been removed. You can see what is installed in Apps." level={1}>
          {manage}
        </EmptyState>
      );
  }
}
