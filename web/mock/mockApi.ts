// Dev-only in-memory stand-in for the JimmyHQ server, enabled with
// `npm run dev:mock`. It follows the API contract closely enough to exercise
// the UI and is never included in the production build.

import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";

type Json = Record<string, unknown>;
type MockSetting = Json & { key: string; default: unknown; value: unknown; type: string; min?: number; max?: number };

const VERSION = "0.0.0-mock";
const COOKIE = "jimmyhq_mock_session=1";
const KINDS = ["task.created", "task.completed", "bench.proposed", "bench.approved", "chat.message", "memory.saved", "agent.run"];
const ACTORS = ["owner", "agent:planner", "agent:scribe", "system"];

function sendJson(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

async function readBody(req: IncomingMessage): Promise<Json> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}") as Json;
  } catch {
    return {};
  }
}

export function mockApi(): Plugin {
  let owner: { name: string; password: string } | null = null;
  let failures = 0;
  let seq = 0;
  const events: Json[] = [];
  const listeners = new Set<ServerResponse>();

  const sections = [
    { id: "appearance", label: "Appearance" },
    { id: "general", label: "General" },
    { id: "tape", label: "Tape" },
    { id: "bench", label: "Bench" },
  ];
  const settings: MockSetting[] = [
    {
      key: "appearance.theme", section: "appearance", type: "choice", label: "Theme",
      help: "Light, dark, or follow the operating system.", default: "system", value: "system",
      choices: [{ value: "light", label: "Light" }, { value: "system", label: "System" }, { value: "dark", label: "Dark" }],
    },
    { key: "appearance.compact", section: "appearance", type: "bool", label: "Compact lists", help: "Show more rows by tightening list spacing.", default: false, value: false },
    { key: "general.greeting", section: "general", type: "str", label: "Greeting", help: "Shown at the top of the Today panel.", default: "Hello", value: "Hello" },
    { key: "general.temperature", section: "general", type: "float", label: "Assistant temperature", help: "Higher values give more varied answers.", default: 0.7, value: 0.7, min: 0, max: 2 },
    { key: "tape.retention_days", section: "tape", type: "int", label: "Retention (days)", help: "How long events are kept before being archived.", default: 365, value: 365, min: 1, max: 3650 },
    { key: "bench.lanes", section: "bench", type: "list", label: "Lanes", help: "Order of the lanes on the Bench.", default: ["Needs review", "Waiting", "Done"], value: ["Needs review", "Waiting", "Done"] },
  ];

  const emit = (kind?: string) => {
    seq += 1;
    const event = {
      seq, id: `evt_${String(seq).padStart(6, "0")}`, ts: Date.now(),
      kind: kind ?? KINDS[seq % KINDS.length], v: 1, actor: ACTORS[seq % ACTORS.length],
      cause: null, root: null, subject: `item_${seq}`, payload: {},
    };
    events.unshift(event);
    for (const res of listeners) res.write(`event: tape\ndata: ${JSON.stringify(event)}\n\n`);
  };
  for (let i = 0; i < 24; i += 1) emit();

  return {
    name: "jimmyhq-mock-api",
    configureServer(server) {
      const timer = setInterval(() => emit(), 4000);
      server.httpServer?.once("close", () => clearInterval(timer));

      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url ?? "/", "http://mock.invalid");
        const path = url.pathname;
        if (!path.startsWith("/api/")) return next();
        const method = req.method ?? "GET";
        const signedIn = (req.headers.cookie ?? "").includes(COOKIE);

        void (async () => {
          if (path === "/api/health") return sendJson(res, 200, { ok: true, version: VERSION });
          if (path === "/api/auth/state") {
            return sendJson(res, 200, { setup_needed: owner === null, signed_in: owner !== null && signedIn, owner_name: owner?.name ?? null });
          }
          if (path === "/api/auth/setup" && method === "POST") {
            if (owner) return sendJson(res, 409, { detail: "An owner already exists." });
            const body = await readBody(req);
            owner = { name: String(body.name ?? ""), password: String(body.password ?? "") };
            return sendJson(res, 200, { ok: true }, { "Set-Cookie": `${COOKIE}; Path=/; HttpOnly; SameSite=Strict` });
          }
          if (path === "/api/auth/login" && method === "POST") {
            const body = await readBody(req);
            if (failures >= 5) return sendJson(res, 429, { detail: "Too many attempts. Try again in a minute." });
            if (!owner || body.password !== owner.password) {
              failures += 1;
              return sendJson(res, 401, { detail: "Incorrect password." });
            }
            failures = 0;
            return sendJson(res, 200, { ok: true }, { "Set-Cookie": `${COOKIE}; Path=/; HttpOnly; SameSite=Strict` });
          }
          if (path === "/api/auth/logout" && method === "POST") {
            return sendJson(res, 200, { ok: true }, { "Set-Cookie": `${COOKIE}; Path=/; Max-Age=0` });
          }

          if (!owner || !signedIn) return sendJson(res, 401, { detail: "Not signed in." });

          if (path === "/api/settings" && method === "GET") return sendJson(res, 200, { sections, settings });
          if (path.startsWith("/api/settings/")) {
            const key = decodeURIComponent(path.slice("/api/settings/".length));
            const setting = settings.find((item) => item.key === key);
            if (!setting) return sendJson(res, 404, { detail: `Unknown setting: ${key}` });
            if (method === "DELETE") setting.value = setting.default;
            else if (method === "PUT") {
              const { value } = await readBody(req);
              if (typeof value === "number" && ((setting.min !== undefined && value < setting.min) || (setting.max !== undefined && value > setting.max))) {
                return sendJson(res, 422, { detail: `${key} must be between ${setting.min} and ${setting.max}.` });
              }
              if (key === "general.greeting" && value === "fail") return sendJson(res, 422, { detail: "That greeting is not allowed (mock error)." });
              setting.value = value;
            }
            emit("settings.changed");
            return sendJson(res, 200, { key, value: setting.value });
          }
          if (path === "/api/tape/stats") {
            const byKind: Record<string, number> = {};
            for (const event of events) byKind[String(event.kind)] = (byKind[String(event.kind)] ?? 0) + 1;
            return sendJson(res, 200, { events: events.length + 1210, bytes: (events.length + 1210) * 340, by_kind: byKind });
          }
          if (path === "/api/tape") {
            const limit = Number(url.searchParams.get("limit") ?? 50);
            const before = url.searchParams.get("before");
            const kind = url.searchParams.get("kind");
            const page = events
              .filter((event) => (before ? Number(event.seq) < Number(before) : true) && (kind ? event.kind === kind : true))
              .slice(0, limit);
            return sendJson(res, 200, { events: page });
          }
          if (path === "/api/feed") {
            res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
            res.write(": connected\n\n");
            listeners.add(res);
            req.on("close", () => listeners.delete(res));
            return;
          }
          if (path === "/api/status") {
            return sendJson(res, 200, {
              version: VERSION,
              processes: [
                { name: "server", ok: true, last_seen_ms: Date.now() },
                { name: "worker", ok: true, last_seen_ms: Date.now() },
                { name: "scheduler", ok: false, last_seen_ms: null },
              ],
            });
          }
          return sendJson(res, 404, { detail: "Not found." });
        })().catch(next);
      });
    },
  };
}
