// Every address of the apps API and the body each request carries, in one place. Pure, so
// the seam with the server is tested (web/tests/apps.requests.test.ts). The table itself is
// in docs/apps.md and server/jimmyhq/apps/routes.py.
//
// Words about all apps start with an underscore, which an app's id never can, so no app
// can take the place of one of them.

import type { InstallPlan, InstallSource, RemoveChoice } from "../api.ts";

const APP_ID = /^[a-z][a-z0-9_]{1,30}$/;

/** No app has this name (an id cannot start with an underscore), so the server answers "no such app". */
export const NO_APP = "_none";

function app(id: string): string {
  // Only an id is ever put in an address. Anything else is not encoded and sent: it is not sent.
  return `/api/apps/${APP_ID.test(id) ? id : NO_APP}`;
}

export const APPS_PATHS = {
  /** Anyone who can read: what the shell draws. */
  shell: "/api/apps",
  installed: "/api/apps/_installed",
  examples: "/api/apps/_examples",
  plan: "/api/apps/_install/plan",
  confirm: "/api/apps/_install/confirm",
  cancel: "/api/apps/_install/cancel",
  detail: (id: string) => app(id),
  described: (id: string) => `${app(id)}/described`,
  enable: (id: string) => `${app(id)}/enable`,
  disable: (id: string) => `${app(id)}/disable`,
  remove: (id: string) => `${app(id)}/remove`,
  /** Owner only: how a full app's program is, its jobs and its last lines. */
  health: (id: string) => `${app(id)}/health`,
  restart: (id: string) => `${app(id)}/restart`,
  /** Owner only: stage a new version (step one), put it in place (step two), look for one now. */
  updatePlan: (id: string) => `${app(id)}/update/plan`,
  updateConfirm: (id: string) => `${app(id)}/update/confirm`,
  updateCheck: (id: string) => `${app(id)}/update/check`,
  rollback: (id: string) => `${app(id)}/rollback`,
} as const;

/** Step one's body: exactly one source. */
export function planBody(source: InstallSource): Record<string, string> {
  if (source.kind === "folder") return { folder: source.path };
  if (source.kind === "example") return { example: source.id };
  return { url: source.url, commit: source.commit };
}

/** Step two's body: the plan's own id and fingerprint, and the typed name when one was asked for. */
export function confirmBody(plan: Pick<InstallPlan, "plan" | "fingerprint" | "needs_typed_name">, typedName: string): Record<string, string> {
  const body: Record<string, string> = { plan: plan.plan, fingerprint: plan.fingerprint };
  if (plan.needs_typed_name) body.typed_name = typedName.trim();
  return body;
}

export function cancelBody(plan: Pick<InstallPlan, "plan">): Record<string, string> {
  return { plan: plan.plan };
}

/** Remove's body. Deleting the data also erases what the app wrote on the record unless the owner untick that. */
export function removeBody(choice: RemoveChoice): { keep_data: boolean; erase_record?: boolean } {
  if (choice.eraseRecord === undefined) return { keep_data: choice.keepData };
  return { keep_data: choice.keepData, erase_record: choice.eraseRecord };
}
