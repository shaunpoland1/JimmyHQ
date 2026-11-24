// The words the server uses for effects, levels and who may receive. Kept apart from the
// API client so pure logic and its tests can use them without loading the network code.

export const EFFECTS = ["read", "send", "create", "update", "delete", "spend", "access", "change"] as const;
export type Effect = (typeof EFFECTS)[number];
export const LEVELS = ["read", "ask", "alone"] as const;
export type Level = (typeof LEVELS)[number];
export const RECIPIENT_LIMITS = ["owner", "known", "anyone"] as const;
export type RecipientLimit = (typeof RECIPIENT_LIMITS)[number];
