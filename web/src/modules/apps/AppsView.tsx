import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { parseHash } from "../../lib/route";
import { AppCard } from "./AppCard";
import { AppDetail } from "./AppDetail";
import { EmptyApps } from "./EmptyApps";
import { FirstPartyApps } from "./FirstPartyApps";
import { InstallDialog } from "./InstallDialog";
import { RemoveDialog } from "./RemoveDialog";
import { UpdateDialog } from "./UpdateDialog";
import type { AppSummary, InstallSource } from "./api";
import { openAppId } from "./lib/route";
import { useAppsLive, useAppsQuery, useSetEnabled } from "./queries";
import { ErrorCard, LoadingCard } from "./ui";

/** The app id in the address (`#/apps/<id>`), kept current as the address changes. */
function useOpenApp(): string | null {
  const [id, setId] = useState(() => openAppId(parseHash(window.location.hash), null));
  useEffect(() => {
    // Settings opens over this screen at its own address: keep the app that was open.
    const sync = () => setId((was) => openAppId(parseHash(window.location.hash), was));
    window.addEventListener("hashchange", sync);
    sync();
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  return id;
}

/** The Apps screen: what is installed, and how to install, switch off or remove an app. */
export function AppsView() {
  useAppsLive();
  const openId = useOpenApp();
  const apps = useAppsQuery();
  const setEnabled = useSetEnabled();
  const [installing, setInstalling] = useState<{ source?: InstallSource } | null>(null);
  const [removing, setRemoving] = useState<AppSummary | null>(null);
  const [updating, setUpdating] = useState<AppSummary | null>(null);

  const list = apps.data ?? [];
  const empty = apps.isSuccess && list.length === 0;
  const savingId = setEnabled.isPending ? setEnabled.variables?.app.id : undefined;
  const installButton = (
    <button type="button" className="btn btn-primary" onClick={() => setInstalling({})}>
      <Plus size={14} aria-hidden="true" />
      Install an app
    </button>
  );

  let body;
  if (openId) {
    body = <AppDetail id={openId} />;
  } else {
    body = (
      <>
        <header className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="min-w-0 flex-1 basis-60">
            <h1 className="text-lg font-semibold">Apps</h1>
            <p className="text-sm text-text-muted">
              Apps add screens, tiles and skills to JimmyHQ. Each one lists what it may touch before you install it, and you can switch it off or remove it at any time.
            </p>
          </div>
          {empty ? null : installButton}
        </header>
        {apps.isPending ? (
          <LoadingCard>Loading your apps…</LoadingCard>
        ) : apps.isError ? (
          <ErrorCard what="Your apps" error={apps.error} onRetry={() => void apps.refetch()} />
        ) : list.length === 0 ? (
          <EmptyApps onInstall={(source) => setInstalling({ source })} />
        ) : (
          <div className="flex flex-col gap-4">
            <ul className="flex flex-col gap-3" aria-label="Installed apps">
              {list.map((app) => (
                <AppCard key={app.id} app={app} saving={savingId === app.id} onToggle={(item, enabled) => setEnabled.mutate({ app: item, enabled })} onRemove={setRemoving} onUpdate={setUpdating} />
              ))}
            </ul>
            <FirstPartyApps installedIds={list.map((app) => app.id)} onInstall={(source) => setInstalling({ source })} />
          </div>
        )}
      </>
    );
  }

  return (
    <div className="@container w-full">
      <div className="max-w-[900px] px-3.5 pb-8 pt-4 @2xl:px-[26px] @2xl:pb-10 @2xl:pt-[22px]">{body}</div>
      {installing ? <InstallDialog initialSource={installing.source} onClose={() => setInstalling(null)} /> : null}
      {removing ? <RemoveDialog app={removing} onClose={() => setRemoving(null)} /> : null}
      {updating ? <UpdateDialog app={updating} onClose={() => setUpdating(null)} /> : null}
    </div>
  );
}
