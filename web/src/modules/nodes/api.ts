// The nodes module's API client. See docs/nodes.md for the routes. Every one
// of these is for the signed-in owner only.

import { request } from "../../api";

export type NodeModel = {
  /** The short name the node offers this model server under. */
  name: string;
  /** The provider that stands for it under Settings, Models. */
  provider_id: string;
  /** The roles that use a model on it. */
  roles: string[];
};

export type NodeRecord = {
  id: string;
  name: string;
  joined_ms: number;
  /** Null until the node has reported in for the first time. */
  version: string | null;
  offers: ("models" | "browser")[];
  models: NodeModel[];
  browsers: string[];
  connected: boolean;
  /** Unix milliseconds, or null if it has never reported in. */
  last_seen_ms: number | null;
  /** A sentence when this machine and the node run different versions. */
  skew: string | null;
  /** True when the versions cannot work together: nothing is sent to the node. */
  blocked: boolean;
};

export type NodesResponse = { nodes: NodeRecord[]; hub_version: string; protocol: number };
export type JoinCode = { code: string; expires_ms: number; minutes: number };
export type RemoveResult = { ok: true; name: string; roles_changed: string[] };

export const NODES_KEY = ["nodes", "list"] as const;

export const nodesApi = {
  list: () => request<NodesResponse>("GET", "/api/nodes"),
  /** A one-time code for another machine to join with. Shown once. */
  joinCode: () => request<JoinCode>("POST", "/api/nodes/join-codes"),
  /** Revokes the node's token at once and takes its models out of any role. */
  remove: (id: string) => request<RemoveResult>("DELETE", `/api/nodes/${encodeURIComponent(id)}`),
};
