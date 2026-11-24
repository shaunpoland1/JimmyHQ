// Typed client for the Village: the roster, one agent's card, chat, priorities and
// the commands the village process runs (birth, retirement). Shapes follow
// server/jimmyhq/village/routes.py. Every response type lives in this file, and
// anything the server may leave out is optional, so a small change there does not
// break the screen.

import { request } from "../../api.ts";

export const NEEDS = ["energy", "frustration", "social", "curiosity", "purpose"] as const;
export type Need = (typeof NEEDS)[number];
/** Each need is 0 to 100. A need the server did not send is simply absent. */
export type Needs = Partial<Record<Need, number>>;

export type Mode = "work" | "explore" | "outlet" | "social" | "rest" | "sleep";

/** Which model to ask: a role from the Models screen, or one provider's model. */
export type ModelChoice = { role?: string | null; provider_id?: string | null; model?: string | null };

export type AgentConfig = {
  role: string;
  beat: ModelChoice;
  night: ModelChoice;
  local_only: boolean;
  capabilities: string[];
};

/** Something an agent may use beyond the basics, as the owner is told about it. */
export type Capability = {
  id: string;
  label: string;
  /** What the agent can then do by itself, and what leaves the machine. */
  description: string;
  /** False while something it depends on is not set up. */
  ready?: boolean;
  /** The sentence that says what is not set up yet, or null. */
  problem?: string | null;
  /** The settings section where that is fixed, when there is one. */
  settings?: string | null;
};

/** One agent as the roster lists it. */
export type AgentSummary = {
  id: string;
  /** null until the agent has named itself. */
  name: string | null;
  role: string;
  retired: boolean;
  /** The server's own one-line status, used when nothing better can be said. */
  status_line?: string;
  mode?: Mode | string | null;
  asleep?: boolean;
  needs?: Needs;
  current_task?: string | null;
  /** How many things the agent has handed over and is waiting to hear back about. */
  waiting?: number;
  /** Chat lines from the agent since the owner last wrote. */
  unanswered?: number;
};

export type Mood = { valence: number; arousal: number };

export type Temperament = {
  energy_drain_rate?: number;
  boredom_rate?: number;
  social_capacity?: number;
  frustration_tolerance?: number;
  risk_appetite?: number;
  mood_weights?: Partial<Record<Need, number>>;
  seed_curiosities?: string[];
  sleep_offset_minutes?: number;
};

export type LastNight = {
  /** Unix milliseconds. */
  ts: number;
  /** The day that was sorted, YYYY-MM-DD. */
  day: string;
  /** "smart" is the night model; "fallback" is the everyday model standing in. */
  by: "smart" | "fallback" | string;
  ops: number;
  rejected?: number;
  snapshot?: number | null;
  notes?: string;
};

export type LastRestore = { ts: number; to: number; undo: number; paths?: string[]; note?: string };

export type AgentVitals = {
  /** "night", "nap" or "forced" while asleep. */
  sleep_kind?: string | null;
  /** Names of tasks set aside. */
  parked?: string[];
  /** When a chosen rest ends. Unix seconds or milliseconds; see `toMs`. */
  rest_until?: number | null;
  /** Not sent yet. When the agent will wake, if the server comes to say. */
  wake_at?: number | null;
  nap_until?: number | null;
};

export type AgentCounts = {
  memory?: number;
  candidates?: number;
  diary_days?: number;
  snapshots?: number;
  events_today?: Record<string, number>;
};

/** One agent in full: the roster row and everything the agent page shows. */
export type AgentCard = AgentSummary & {
  born_ts?: number | null;
  retired_ts?: number | null;
  seed?: number | null;
  vitals?: AgentVitals;
  mood?: Mood | null;
  config?: AgentConfig;
  temperament?: Temperament;
  last_night?: LastNight | null;
  last_restore?: LastRestore | null;
  counts?: AgentCounts;
};

export type NewAgent = {
  role: string;
  seed: number | null;
  models: { beat: ModelChoice | null; night: ModelChoice | null };
  local_only: boolean;
};

export type CommandTicket = { command_id: string };

export type CommandResult = {
  ok: boolean;
  /** Why it was refused, as a sentence. */
  error?: string;
  agent_id?: string;
  seed?: number;
  /** Starter tasks given at birth. */
  tasks?: number;
  /** Shared files kept when an agent retires. */
  left?: number | string[];
  /** A rollback: the snapshot restored, the snapshot taken first so it can be undone, and what was put back. */
  to?: number;
  undo?: number;
  paths?: string[];
};

export type CommandState = { done: boolean; result: CommandResult | null; kind?: string; agent?: string | null };

export type Priority = {
  id: string;
  title: string;
  why: string;
  details: string;
  /** Empty means everyone. */
  agents: string[];
  active: boolean;
  suggested_by?: string | null;
  created_ts?: number;
  updated_ts?: number;
};

export type NewPriority = { title: string; why: string; details: string; agents: string[]; active: boolean };
export type PriorityChange = Partial<NewPriority>;

export type ChatMessage = {
  /** The line's place on the Tape. Pages are asked for by it. */
  seq: number;
  id: string;
  /** Unix milliseconds. */
  ts: number;
  sender: "owner" | "agent";
  text: string;
  urgent?: boolean;
};

export type ChatPage = { messages: ChatMessage[]; asleep?: boolean; more?: boolean };
export type SentMessage = ChatMessage & { delivered?: "now" | "when it wakes" | string };

// ---- diary, memory and snapshots ----

export type DiaryEntry = {
  /** HH:MM, in the agent's own time zone. */
  time: string;
  dream?: boolean;
  text: string;
};
/** `days` is every day with a diary, oldest first; `day` is the one whose entries are sent. */
export type DiaryPage = { days: string[]; day: string; entries: DiaryEntry[] };

export const MEMORY_SECTIONS = ["self", "skills", "tools", "wants", "moments", "facts", "story"] as const;
export type MemorySection = (typeof MEMORY_SECTIONS)[number];

export type MemoryLine = {
  id: number;
  section?: string;
  text: string;
  /** 0 to 1. */
  strength?: number;
  /** "core", "useful" or "routine". */
  weight?: string;
  /** "owner", "seen", "village", "web:unconfirmed" or "self-reported". */
  source?: string;
  /** Ids of the events the line rests on. */
  evidence?: string[];
  created?: string;
  last_confirmed?: string;
  /** How many later nights confirmed it again. */
  confirmed?: number;
  locked?: boolean;
  consolidated_by?: string;
  kind?: string;
};

/** Something it asked to remember during the day, waiting for the next night to decide. */
export type Candidate = { ts?: number; section?: string; text: string; evidence?: string[] };
export type Impression = { about: string; text: string };

export type MemoryPage = {
  sections?: Partial<Record<string, MemoryLine[]>>;
  candidates?: Candidate[];
  impressions?: Impression[];
  last_night?: LastNight | null;
};

/** One saved copy of its memory. The manifest, as the server keeps it. */
export type Snapshot = {
  seq: number;
  /** Unix milliseconds (seconds are accepted too). */
  ts: number;
  /** "night", "nap" or "before rollback". */
  reason?: string;
  paths?: string[];
  files?: number;
  /** The day a night sorted. */
  day?: string;
  by?: string;
  /** For "before rollback": the snapshot it was about to restore. */
  to?: number;
};
export type SnapshotList = { snapshots: Snapshot[] };
export type RollbackAsk = { to: number; diary: boolean; note: string };

// ---- files ----

export type FileItem = { name: string; path: string; dir: boolean; size: number | null; modified?: number };
export type FileList = { path: string; items: FileItem[] };
export type FileText = { path: string; size?: number; modified?: number; truncated?: boolean; text: string };

// ---- the village event log, the break room and the work board ----

export type VillageEvent = { id: string; ts: number; kind: string; agent?: string | null; data: Record<string, unknown> };
export type EventsPage = { events: VillageEvent[] };
export type EventsAsk = { kinds?: readonly string[]; before?: string; limit?: number };

export type RoomLine = { id: string; ts: number; agent: string | null; kind?: string; text: string };
export type BoardPost = {
  id: string;
  ts: number;
  agent: string | null;
  /** "need_help", "good_at" or "handoff". */
  kind?: string | null;
  text: string;
  open: boolean;
  resolved?: { by?: string | null; ts?: number; note?: string } | null;
};

/** How far an agent may go by itself when a form would change something. */
export type SubmitLevel = "read" | "approve" | "alone";
export type BrowserTabInfo = { url: string; title: string; active: boolean };
export type BrowserUse = { used: number; cap: number };
export type BrowserRefusal = { ts_ms: number; host: string; rule: string };
/** What the owner watches: one agent's browser as the village process last left it. */
export type BrowserState = {
  agent_id: string;
  /** Whether this agent has the browser capability at all. */
  held: boolean;
  /** False when no browser program is installed; `problem` then says what to do. */
  ready: boolean;
  problem?: string | null;
  running: boolean;
  updated_ms?: number | null;
  /** When the newest picture was taken, or null when there is none to show. */
  picture_ms?: number | null;
  tabs: BrowserTabInfo[];
  refused?: number;
  refused_last?: BrowserRefusal[];
  submit_level: SubmitLevel;
  /** Empty means anywhere the fence allows; otherwise only these sites. */
  allow_hosts: string[];
  waiting?: { card_id: string; host?: string | null; asked_ms: number }[];
  limits?: { pages?: BrowserUse; actions?: BrowserUse; requests?: BrowserUse };
};
export type BrowserChange = { submit_level?: SubmitLevel; allow_hosts?: string[] };

const agentPath = (id: string) => `/api/village/agents/${encodeURIComponent(id)}`;

export const villageApi = {
  /** Everyone who has ever lived here, in the order they arrived. */
  agents: () => request<AgentSummary[]>("GET", "/api/village/agents?retired=true"),
  agent: (id: string) => request<AgentCard>("GET", agentPath(id)),
  birth: (body: NewAgent) => request<CommandTicket>("POST", "/api/village/agents", { body }),
  configure: (id: string, config: AgentConfig) => request<AgentConfig>("PUT", `${agentPath(id)}/config`, { body: config }),
  capabilities: () => request<Capability[]>("GET", "/api/village/capabilities"),
  /** One agent's browser: its tabs, limits and the owner's choices. Owner only. */
  browser: (id: string) => request<BrowserState>("GET", `${agentPath(id)}/browser`),
  changeBrowser: (id: string, body: BrowserChange) => request<BrowserState>("PUT", `${agentPath(id)}/browser`, { body }),
  retire: (id: string, note: string) => request<CommandTicket>("POST", `${agentPath(id)}/retire`, { body: { note } }),
  command: (id: string) => request<CommandState>("GET", `/api/village/commands/${encodeURIComponent(id)}`),

  priorities: () => request<Priority[]>("GET", "/api/village/priorities"),
  addPriority: (body: NewPriority) => request<Priority>("POST", "/api/village/priorities", { body }),
  changePriority: (id: string, body: PriorityChange) =>
    request<Priority>("PUT", `/api/village/priorities/${encodeURIComponent(id)}`, { body }),

  chat: (id: string, before?: number) =>
    request<ChatPage>("GET", `${agentPath(id)}/chat${before === undefined ? "" : `?before=${before}`}`),
  diary: (id: string, day = "") => request<DiaryPage>("GET", `${agentPath(id)}/diary${day ? `?day=${encodeURIComponent(day)}` : ""}`),
  memory: (id: string) => request<MemoryPage>("GET", `${agentPath(id)}/memory`),
  snapshots: (id: string) => request<SnapshotList>("GET", `${agentPath(id)}/snapshots`),
  rollback: (id: string, body: RollbackAsk) => request<CommandTicket>("POST", `${agentPath(id)}/rollback`, { body }),

  files: (id: string, path: string) => request<FileList>("GET", `${agentPath(id)}/files?path=${encodeURIComponent(path)}`),
  file: (id: string, path: string) => request<FileText>("GET", `${agentPath(id)}/files/read?path=${encodeURIComponent(path)}`),
  /** The shared folders answer with a listing for a folder and the text for a file. */
  shared: (path: string) => request<FileList | FileText>("GET", `/api/village/shared?path=${encodeURIComponent(path)}`),

  events: (id: string, ask: EventsAsk = {}) => {
    const query = new URLSearchParams();
    if (ask.kinds && ask.kinds.length > 0) query.set("kinds", ask.kinds.join(","));
    if (ask.before) query.set("before", ask.before);
    if (ask.limit) query.set("limit", String(ask.limit));
    const text = query.toString();
    return request<EventsPage>("GET", `${agentPath(id)}/events${text ? `?${text}` : ""}`);
  },
  breakroom: () => request<{ lines: RoomLine[] }>("GET", "/api/village/breakroom"),
  board: () => request<{ posts: BoardPost[] }>("GET", "/api/village/board"),

  say: (id: string, text: string, urgent: boolean) =>
    request<SentMessage>("POST", `${agentPath(id)}/chat`, { body: { text, urgent } }),
};
