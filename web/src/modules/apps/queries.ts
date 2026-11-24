// The apps module's cached server data, kept current from the live feed, and the
// actions that change it: switching an app on or off, updating, rolling back and removing it.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { ApiError, errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { appsApi } from "./api";
import type { AppDetail, AppSummary, KeptVersion, RemoveChoice, UpdatePlan } from "./api";
import { rolledBackToast, updatedToast } from "./lib/update";
import { staleAfter } from "./lib/events";
import { switchedToast } from "./lib/status";
import { restartedToast } from "./lib/health";
import type { HealthState } from "./lib/health";
import { subscribeTape } from "./live";

export const APPS_KEYS = {
  all: ["apps"] as const,
  list: ["apps", "list"] as const,
  firstParty: ["apps", "first-party"] as const,
  detail: (id: string) => ["apps", "detail", id] as const,
  health: (id: string) => ["apps", "health", id] as const,
};

/** How often the health panel asks again while it is open. */
export const HEALTH_POLL_MS = 5000;

/** How a full app's program is doing, kept fresh while it is shown. */
export function useAppHealth(id: string, enabled: boolean) {
  return useQuery({ queryKey: APPS_KEYS.health(id), queryFn: () => appsApi.health(id), enabled, refetchInterval: HEALTH_POLL_MS });
}

/** The owner's Restart: stops the program if it runs, and starts it again. */
export function useRestartApp() {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ app }: { app: Pick<AppSummary, "id" | "name">; was: HealthState }) => appsApi.restart(app.id),
    onSuccess: (_health, { app, was }) => toast.success(restartedToast(app.name, was)),
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: () => void client.invalidateQueries({ queryKey: APPS_KEYS.all }),
  });
}

/** Events arrive in bursts (an install prints several); wait this long before refetching. */
const SETTLE_MS = 300;

export function useAppsQuery() {
  return useQuery({ queryKey: APPS_KEYS.list, queryFn: appsApi.list });
}

/** The first-party apps this copy ships. They change only with a new version, so asked once. */
export function useFirstPartyQuery() {
  return useQuery({ queryKey: APPS_KEYS.firstParty, queryFn: appsApi.firstParty, staleTime: Infinity });
}

export function useAppQuery(id: string) {
  return useQuery({
    queryKey: APPS_KEYS.detail(id),
    queryFn: () => appsApi.detail(id),
    // An app that is not there will not appear by asking again.
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
}

let liveUsers = 0;
let stopLive: (() => void) | null = null;

function startLive(client: QueryClient): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const stop = subscribeTape({
    onEvent: (event) => {
      if (!staleAfter(event.kind) || timer !== null) return;
      timer = setTimeout(() => {
        timer = null;
        void client.invalidateQueries({ queryKey: APPS_KEYS.all });
      }, SETTLE_MS);
    },
    onReconnect: () => void client.invalidateQueries({ queryKey: APPS_KEYS.all }),
  });
  return () => {
    stop();
    if (timer !== null) clearTimeout(timer);
  };
}

/** Refetches the apps data when a live Tape event says an app changed. */
export function useAppsLive(): void {
  const client = useQueryClient();
  useEffect(() => {
    liveUsers += 1;
    if (liveUsers === 1) stopLive = startLive(client);
    return () => {
      liveUsers -= 1;
      if (liveUsers === 0) {
        stopLive?.();
        stopLive = null;
      }
    };
  }, [client]);
}

type EnableVars = { app: Pick<AppSummary, "id" | "name">; enabled: boolean; undo?: boolean };
type EnableContext = { previous: AppSummary[] | undefined; previousDetail: AppDetail | undefined };

/** Switches an app on or off at once, puts it back if the server refuses, and offers Undo. */
export function useSetEnabled() {
  const client = useQueryClient();
  const toast = useToast();

  const mutation = useMutation<AppSummary, unknown, EnableVars, EnableContext>({
    mutationFn: ({ app, enabled }) => (enabled ? appsApi.enable(app.id) : appsApi.disable(app.id)),
    onMutate: async ({ app, enabled }) => {
      await client.cancelQueries({ queryKey: APPS_KEYS.all });
      const previous = client.getQueryData<AppSummary[]>(APPS_KEYS.list);
      const previousDetail = client.getQueryData<AppDetail>(APPS_KEYS.detail(app.id));
      client.setQueryData<AppSummary[]>(APPS_KEYS.list, (data) => data?.map((item) => (item.id === app.id ? { ...item, enabled } : item)));
      client.setQueryData<AppDetail>(APPS_KEYS.detail(app.id), (data) => (data ? { ...data, enabled } : data));
      return { previous, previousDetail };
    },
    onError: (error, { app }, context) => {
      if (context?.previous) client.setQueryData(APPS_KEYS.list, context.previous);
      if (context?.previousDetail) client.setQueryData(APPS_KEYS.detail(app.id), context.previousDetail);
      toast.error(errorDetail(error));
    },
    onSuccess: (_result, { app, enabled, undo }) => {
      if (undo) toast.success(switchedToast(app, enabled));
      else toast.success(switchedToast(app, enabled), { label: "Undo", run: () => mutation.mutate({ app, enabled: !enabled, undo: true }) });
    },
    onSettled: () => void client.invalidateQueries({ queryKey: APPS_KEYS.all }),
  });
  return mutation;
}

/** Removes an app. The caller says whether its data is kept. */
export function useRemoveApp(onDone: () => void) {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ app, choice }: { app: Pick<AppSummary, "id" | "name">; choice: RemoveChoice }) => appsApi.remove(app.id, choice),
    onSuccess: (_result, { app, choice }) => {
      toast.success(choice.keepData ? `Removed ${app.name}. Its data is kept.` : `Removed ${app.name} and deleted its data.`);
      client.removeQueries({ queryKey: APPS_KEYS.detail(app.id) });
      onDone();
    },
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: () => void client.invalidateQueries({ queryKey: APPS_KEYS.all }),
  });
}

/** Puts a planned update in place. The plan came from `appsApi.planUpdate`. */
export function useConfirmUpdate(onDone: () => void) {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ plan, typed }: { plan: UpdatePlan; typed: string }) => appsApi.confirmUpdate(plan, typed),
    onSuccess: (app) => {
      toast.success(updatedToast(app.name, app.version));
      onDone();
    },
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: () => void client.invalidateQueries({ queryKey: APPS_KEYS.all }),
  });
}

/** Switches back to a kept version, with that version's data when the owner asks. */
export function useRollback(onDone: () => void) {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ app, choice, restore }: { app: Pick<AppSummary, "id" | "name">; choice: KeptVersion; restore: boolean }) =>
      appsApi.rollback(app.id, choice, restore),
    onSuccess: (app) => {
      toast.success(rolledBackToast(app.name, app.version));
      onDone();
    },
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: () => void client.invalidateQueries({ queryKey: APPS_KEYS.all }),
  });
}

/** Asks the server to look for a newer version now. */
export function useCheckUpdate() {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (app: Pick<AppSummary, "id" | "name">) => appsApi.checkUpdate(app.id),
    onSuccess: (found, app) => toast.success(found ? `Found a newer version of ${app.name}.` : `${app.name} is up to date, as far as JimmyHQ can tell.`),
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: () => void client.invalidateQueries({ queryKey: APPS_KEYS.all }),
  });
}
