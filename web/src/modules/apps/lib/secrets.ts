// An app's secrets on its own Settings page: the owner types one in, and it is never
// shown again. Pure, so each rule is tested (web/tests/apps.secrets.test.ts).
//
//   GET    /api/apps/<id>/secrets          -> { secrets: [{ name, label, help, set }] }
//   PUT    /api/apps/<id>/secrets/<name>   body { value }  -> { name, set: true }
//   DELETE /api/apps/<id>/secrets/<name>   -> { name, set: false }
//
// The server answers "set" or "not set" and never the value. This screen never keeps a
// value after it is sent: the field is emptied whether the answer is yes or no.

const APP_ID = /^[a-z][a-z0-9_]{1,30}$/;
const SECRET_NAME = /^[a-z][a-z0-9_]{0,39}$/;
// An app's own Settings page is `app.<id>` (server/jimmyhq/apps/plugs/settings.py).
const APP_PAGE = /^app\.([a-z][a-z0-9_]{1,30})$/;

export type SecretState = { name: string; label: string; help: string; set: boolean };

/** The app whose own Settings page this is, or null for any other page. */
export function appIdOfSection(sectionId: string): string | null {
  const found = APP_PAGE.exec(sectionId);
  return found?.[1] ?? null;
}

export function secretsPath(appId: string): string | null {
  return APP_ID.test(appId) ? `/api/apps/${appId}/secrets` : null;
}

/** The address of one secret, or null unless both parts are exactly an app id and a secret's name. */
export function secretPath(appId: string, name: string): string | null {
  const base = secretsPath(appId);
  return base && SECRET_NAME.test(name) ? `${base}/${name}` : null;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** The list the server sent, keeping only well-formed entries. Anything like a value is dropped. */
export function readSecrets(raw: unknown): SecretState[] {
  const list = isObject(raw) && Array.isArray(raw.secrets) ? raw.secrets : [];
  return list.flatMap((item): SecretState[] => {
    if (!isObject(item) || typeof item.name !== "string" || !SECRET_NAME.test(item.name)) return [];
    return [
      {
        name: item.name,
        label: typeof item.label === "string" && item.label ? item.label.slice(0, 60) : item.name,
        help: typeof item.help === "string" ? item.help.slice(0, 300) : "",
        set: item.set === true,
      },
    ];
  });
}

/** What the field holds, as it will be sent: the spaces and line ending of a paste taken off. */
export function cleanSecret(value: string): string {
  return value.trim();
}

/** Null when the field can be saved, or the sentence that says why not. Never repeats the value. */
export function secretProblem(value: string, maxChars = 4000): string | null {
  const clean = cleanSecret(value);
  if (!clean) return "Paste the key or token first. Nothing was saved.";
  if (clean.length > maxChars) return `A secret can be at most ${maxChars} characters. Nothing was saved. Check that only the key or token was pasted.`;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(clean)) return "A secret is one line of text. Nothing was saved. Paste only the key or token.";
  return null;
}

export function secretBody(value: string): { value: string } {
  return { value: cleanSecret(value) };
}

export function statusWord(state: Pick<SecretState, "set">): "Set" | "Not set" {
  return state.set ? "Set" : "Not set";
}

/** The question the Clear button asks first, in the app's ConfirmDialog. */
export function clearQuestion(appName: string, state: Pick<SecretState, "label">): { title: string; message: string } {
  return {
    title: `Clear ${state.label}?`,
    message: `${appName} will no longer have it. Anything that needs it stops working until you type it in again.`,
  };
}
