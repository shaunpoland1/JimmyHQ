// Query keys for the chat module.

export const CHAT_KEY = ["chat"] as const;
export const CHAT_OPTIONS_KEY = ["chat", "options"] as const;
export const CHAT_LIST_KEY = ["chat", "list"] as const;

/** Every list of one kind (the open ones or the archived ones), whatever the search. */
export function listsKey(archived: boolean) {
  return [...CHAT_LIST_KEY, archived ? "archived" : "open"] as const;
}

/** One list: `search` is the title search as `searchQuery` leaves it, "" for none. */
export function listKey(archived: boolean, search = "") {
  return [...listsKey(archived), search] as const;
}

export function conversationKey(id: string) {
  return ["chat", "conversation", id] as const;
}

/** The passages one reply was given, read as they stand now. */
export function memoryKey(conversationId: string, messageId: string) {
  return ["chat", "memory", conversationId, messageId] as const;
}
