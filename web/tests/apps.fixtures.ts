import type { AppDetail, InstallPlan, PermissionLine } from "../src/modules/apps/api.ts";

export const OWN: PermissionLine = { id: "always.data", permission: "always.data", sentence: "Keep its own data on this computer.", always: true };
export const RUNS: PermissionLine = {
  id: "always.full",
  permission: "always.full",
  sentence: "Runs as you on this computer. It can read and change your files and use the network. JimmyHQ cannot limit this.",
  strong: true,
  always: true,
};

export function line(id: string, sentence = `Sentence for ${id}.`): PermissionLine {
  const [permission, host] = id.split(":");
  return { id, permission, host: host ?? null, sentence };
}

export function app(over: Partial<AppDetail> = {}): AppDetail {
  return { id: "water", name: "Water", version: "1.0.0", tier: "sandboxed", enabled: true, permissions: [line("tasks.write"), OWN], ...over };
}

export function plan(over: Partial<InstallPlan> = {}): InstallPlan {
  return {
    plan: "01JD0000000000000000000000",
    fingerprint: "f".repeat(64),
    source: { kind: "folder", path: "my-apps/water" },
    needs_typed_name: false,
    app: { id: "water", name: "Water", version: "1.0.0", tier: "sandboxed", summary: "Count glasses of water.", author: "Example Author", licence: "MIT" },
    reviewed: false,
    provenance: "folder",
    permissions: [line("tasks.write"), OWN],
    creates: null,
    installed_version: null,
    described: false,
    ...over,
  };
}

export function fullPlan(over: Partial<InstallPlan> = {}): InstallPlan {
  return plan({
    app: { id: "drafts", name: "Drafts", version: "0.1.0", tier: "full", author: "Example Author" },
    permissions: [RUNS, line("model.ask"), line("jobs"), line("bench.file"), OWN],
    needs_typed_name: true,
    ...over,
  });
}
