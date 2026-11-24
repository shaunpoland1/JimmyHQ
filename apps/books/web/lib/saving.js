// Saving one file for the owner. The platform's limit on a saved file is the owner's own
// setting, and a page is never told it, so the page does not guess a number: it saves the
// whole file and listens for the platform's refusal (`too_large`). Only then does it try
// the file packed tighter (JSON can lose its line breaks), and only if that is still too
// large does it say so, naming the setting. No DOM, so it is tested with node.

export const SETTING = "Largest file an app may offer you to keep (MB)";

/** Whether the platform refused a save because the file was over the owner's limit. */
export function isTooLarge(error) {
  return Boolean(error) && error.code === "too_large";
}

/** The same file with its JSON packed onto one line, or null when that would not be smaller. */
export function packed(file) {
  if (!file || file.type !== "application/json") return null;
  try {
    const text = JSON.stringify(JSON.parse(file.text));
    return text.length < file.text.length ? { ...file, text } : null;
  } catch {
    return null;
  }
}

/**
 * Hand `file` ({name, type, text}) to `save` (hq.ui.save). Resolves `{saved, packed}` once
 * the platform has taken it, or `{tooLarge: true}` when even the packed form is over the
 * owner's limit. Any other refusal is thrown for the caller to say.
 */
export async function saveWhole(file, save) {
  try {
    return { saved: await save(file), packed: false };
  } catch (error) {
    if (!isTooLarge(error)) throw error;
  }
  const smaller = packed(file);
  if (smaller) {
    try {
      return { saved: await save(smaller), packed: true };
    } catch (error) {
      if (!isTooLarge(error)) throw error;
    }
  }
  return { tooLarge: true };
}

/** A size in words: "850 KB" or "12.3 MB" (decimal, as the platform counts). */
export function sizeWords(bytes) {
  const n = Number(bytes) || 0;
  return n >= 1_000_000 ? `${Math.round(n / 100_000) / 10} MB` : `${Math.max(1, Math.round(n / 1000))} KB`;
}

/** The sentence for a file that is over the owner's limit even when packed. */
export function tooLargeWords(bytes, what) {
  const instead = what === "json" ? "save each account as CSV instead" : "save one account at a time";
  return `That file is ${sizeWords(bytes)}, more than the largest file an app may offer you to keep. Raise "${SETTING}" under Settings, Apps (it is among the advanced ones), or ${instead}. Nothing was saved.`;
}
