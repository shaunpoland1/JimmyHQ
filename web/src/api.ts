// Typed client for the JimmyHQ server API. All calls are same-origin JSON and
// rely on the HttpOnly session cookie for authentication.

export type TapeEvent = {
  seq: number;
  id: string;
  /** Unix milliseconds, UTC. */
  ts: number;
  kind: string;
  v: number;
  actor: string;
  cause: string | null;
  root: string | null;
  subject: string | null;
  payload: Record<string, unknown>;
  /** False when the server could not check the payload (unknown kind or newer version). */
  readable?: boolean;
  /** True when the owner had this event's content erased: the payload is empty and only its place, time and kind remain. */
  erased?: boolean;
};
// The contract calls this type `Event`; the alias avoids shadowing the DOM type.
export type { TapeEvent as Event };

export type Health = { ok: true; version: string };

export type AuthState = {
  setup_needed: boolean;
  signed_in: boolean;
  owner_name: string | null;
};

export type SettingType = "bool" | "choice" | "int" | "float" | "str" | "list";
export type SettingValue = boolean | number | string | string[];
export type SettingChoice = { value: string | number; label: string };

export type Setting = {
  key: string;
  section: string;
  type: SettingType;
  label: string;
  help: string;
  default: SettingValue;
  value: SettingValue;
  min?: number;
  max?: number;
  choices?: SettingChoice[];
  /** For a list: the only values it may hold. The screen offers these to pick from. */
  items?: SettingChoice[];
  /** For a list: no value twice. */
  unique?: boolean;
  /** False for what is personal or tied to this computer; a shared profile leaves it out. */
  portable?: boolean;
  /** For text: what it holds when that is more exact than any text, e.g. "timezone". */
  format?: string;
  /** True for fine tuning most people never need; kept behind "Show advanced settings". */
  advanced?: boolean;
};

export type SettingsSection = { id: string; label: string };
/** Where a section sits: its group, the page it is a headed part of (if any), and the line under its heading. */
export type SettingsSectionPlace = { group: string; parent: string | null; description: string };
export type SettingsLayout = {
  groups: { id: string; label: string }[];
  sections: Record<string, SettingsSectionPlace>;
};
export type SettingsResponse = {
  /** In the order to show. */
  sections: SettingsSection[];
  settings: Setting[];
  /** Missing from a server older than the grouped Settings screen. */
  layout?: SettingsLayout;
};
export type SettingResult = { key: string; value: SettingValue };

/** A settings file: what Export writes and Import reads. */
export type SettingsDocument = {
  jimmyhq_settings: number;
  name?: string;
  description?: string;
  /** A null value means "back to the default". */
  settings: Record<string, unknown>;
};
export type PlanEntry = {
  key: string;
  status: "change" | "same" | "refused";
  /** Null for a name that is not a setting here. */
  label: string | null;
  old: unknown;
  new: unknown;
  reset: boolean;
  portable: boolean | null;
  /** For a refused entry: what is wrong and what to do. */
  reason: string | null;
};
export type SettingsPlan = {
  name: string;
  description: string;
  applied: boolean;
  changes: PlanEntry[];
  unchanged: string[];
  refused: PlanEntry[];
  /** A settings file that puts every changed setting back as it was. */
  undo: SettingsDocument;
};
export type SettingsExportOptions = { portable: boolean; changed: boolean };

export type TapeResponse = { events: TapeEvent[] };
export type TapeStats = { events: number; bytes: number; by_kind: Record<string, number> };
export type TapeQuery = { limit?: number; before?: number; kind?: string };

export type ProcessStatus = { name: string; ok: boolean; last_seen_ms: number | null };
export type StatusResponse = { version: string; processes: ProcessStatus[] };

export const FEED_URL = "/api/feed";

export class ApiError extends Error {
  /** HTTP status, or 0 when the server could not be reached. */
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Human-readable message for any thrown value. */
export function errorDetail(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

let unauthorizedHandler: (() => void) | null = null;

/** Registers what happens when an authed endpoint answers 401 (session ended). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function detailFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : "",
        )
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
  }
  return `Request failed (${status}).`;
}

export type RequestOptions = {
  /** Sent as JSON, or as a form upload when it is a FormData. */
  body?: unknown;
  authed?: boolean;
  signal?: AbortSignal;
};

/** Tell the app that the session has ended, as a 401 from `request` does. */
export function reportUnauthorized(): void {
  unauthorizedHandler?.();
}

// One check shared by every request that was answered 401 at the same moment.
let sessionCheck: Promise<boolean> | null = null;

/**
 * Whether this browser still has a session, asked after a 401. Changing the password
 * replaces the session cookie, so a request sent just before the change carries the
 * old one and is refused although the browser is still signed in.
 */
function stillSignedIn(): Promise<boolean> {
  sessionCheck ??= fetch("/api/auth/state", { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then(async (response) => response.ok && ((await response.json()) as Partial<AuthState>).signed_in === true)
    .catch(() => false)
    .finally(() => {
      sessionCheck = null;
    });
  return sessionCheck;
}

/** JSON request to the server. Module clients in src/modules/<name>/api.ts build on this. */
export function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  return send<T>(method, path, options, true);
}

async function send<T>(method: string, path: string, options: RequestOptions, again: boolean): Promise<T> {
  const { body, authed = true, signal } = options;
  const form = typeof FormData !== "undefined" && body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      signal,
      // A form upload sets its own content type, with the boundary.
      headers: body === undefined || form ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : form ? (body as FormData) : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "Cannot reach the server.");
  }
  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }
    if (response.status === 401 && authed) {
      // A 401 is the gate's refusal: the request did nothing, so sending it once more
      // with the cookie the browser now holds is safe.
      if (again && (await stillSignedIn())) return send<T>(method, path, options, false);
      unauthorizedHandler?.();
    }
    throw new ApiError(response.status, detailFrom(parsed, response.status));
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "The server sent an unreadable response.");
  }
}

function settingPath(key: string): string {
  return `/api/settings/${encodeURIComponent(key)}`;
}

/** Where the server hands out the settings file; a link to it downloads. */
export function settingsExportUrl(options: SettingsExportOptions): string {
  const params = new URLSearchParams({ portable: String(options.portable), changed: String(options.changed) });
  return `/api/settings/export?${params.toString()}`;
}

export const api = {
  health: () => request<Health>("GET", "/api/health", { authed: false }),
  authState: () => request<AuthState>("GET", "/api/auth/state", { authed: false }),
  setup: (name: string, password: string) =>
    request<{ ok: true }>("POST", "/api/auth/setup", { body: { name, password }, authed: false }),
  login: (password: string) =>
    request<{ ok: true }>("POST", "/api/auth/login", { body: { password }, authed: false }),
  logout: () => request<{ ok: true }>("POST", "/api/auth/logout", { authed: false }),
  settings: () => request<SettingsResponse>("GET", "/api/settings"),
  putSetting: (key: string, value: SettingValue) =>
    request<SettingResult>("PUT", settingPath(key), { body: { value } }),
  resetSetting: (key: string) => request<SettingResult>("DELETE", settingPath(key)),
  planSettings: (document: SettingsDocument) =>
    request<SettingsPlan>("POST", "/api/settings/plan", { body: document }),
  importSettings: (document: SettingsDocument, skipInvalid: boolean) =>
    request<SettingsPlan>("POST", `/api/settings/import${skipInvalid ? "?skip_invalid=true" : ""}`, {
      body: document,
    }),
  tape: (query: TapeQuery = {}) => {
    const params = new URLSearchParams();
    params.set("limit", String(query.limit ?? 50));
    if (query.before !== undefined) params.set("before", String(query.before));
    if (query.kind) params.set("kind", query.kind);
    return request<TapeResponse>("GET", `/api/tape?${params.toString()}`);
  },
  tapeStats: () => request<TapeStats>("GET", "/api/tape/stats"),
  status: () => request<StatusResponse>("GET", "/api/status"),
};
