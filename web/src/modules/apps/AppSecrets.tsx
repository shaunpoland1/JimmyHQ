import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import type { ComponentType } from "react";
import { errorDetail } from "../../api";
import { secretsApi } from "./api";
import { appIdOfSection } from "./lib/secrets";
import { SecretRow } from "./SecretRow";
import { useInstalledApps } from "./useInstalledApps";
import { AppHosts } from "./AppHosts";

const CARD = "@container rounded-xl border border-border bg-surface";

function secretsKey(appId: string) {
  return ["apps", "secrets", appId] as const;
}

/**
 * The secrets an app declares, on that app's own Settings page: a field for each, with
 * its label and help from the manifest. The value is sent once and never shown again;
 * the page only ever says "Set" or "Not set". Owned by the apps module.
 */
export function AppSecrets({ appId }: { appId: string }) {
  const query = useQuery({ queryKey: secretsKey(appId), queryFn: () => secretsApi.list(appId), staleTime: 30_000 });
  const installed = useInstalledApps();
  const appName = installed.apps.find((app) => app.id === appId)?.name ?? appId;
  if (query.isPending || !query.data || query.data.length === 0) {
    if (query.isError) {
      return (
        <p className={`${CARD} px-4 py-3.5 text-sm text-text-muted`} role="alert">
          <span className="font-medium text-text">This app's secrets could not be read.</span> {errorDetail(query.error)}
        </p>
      );
    }
    return null;
  }
  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex items-center gap-3 px-4 py-3.5">
        <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
          <KeyRound size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-semibold text-text">Keys and tokens</h3>
          <p className="text-xs text-text-muted">Kept in JimmyHQ's secret store for {appName} only. Once saved, a secret is never shown again.</p>
        </div>
      </div>
      {query.data.map((state) => (
        <SecretRow
          key={state.name}
          appName={appName}
          state={state}
          queryKey={secretsKey(appId)}
          save={(typed) => secretsApi.set(appId, state.name, typed)}
          clear={() => secretsApi.clear(appId, state.name)}
        />
      ))}
    </div>
  );
}

const made = new Map<string, ComponentType>();

/**
 * The extra part of an app's own Settings page (`app.<id>`), or undefined for any other
 * page. One component per app, made once, so the page does not redraw it from scratch.
 */
export function appSettingsExtra(sectionId: string): ComponentType | undefined {
  const appId = appIdOfSection(sectionId);
  if (!appId) return undefined;
  let found = made.get(appId);
  if (!found) {
    const Bound = () => (
      <>
        <AppSecrets appId={appId} />
        <AppHosts appId={appId} />
      </>
    );
    Bound.displayName = `AppSecrets(${appId})`;
    made.set(appId, Bound);
    found = Bound;
  }
  return found;
}
