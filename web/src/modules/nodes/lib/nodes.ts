// Pure logic for Settings, Nodes: what to say about a node, the command to
// type on the other machine, and what removing a node will change.

import type { TapeEvent } from "../../../api";
import type { NodeRecord, RemoveResult } from "../api";

export type Tone = "ok" | "warn" | "off";
export type NodeStatus = { tone: Tone; label: string; detail: string | null };

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** "a", "a and b", "a, b and c". */
export function listed(items: readonly string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** How long ago, in words that do not need a clock: "just now", "5 minutes ago". */
export function ago(then: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} days ago`;
}

export function nodeStatus(node: NodeRecord, now: number): NodeStatus {
  if (node.blocked) return { tone: "warn", label: "Versions do not match", detail: node.skew };
  if (node.connected) return { tone: "ok", label: "Connected", detail: node.skew };
  if (node.last_seen_ms === null) {
    return { tone: "off", label: "Not connected yet", detail: "On that machine, start it with: jimmyhq node run" };
  }
  return {
    tone: "warn",
    label: "Out of reach",
    detail: `Last heard from ${ago(node.last_seen_ms, now)}. Check that the machine is on and that jimmyhq node run is running on it. Its models are passed over until it is back.`,
  };
}

/** What the node lends, in one line. */
export function offersLine(node: NodeRecord): string {
  const parts: string[] = [];
  if (node.models.length > 0) parts.push(`Models: ${node.models.map((model) => model.name).join(", ")}`);
  if (node.browsers.length > 0) parts.push(`Browsers: ${node.browsers.join(", ")}`);
  if (parts.length > 0) return parts.join(". ");
  return "Offers nothing yet. On that machine, run: jimmyhq node offer model --name NAME --url http://127.0.0.1:PORT/v1";
}

/** The roles that use any model on this node, each once, in order. */
export function rolesUsing(node: NodeRecord): string[] {
  return [...new Set(node.models.flatMap((model) => model.roles))].sort();
}

/** What the confirm dialog says before a node is removed. */
export function removeMessage(node: NodeRecord): string {
  const start = `${node.name} is cut off at once and its token stops working.`;
  const roles = rolesUsing(node);
  if (roles.length === 0) return `${start} No role uses a model on it, so no role changes.`;
  const which = roles.length === 1 ? `The ${roles[0]} role uses a model on it and loses it` : `The ${listed(roles)} roles use a model on it and lose it`;
  const next = roles.length === 1 ? "It moves to its next choice, or has no model if it has no other." : "Each moves to its next choice, or has no model if it has no other.";
  return `${start} ${which}. ${next}`;
}

export function removedLine(result: RemoveResult): string {
  if (result.roles_changed.length === 0) return `${result.name} was removed.`;
  const roles = result.roles_changed;
  return `${result.name} was removed. The ${listed(roles)} role${roles.length === 1 ? " was" : "s were"} changed: check ${roles.length === 1 ? "it" : "them"} under Settings, Models.`;
}

/** Whether an address the owner is using only works on the hub itself. */
export function onlyWorksHere(origin: string): boolean {
  try {
    return LOOPBACK.has(new URL(origin).hostname.toLowerCase());
  } catch {
    return true;
  }
}

/** The exact command to run on the other machine. */
export function joinCommand(origin: string, code: string): string {
  const address = onlyWorksHere(origin) ? origin.replace(/\/\/(\[[^\]]+\]|[^/:]+)/, "//ADDRESS-OF-THIS-COMPUTER") : origin;
  return `jimmyhq node join ${address} ${code}`;
}

/** What to say under the command about the address in it, or null when it can be used as it is. */
export function addressNote(origin: string): string | null {
  if (onlyWorksHere(origin)) {
    return "You opened JimmyHQ at an address that only works on this computer. In the command, replace ADDRESS-OF-THIS-COMPUTER with the name or address this computer has on your private network, and make sure JimmyHQ answers to it (Settings, Access).";
  }
  if (origin.toLowerCase().startsWith("http://")) {
    return "This address is plain http. That is fine on a private network that encrypts, such as a mesh VPN. The other machine refuses it for an address that is not private unless you add --allow-plain-http.";
  }
  return null;
}

/** How long a join code still works, or that it has run out. */
export function codeLife(expiresMs: number, now: number): { live: boolean; text: string } {
  const left = expiresMs - now;
  if (left <= 0) return { live: false, text: "This code has run out. Make a new one." };
  const minutes = Math.ceil(left / 60_000);
  return { live: true, text: `Works once, for ${minutes} more minute${minutes === 1 ? "" : "s"}. It is not shown again.` };
}

/** Oldest first, so a list does not jump about as nodes connect and drop. */
export function sortNodes(nodes: readonly NodeRecord[]): NodeRecord[] {
  return [...nodes].sort((a, b) => a.joined_ms - b.joined_ms || a.name.localeCompare(b.name));
}

/** Whether a Tape event changes what this screen shows. */
export function affectsNodes(event: Pick<TapeEvent, "kind">): boolean {
  return event.kind.startsWith("node.") || event.kind === "model.role.set";
}
