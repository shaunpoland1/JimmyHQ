// The roles of an app, over HTTP: what a waiting install would like filled, what an
// installed app's roles are now, and the owner's choices. Owner only.

import { request } from "../../api.ts";
import { choicesBody, readRolesView } from "./lib/roles.ts";
import type { Choices, RolesView } from "./lib/roles.ts";

export const ROLES_KEY = (id: string) => ["apps", "roles", id] as const;

export const rolesApi = {
  forPlan: async (plan: string): Promise<RolesView> => readRolesView(await request<unknown>("GET", `/api/apps/_install/roles/${encodeURIComponent(plan)}`)),
  forApp: async (id: string): Promise<RolesView> => readRolesView(await request<unknown>("GET", `/api/apps/${encodeURIComponent(id)}/roles`)),
  choose: (id: string, choices: Choices) => request<unknown>("PUT", `/api/apps/${encodeURIComponent(id)}/roles`, { body: choicesBody(choices) }),
};
