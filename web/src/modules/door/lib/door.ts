// Pure logic for the MCP door's settings: names, switches, limits and the words shown.
// Nothing here touches the network or the page.

import type { ClientTools, DoorAppTool, DoorCall, DoorClient, DoorStatus, DoorTool } from "../api";

export const NAME_MAX = 32;
export const ABOUT_MAX = 300;
export const LIMIT_MAX = 100000;

export type Check<T> = { ok: true; value: T } | { ok: false; error: string };

/** A client's name as the server takes it: lower case, digits, - and _. */
export function checkName(raw: string, taken: readonly string[] = []): Check<string> {
  const name = raw.trim().toLowerCase();
  if (!name) return { ok: false, error: "Give the client a short name." };
  if (name.length > NAME_MAX) return { ok: false, error: `A name can be at most ${NAME_MAX} characters.` };
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) {
    return { ok: false, error: "Use lower-case letters, digits, - and _ only, starting with a letter or a digit." };
  }
  if (taken.includes(name)) return { ok: false, error: "That name has been used for a client before. Names are not reused." };
  return { ok: true, value: name };
}

/** What the owner typed for a daily limit: empty means "use the tool's own setting". */
export function checkLimit(raw: string): Check<number | null> {
  const text = raw.trim();
  if (!text) return { ok: true, value: null };
  if (!/^\d+$/.test(text)) return { ok: false, error: "A daily limit is a whole number, or empty for the usual one." };
  const value = Number(text);
  if (value > LIMIT_MAX) return { ok: false, error: `A daily limit can be at most ${LIMIT_MAX}.` };
  return { ok: true, value };
}

/** Whether a client may use a tool right now. */
export function toolOn(client: Pick<DoorClient, "tools">, tool: Pick<DoorTool, "name" | "always">): boolean {
  return tool.always || tool.name in client.tools;
}

/** The tools map after a switch is flipped. Other tools and their limits are kept. */
export function withTool(tools: ClientTools, name: string, on: boolean): ClientTools {
  const next: ClientTools = { ...tools };
  if (on) next[name] = name in tools ? (tools[name] ?? null) : null;
  else delete next[name];
  return next;
}

/** The tools map after a tool's own daily limit is set (null clears it). */
export function withLimit(tools: ClientTools, name: string, limit: number | null, always = false): ClientTools {
  const next: ClientTools = { ...tools };
  if (limit === null && always) delete next[name];
  else next[name] = limit;
  return next;
}

/** The calls a day that apply to one tool for one client. */
export function limitFor(client: Pick<DoorClient, "tools">, tool: Pick<DoorTool, "name" | "per_day">): number {
  return client.tools[tool.name] ?? tool.per_day;
}

/** "3 of 20 today". */
export function usageText(client: Pick<DoorClient, "tools" | "used_today">, tool: Pick<DoorTool, "name" | "per_day">): string {
  return `${client.used_today[tool.name] ?? 0} of ${limitFor(client, tool)} today`;
}

/** Areas typed as "home, work" become a clean list. */
export function parseAreas(raw: string): string[] {
  const seen = new Set<string>();
  for (const part of raw.split(",")) {
    const area = part.trim().toLowerCase();
    if (area) seen.add(area);
  }
  return [...seen];
}

export type Tone = "ok" | "warn" | "off";

/** One line on where the door stands, for the top of the card. */
export function statusLine(status: Pick<DoorStatus, "enabled" | "listening" | "problem" | "listen" | "public">): { tone: Tone; text: string } {
  if (!status.enabled) return { tone: "off", text: "Off. Nothing answers at the door's address and every client is shut out." };
  if (status.problem) return { tone: "warn", text: status.problem };
  if (!status.listening) return { tone: "warn", text: "Switched on, and starting. If this stays, check the port under the settings below." };
  const where = status.listen === "main_port" ? "at /mcp on JimmyHQ's own address" : "on its own port, which answers nothing but /mcp";
  const reach = status.public ? "You have set a public address: anyone on the internet can knock, and only a client's token gets in." : "It can be reached from this computer only, until you point a tunnel or a private network at it.";
  return { tone: "ok", text: `On, ${where}. ${reach}` };
}

/** Whether an installed app's tool is switched on for a client. Only its own key counts. */
export function appToolOn(client: Pick<DoorClient, "tools">, tool: Pick<DoorAppTool, "key">): boolean {
  return tool.key in client.tools;
}

/** "3 of 50 today" for an app tool: used under its name, limited under its key. */
export function appUsageText(client: Pick<DoorClient, "tools" | "used_today">, tool: Pick<DoorAppTool, "key" | "name" | "per_day">): string {
  return `${client.used_today[tool.name] ?? 0} of ${client.tools[tool.key] ?? tool.per_day} today`;
}

/** What an app tool does to the app, in the owner's words. */
export function appEffectWords(tool: Pick<DoorAppTool, "write" | "app_name">): string {
  return tool.write ? `Writes into ${tool.app_name}'s own records` : "Only reads";
}

export type AppGroup<T> = { app_id: string; app_name: string; tools: T[] };

/** App tools grouped by app, in the order they came. */
export function appGroups<T extends Pick<DoorAppTool, "app_id" | "app_name">>(tools: readonly T[]): AppGroup<T>[] {
  const groups = new Map<string, AppGroup<T>>();
  for (const tool of tools) {
    const group = groups.get(tool.app_id) ?? { app_id: tool.app_id, app_name: tool.app_name, tools: [] };
    group.tools.push(tool);
    groups.set(tool.app_id, group);
  }
  return [...groups.values()];
}

/** What a client with these tools can do, in the owner's words. */
export function canDo(client: Pick<DoorClient, "tools">, tools: readonly DoorTool[], appTools: readonly Pick<DoorAppTool, "key" | "write">[] = []): string {
  const on = tools.filter((tool) => !tool.always && toolOn(client, tool));
  const apps = appTools.filter((tool) => appToolOn(client, tool));
  const total = on.length + apps.length;
  if (!total) return "This client can ask who it is, and nothing else. Switch a tool on to let it do more.";
  const writes = on.filter((tool) => tool.write).length + apps.filter((tool) => tool.write).length;
  const reads = total - writes;
  const parts = [];
  if (reads) parts.push(`${reads} that only read`);
  if (writes) parts.push(`${writes} that can add something`);
  const from = apps.length ? ` (${apps.length} from your apps)` : "";
  return `This client can use ${total} ${total === 1 ? "tool" : "tools"}${from}: ${parts.join(" and ")}.`;
}

/** Shown above the apps' tools in a client's list. */
export const APP_TOOLS_NOTE =
  "Tools your installed apps offer, which you can give this client one by one. Only tools that read, or write only into the app's own records, are ever offered here. A tool that belongs to one of an app's roles, or that could change something outside the app, never is. Each is off until you switch it on, and a new app's tools arrive off. The app sees the call as coming from this client, not from one of your agents, and what it answers reaches the client marked as text from an app.";

/** Shown wherever messages to agents are switched on: who can be written to, and how. */
export const MESSAGES_NOTE =
  "A message reaches an agent only if you gave that agent \"Answer outside systems\" on the Village screen. That one switch lets the agent hear from outside clients and write back. A message for any other agent is refused.";

export const NEVER =
  "No client can decide a card, change or drop a task, read an agent's files or diary, read private memory, save a fact without your approval, change a setting or read the Tape. Everything a client writes is signed with its name.";

const RULE_WORDS: Record<string, string> = {
  door_tool_not_allowed: "asked for a tool it may not use",
  door_daily_limit: "reached the day's limit",
  door_rate_limit: "asked too fast",
  door_bad_arguments: "sent arguments the tool does not take",
  door_internal: "the tool failed inside JimmyHQ",
  door_app_not_running: "the app was not running",
  door_app_tool_timeout: "the app did not answer in time",
  door_app_tool_failed: "the app said it did not work",
  door_busy: "called again while an app was still answering",
};

/** Why a call was refused, in plain words. An unknown rule is shown as its name. */
export function ruleWords(rule: string): string {
  return RULE_WORDS[rule] ?? rule.replace(/^door_/, "").replace(/_/g, " ");
}

/** One receipt as a line: who, what, and how it went. */
export function callLine(call: Pick<DoorCall, "client" | "tool" | "ok" | "rule" | "created">): string {
  const tool = call.tool === "(any)" ? "a request" : call.tool;
  if (!call.ok) return `${call.client}: ${tool} was refused (${ruleWords(call.rule)})`;
  const made = call.created ? `, made ${call.created.split(":")[0]} ${call.created.split(":").slice(1).join(":").slice(-6)}` : "";
  return `${call.client}: ${tool}${made}`;
}

/** Whether a live event means the door's data should be asked for again. */
const APP_LIFECYCLE = new Set(["apps.installed", "apps.updated", "apps.rolled_back", "apps.removed", "apps.enabled", "apps.disabled", "apps.permissions_granted"]);

export function affectsDoor(event: { kind: string; payload?: unknown }): boolean {
  if (event.kind.startsWith("door.")) return true;
  // An app installed, switched off or removed changes which app tools can be switched on.
  if (APP_LIFECYCLE.has(event.kind)) return true;
  if (event.kind !== "setting.updated" && event.kind !== "setting.reset") return false;
  const key = (event.payload as { key?: unknown } | undefined)?.key;
  return typeof key === "string" && key.startsWith("door.");
}

/** Live clients first, oldest first; revoked ones after, newest first. */
export function sortClients<T extends Pick<DoorClient, "created" | "revoked">>(clients: readonly T[]): T[] {
  const live = clients.filter((client) => client.revoked === null).sort((a, b) => a.created - b.created);
  const gone = clients.filter((client) => client.revoked !== null).sort((a, b) => (b.revoked ?? 0) - (a.revoked ?? 0));
  return [...live, ...gone];
}
