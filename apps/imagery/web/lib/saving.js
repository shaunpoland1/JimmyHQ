// Saving pictures: what the page hands JimmyHQ's save (`hq.ui.save`), and what it says when
// JimmyHQ stops it. Pure: no DOM, no bridge, so each rule is tested. The page passes in the
// calls it makes (`picture`, `save`) and the sentence maker (`why`).
//
// JimmyHQ lets an app hand the owner only a few files from one click (Settings, Apps,
// "Most files an app may offer from one click"), only so many in a minute, each no larger
// than "Largest file an app may offer you to keep (MB)", and each within a few seconds of
// the owner's click or of the file before it. The page cannot see those numbers, so
// "Save all starred" saves one picture after another and, when JimmyHQ refuses one, stops
// and says so. It never retries by itself, since only the owner's next click may save more.

/** The picture types JimmyHQ's save takes. */
export const PICTURE_TYPES = ["image/png", "image/jpeg", "image/webp"];

/** The picture type a file's first bytes say it is, or null. */
export function sniffType(bytes) {
  const starts = (...head) => head.every((value, at) => bytes[at] === value);
  if (starts(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)) return "image/png";
  if (starts(0xff, 0xd8, 0xff)) return "image/jpeg";
  const tag = (from, word) => [...word].every((char, at) => bytes[from + at] === char.charCodeAt(0));
  if (bytes.length >= 12 && tag(0, "RIFF") && tag(8, "WEBP")) return "image/webp";
  return null;
}

/**
 * The picture as a Blob of the type its bytes say (JimmyHQ's draw may hand back the stored
 * file or a redrawn PNG or JPEG, with whatever type the browser gave it), or null when it
 * is none of the three types the save takes. `fallback` is the type Imagery stored.
 */
export async function typedPicture(blob, fallback) {
  if (!blob || typeof blob.slice !== "function") return null;
  const head = new Uint8Array(await blob.slice(0, 16).arrayBuffer());
  const type = sniffType(head) ?? (PICTURE_TYPES.includes(fallback) ? fallback : null);
  if (!type) return null;
  return blob.type === type ? blob : new Blob([blob], { type });
}

/** A sentence for the owner when JimmyHQ cannot hand over the picture or was handed none. */
export const NOT_A_PICTURE = "That picture is not a PNG, JPEG or WebP file JimmyHQ can save, so nothing was saved.";

/**
 * Words for a refusal of the save call. JimmyHQ's own sentence for the owner is shown as it
 * is. A picture larger than the owner lets an app hand over has none (its message is for the
 * app's author), so Imagery says it in its own words.
 */
export function saveRefusal(error, why) {
  if (error && error.code === "too_large" && typeof error.owner_message !== "string") {
    return "This picture is larger than the largest file JimmyHQ lets an app offer you to keep, so it was not saved. Raise \"Largest file an app may offer you to keep (MB)\" under Settings, Apps, then try again.";
  }
  return why(error);
}

function longest(width, height) {
  return Math.max(Number(width) || 0, Number(height) || 0);
}

/**
 * A sentence when the picture JimmyHQ drew is smaller than the one Imagery keeps (JimmyHQ
 * draws a picture no larger than "Longest side of a picture shown in full (pixels)"), else "".
 * `row` holds the kept size.
 */
export function smallerNote(row, shownWidth, shownHeight) {
  const kept = longest(row?.width, row?.height);
  const shown = longest(shownWidth, shownHeight);
  if (!kept || !shown || shown >= kept) return "";
  return `This is ${shownWidth} by ${shownHeight} pixels, smaller than the ${row.width} by ${row.height} Imagery keeps. To see and save it larger, raise "Longest side of a picture shown in full (pixels)" under Settings, Apps, advanced.`;
}

/**
 * Save the pictures in `rows` one after another, from one click of the owner's.
 *   io.picture(row)        the picture as a Blob (JimmyHQ's full view)
 *   io.save(row, blob)     JimmyHQ's save, resolving once the file was handed over
 *   io.sentence(error, stage)  the owner's words for a refusal; `stage` is "picture" or "save"
 *   skip                   ids already saved by an earlier press that stopped
 * Returns {saved, skipped, stopped, left, pending}: ids saved now; those left out because
 * they are too large to show or save, with the sentence; the refusal that stopped the run
 * ({code, said}) or null; the ids still to save; how many were to be saved now. A refusal
 * that is about one picture (too large) leaves that one out and goes on; any other stops.
 */
export async function saveAll(rows, io, skip = new Set()) {
  const todo = rows.filter((row) => !skip.has(row.id));
  const saved = [];
  const skipped = [];
  let stopped = null;
  for (const row of todo) {
    let stage = "picture";
    try {
      const blob = await io.picture(row);
      stage = "save";
      await io.save(row, blob);
      saved.push(row.id);
    } catch (error) {
      const said = io.sentence(error, stage);
      if (error && error.code === "too_large") skipped.push({ id: row.id, said });
      else {
        stopped = { code: error && error.code ? error.code : "", said };
        break;
      }
    }
  }
  const done = new Set([...saved, ...skipped.map((one) => one.id)]);
  const left = todo.filter((row) => !done.has(row.id)).map((row) => row.id);
  return { saved, skipped, stopped, left, pending: todo.length };
}

function pictures(n) {
  return `${n} picture${n === 1 ? "" : "s"}`;
}

/** What "Save all starred" says when it is done or has stopped: how many, why it stopped, what to do. */
export function saveAllLine(result) {
  const { saved, skipped, stopped, left, pending } = result;
  const parts = [];
  if (!pending) return "There is no starred picture left to save.";
  if (stopped) {
    parts.push(saved.length
      ? `Saved ${saved.length} of ${pending}, then stopped.`
      : "Nothing was saved.");
    parts.push(stopped.said);
  } else {
    parts.push(`Saved ${saved.length === pending && pending > 1 ? "all " : ""}${pictures(saved.length)}.`);
  }
  for (const one of skipped) parts.push(`One was left out: ${one.said}`);
  if (left.length) {
    parts.push(`${left.length === 1 ? "1 starred picture is" : `${left.length} starred pictures are`} not saved yet. Press the button again for ${left.length === 1 ? "it" : "them"}.`);
  }
  return parts.join(" ");
}

/** The button's words: all of them, or what is left after a run that stopped. */
export function saveAllLabel(starred, left) {
  return left > 0 ? `Save the other ${left} starred` : `Save all starred (${starred})`;
}
