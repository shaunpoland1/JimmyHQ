// Plain wording and decisions for the "Reach JimmyHQ from your other devices" card.
// No React and no network: everything here is a function of the server's status.

import type { RemoteStatus, TurnOffResult, TurnOnResult } from "../api";

export type Tone = "ok" | "warn" | "off";
export type Headline = { tone: Tone; label: string; detail: string };

/** The label of the generated setting that holds the allowed names, so the card can point to it. */
export const HOSTS_LABEL = "Other names for this server";

/** The HTTPS port of the tailnet address that forwards to JimmyHQ, read from the "off" command the server built. */
export function mappedPort(status: RemoteStatus): number {
  const found = /--https=(\d+)/.exec(status.commands.off ?? "");
  return found ? Number(found[1]) : status.port;
}

/** What the card says about where things stand, in a few plain words and a sentence. */
export function headline(status: RemoteStatus): Headline {
  if (status.state !== "ready") {
    const label = {
      not_installed: "Tailscale is not installed",
      not_running: "Tailscale is not running",
      needs_login: "Tailscale is not signed in",
      stopped: "Tailscale is turned off",
      unknown: "Tailscale could not be read",
    }[status.state];
    return { tone: "off", label, detail: status.problem ?? "" };
  }
  if (status.mapped && status.public) {
    return {
      tone: "warn",
      label: "Open to the public internet",
      detail: `Tailscale Funnel is switched on for this address, so anyone can reach it, not only your own devices. JimmyHQ refuses what arrives that way and will not add this name to the allowed hosts while it is public. Turn Funnel off with “tailscale funnel --https=${mappedPort(status)} off” in a terminal on this computer, or turn this mapping off below.`,
    };
  }
  if (status.mapped && status.allowed) {
    return { tone: "ok", label: "Reachable from your devices", detail: "Open the address below on a device signed in to your Tailscale network." };
  }
  if (status.mapped) {
    return { tone: "warn", label: "Almost there", detail: "Tailscale forwards to JimmyHQ, but JimmyHQ does not answer to this name yet. Add it to the allowed hosts." };
  }
  if (!status.name) {
    return { tone: "warn", label: "Tailscale has no name for this computer", detail: status.problem ?? "" };
  }
  if (status.clash) {
    return {
      tone: "warn",
      label: "That address is in use",
      detail: `Something else is already served on port ${status.port} of your tailnet address. JimmyHQ will not replace it. Turn that off first, or do it yourself with another port.`,
    };
  }
  return { tone: "off", label: "Ready to turn on", detail: "Tailscale is running and signed in. JimmyHQ cannot be reached from your other devices yet." };
}

export type Actions = {
  /** Show "Turn on". */
  turnOn: boolean;
  /** Show "Turn off". */
  turnOff: boolean;
  /** Show "Add this address to allowed hosts". */
  allowHost: boolean;
  /** A step that changes this computer is wanted but this page is open on another device. */
  atMachine: boolean;
};

/**
 * Which buttons to offer. Changing what this computer opens to a network is
 * done from the computer itself; from another device the buttons are replaced
 * by a sentence. Adding a name to the allowed hosts can be done from anywhere,
 * but never while Funnel makes the address public: the server refuses it then.
 */
export function actions(status: RemoteStatus): Actions {
  const none = { turnOn: false, turnOff: false, allowHost: false, atMachine: false };
  if (status.state !== "ready" || !status.name) return none;
  const wantsOn = !status.mapped && !status.clash && status.port > 0;
  const wantsOff = status.mapped;
  return {
    turnOn: wantsOn && status.from_this_computer,
    turnOff: wantsOff && status.from_this_computer,
    allowHost: !status.allowed && !(status.mapped && status.public),
    atMachine: (wantsOn || wantsOff) && !status.from_this_computer,
  };
}

/** What a click on "Turn on" will do, for the confirmation. */
export function turnOnPlan(status: RemoteStatus): { command: string; changes: string[] } {
  const changes = [
    `Tailscale starts forwarding HTTPS on port ${status.port} of your tailnet address to JimmyHQ on this computer. It stays on after a restart until you turn it off.`,
    status.allowed
      ? "The name is already in the allowed hosts, so nothing changes there."
      : `The name ${status.name ?? "of this computer"} is added to “${HOSTS_LABEL}” so JimmyHQ answers to it.`,
    "Only devices signed in to your own Tailscale network can reach it. It is not opened to the public internet.",
  ];
  return { command: status.commands.on ?? "", changes };
}

/** What a click on "Turn off" will do, for the confirmation. */
export function turnOffPlan(status: RemoteStatus): { command: string; changes: string[] } {
  const changes = ["Tailscale stops forwarding to JimmyHQ. Your other devices can no longer open it.", "Nothing else in Tailscale changes."];
  return { command: status.commands.off ?? "", changes };
}

/** One line after "Turn on" worked. */
export function turnedOnLine(result: TurnOnResult): string {
  const where = result.status.address ? ` Open ${result.status.address} on your other device.` : "";
  return `${result.ran ? "Tailscale is now forwarding to JimmyHQ." : "It was already on."}${where}`;
}

/** One line after "Turn off" worked. */
export function turnedOffLine(result: TurnOffResult): string {
  const base = result.ran ? "Tailscale no longer forwards to JimmyHQ." : "It was already off.";
  return result.host_removed ? `${base} The name was removed from the allowed hosts.` : base;
}

export type Step = { title: string; text: string; command?: string };

/** The three steps for doing it by hand, from the real port and name where they are known. */
export function steps(status: RemoteStatus): Step[] {
  const name = status.name ?? status.commands.allowed_hosts_value ?? "<machine-name>.<tailnet-name>.ts.net";
  const address = status.address ?? (status.port > 0 ? `https://${name}${status.port === 443 ? "" : `:${status.port}`}` : `https://${name}`);
  return [
    {
      title: "Map your Tailscale address to JimmyHQ",
      text: "On this computer, in a terminal (on Linux add sudo, on Windows use a terminal opened as administrator). Tailscale reaches only your own devices this way.",
      command: status.commands.on ?? `tailscale serve --bg --https=<port> http://127.0.0.1:<port>`,
    },
    {
      title: "Let JimmyHQ answer to that name",
      text: `Add ${name} to “${HOSTS_LABEL}” in Settings, Access (or to allowed_hosts in config.toml, then restart). Without it JimmyHQ refuses the address.`,
    },
    {
      title: "Open it on your other device",
      text: `Sign in to the same Tailscale network there, then open ${address} and sign in to JimmyHQ.`,
    },
  ];
}

/** True for a tape event after which the status may read differently. */
export function affectsRemoteStatus(event: { kind: string; payload?: Record<string, unknown> }): boolean {
  if (event.kind.startsWith("remote.")) return true;
  if (event.kind === "setting.updated" || event.kind === "setting.reset") {
    const key = event.payload?.key;
    return typeof key === "string" && (key === "access.allowed_hosts" || key.startsWith("remote."));
  }
  return false;
}
