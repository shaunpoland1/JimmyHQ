// The remote access module's API client. See docs/remote.md for the routes.

import { request } from "../../api";

export type RemoteState = "not_installed" | "not_running" | "needs_login" | "stopped" | "ready" | "unknown";

export type RemoteStatus = {
  state: RemoteState;
  /** A sentence that says what to do when Tailscale cannot be used yet, or null. */
  problem: string | null;
  /** The "Path to the Tailscale command" setting points at nothing. */
  path_missing: boolean;
  /** Tailscale's own word for its state, such as Running. */
  backend_state: string | null;
  /** This computer's name on the tailnet. Only known when Tailscale is ready. */
  name: string | null;
  /** Whether HTTPS certificates are switched on for the tailnet. Null: this Tailscale did not say. */
  https_enabled: boolean | null;
  /** JimmyHQ's own port. 0 when it is not known. */
  port: number;
  /** Whether the list of what Tailscale serves could be read. */
  serve_known: boolean;
  /** An address on the tailnet already forwards to JimmyHQ. */
  mapped: boolean;
  address: string | null;
  /** The mapping is open to the whole internet (Tailscale Funnel). */
  public: boolean;
  /** Something else is already served on the port that would be used. */
  clash: boolean;
  /** The name is already in the allowed hosts. */
  allowed: boolean;
  /** This request comes from the computer JimmyHQ runs on. */
  from_this_computer: boolean;
  commands: { on: string | null; off: string | null; status: string; allowed_hosts_value: string | null };
};

export type TurnOnResult = { ran: string | null; allowed_added: boolean; status: RemoteStatus };
export type TurnOffResult = { ran: string | null; host_removed: boolean; status: RemoteStatus };

export const REMOTE_STATUS_KEY = ["remote", "status"] as const;

export const remoteApi = {
  status: () => request<RemoteStatus>("GET", "/api/remote/status"),
  /** Runs the one tailscale serve command, at this computer only, and allows the name. */
  turnOn: () => request<TurnOnResult>("POST", "/api/remote/tailscale/serve"),
  turnOff: (removeHost: boolean) => request<TurnOffResult>("DELETE", `/api/remote/tailscale/serve${removeHost ? "?remove_host=true" : ""}`),
  /** Adds the detected name to the allowed hosts and touches nothing else. */
  allowHost: () => request<{ added: boolean; status: RemoteStatus }>("POST", "/api/remote/allow-host"),
};
