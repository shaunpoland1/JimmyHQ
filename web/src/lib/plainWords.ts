// Plain words for the ids the system uses inside: who filed something and what kind of
// event or card it is. Screens made for a person show these; the raw id stays in a details
// view where one exists. Pure, so it is tested.

/** "books_read" as "Books read". */
export function titleWords(id: string): string {
  const words = id.replace(/[._-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : id;
}

/** `app.<id>.<rest>`: the app's id and the rest of the kind, or null for a kind that is not an app's. */
function appKind(kind: string): { app: string; rest: string } | null {
  const parts = kind.split(".");
  if (parts[0] !== "app" || parts.length < 3 || !parts[1]) return null;
  return { app: parts[1], rest: parts.slice(2).join(".") };
}

/**
 * A card or event kind in words. An app's kind loses its `app.<id>.` front: "app.books.due"
 * is "Due" where the app's name is already beside it, and "Books: due" where `withApp` says
 * the name is not. Any other kind reads as a phrase ("task.created" is "Task created").
 */
export function plainKind(kind: string, withApp = false): string {
  const app = appKind(kind);
  if (app) {
    const rest = titleWords(app.rest);
    return withApp ? `${titleWords(app.app)}: ${rest.charAt(0).toLowerCase()}${rest.slice(1)}` : rest;
  }
  return titleWords(kind);
}

/**
 * Who did something, in words: "You" for the owner, an app by its name (the card's own
 * `source` when it has one), anyone else by the part after the colon.
 */
export function plainActor(actor: string, appName?: string | null): string {
  if (actor === "owner") return "You";
  if (actor === "system") return "JimmyHQ";
  const colon = actor.indexOf(":");
  if (colon <= 0) return actor;
  const prefix = actor.slice(0, colon);
  const rest = actor.slice(colon + 1).trim();
  if (prefix === "app") return appName?.trim() || titleWords(rest);
  return rest || actor;
}
