// The MCP door module's API client. Shapes follow server/jimmyhq/door/routes.py and
// docs/door.md. A client's token is in exactly two answers: the one that adds it and
// the one that rotates it. No listing ever holds one.

import { request } from "../../api";

export type DoorTool = {
  name: string;
  title: string;
  description: string;
  /** True when the tool changes something (adds a task, files a card, sends a message). */
  write: boolean;
  /** True for the one tool every client has, with no switch. */
  always: boolean;
  /** The tool's default calls a day, from its setting. */
  per_day: number;
};

/** A tool of an installed app that the owner may switch on for a client. Only tools
 *  that read, or write only into the app's own records, and that no role of the app
 *  lists, are ever here (server/jimmyhq/door/apptools.py). */
export type DoorAppTool = {
  /** The switch, as kept in a client's tools map. It names the app, its installation,
   *  the tool and its effect, so a switch never carries over to another tool. */
  key: string;
  /** The name a client calls it by: app_<app id>_<tool>. Its use is counted under this. */
  name: string;
  app_id: string;
  app_name: string;
  tool: string;
  title: string;
  description: string;
  effect: "read" | "record";
  /** True for a tool that writes into the app's own records. */
  write: boolean;
  per_day: number;
};

/** A tool an app offers its agents that is never offered to outside clients, and why. */
export type DoorAppToolHeldBack = { app_id: string; app_name: string; name: string; tool: string; effect: string; why: string };

export type DoorStatus = {
  enabled: boolean;
  listen: "own_port" | "main_port";
  /** The address to give a client: the public one if the owner set it, else the local one. */
  address: string;
  local_address: string;
  public: string | null;
  listening: boolean;
  problem: string | null;
  /** Wrong tokens seen in the last 15 minutes. */
  failed_tries: number;
  calls_per_minute: number;
  tools: DoorTool[];
  app_tools: DoorAppTool[];
  app_tools_held_back: DoorAppToolHeldBack[];
};

export type DoorOptions = {
  bench_bodies: boolean;
  addresses: boolean;
  task_areas: string[];
  calls_per_minute: number | null;
  /** True: a message to an agent is a Bench card first, and is delivered only if the owner approves it. */
  messages_need_approval: boolean;
};

export type DoorCall = {
  seq: number;
  ts: number;
  client_id: string;
  client: string;
  tool: string;
  ok: boolean;
  /** The rule that refused the call, or "". */
  rule: string;
  write: boolean;
  in_bytes: number;
  out_chars: number;
  ms: number;
  /** What a write made, as "task:<id>", "card:<id>" or "message:<id>". */
  created: string | null;
  /** For a call of an installed app's tool: which app, and the tool's own name. */
  app_id?: string | null;
  app_tool?: string | null;
};

/** tool name (or an app tool's key) -> its own calls a day for this client, or null for the tool's setting. */
export type ClientTools = Record<string, number | null>;

export type DoorClient = {
  id: string;
  name: string;
  about: string;
  tools: ClientTools;
  options: DoorOptions;
  created: number;
  rotated: number | null;
  revoked: number | null;
  used_today: Record<string, number>;
  last_call: DoorCall | null;
};

/** The door's address on the owner's Tailscale network, or null when JimmyHQ is not shared there. */
export type DoorTailnet = { address: string | null; in_use: boolean; moves_door: boolean };

export type TokenAnswer = { client: DoorClient; token: string; address: string };
export type ClientChange = { about?: string; tools?: ClientTools; options?: Partial<DoorOptions> };

const BASE = "/api/door";
export const DOOR_KEYS = {
  all: ["door"] as const,
  status: ["door", "status"] as const,
  clients: ["door", "clients"] as const,
  calls: ["door", "calls"] as const,
  /** Not under "door": it asks Tailscale, so it is not asked again on every call. */
  tailnet: ["door-tailnet"] as const,
};

export const doorApi = {
  status: () => request<DoorStatus>("GET", `${BASE}/status`),
  tailnet: () => request<DoorTailnet>("GET", `${BASE}/tailnet`),
  useAddress: (use: "tailnet" | "local") => request<DoorStatus>("POST", `${BASE}/address`, { body: { use } }),
  clients: () => request<DoorClient[]>("GET", `${BASE}/clients`),
  calls: (limit = 50) => request<{ calls: DoorCall[] }>("GET", `${BASE}/calls?limit=${limit}`),
  add: (name: string, about: string) => request<TokenAnswer>("POST", `${BASE}/clients`, { body: { name, about } }),
  change: (id: string, change: ClientChange) => request<DoorClient>("PUT", `${BASE}/clients/${encodeURIComponent(id)}`, { body: change }),
  rotate: (id: string) => request<TokenAnswer>("POST", `${BASE}/clients/${encodeURIComponent(id)}/rotate`),
  revoke: (id: string) => request<DoorClient>("POST", `${BASE}/clients/${encodeURIComponent(id)}/revoke`),
};
