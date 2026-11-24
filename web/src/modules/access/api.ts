// The owner's own account: change the password, sign out other browsers, and API tokens.
// The routes are in server/jimmyhq/web/app.py; see "Access" in docs/architecture.md.

import { request } from "../../api";

export type SessionsEnded = { ok: true; sessions_ended: number };

export type TokenLevel = "reader" | "writer";

/** An API token as the list shows it. The token itself is never in here. */
export type ApiToken = {
  id: string;
  label: string;
  level: TokenLevel;
  scopes: string[];
  created: number;
  revoked: number | null;
};

/** What making a token answers: the row, and the token itself, sent this once only. */
export type MadeToken = { token: ApiToken; secret: string };

export const accessApi = {
  /** A wrong current password is a 403 (this browser stays signed in), too many are a 429. */
  changePassword: (current: string, next: string) =>
    request<SessionsEnded>("POST", "/api/auth/password", { body: { current, new: next } }),
  endOtherSessions: () => request<SessionsEnded>("POST", "/api/auth/sessions/end-others"),
  /** The signed-in owner only; a token is refused with 403. */
  listTokens: () => request<{ tokens: ApiToken[] }>("GET", "/api/auth/tokens"),
  makeToken: (label: string, level: TokenLevel) => request<MadeToken>("POST", "/api/auth/tokens", { body: { label, level } }),
  revokeToken: (id: string) => request<{ token: ApiToken }>("POST", `/api/auth/tokens/${encodeURIComponent(id)}/revoke`),
};
