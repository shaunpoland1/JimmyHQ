// Saving from the page: the details of a picture as a note, the picture itself, and every
// starred picture one after another. JimmyHQ's save (`hq.ui.save`) needs the owner's own
// click just before, so each of these is called from a button's click handler. What each
// returns is {ok, text}: the sentence to show, never an exception.
import { why } from "../_ui.js";
import { detailsName, detailsText } from "../lib/format.js";
import { NOT_A_PICTURE, saveAll, saveAllLine, saveRefusal, smallerNote, typedPicture } from "../lib/saving.js";

/** The size in pixels of a picture JimmyHQ drew, or null when the browser cannot say. */
export async function measure(blob) {
  try {
    const bitmap = await createImageBitmap(blob);
    const size = { width: bitmap.width, height: bitmap.height };
    bitmap.close?.();
    return size;
  } catch {
    return null;
  }
}

export async function saveDetails(ctx, row) {
  try {
    const done = await ctx.save({ name: detailsName(row), type: "text/markdown", text: detailsText(row, ctx.zone) });
    return { ok: true, text: `Saved the details as ${done.name}.` };
  } catch (error) {
    return { ok: false, text: saveRefusal(error, why) };
  }
}

/**
 * Save one picture. `loaded` is the picture's Blob, or a promise of it, when the page
 * already has it (the large view); otherwise JimmyHQ draws it now.
 */
export async function savePicture(ctx, row, loaded = null) {
  try {
    let blob = null;
    try {
      blob = loaded ? await loaded : null;
    } catch {
      blob = null; // the large view failed: ask again, as the save is the owner's own click
    }
    blob ??= await ctx.picture(row.ref);
    const typed = await typedPicture(blob, row.mime);
    if (!typed) return { ok: false, text: NOT_A_PICTURE };
    const done = await ctx.save({ name: detailsName(row), blob: typed });
    const size = await measure(typed);
    const note = size ? smallerNote(row, size.width, size.height) : "";
    return { ok: true, text: `Saved the picture as ${done.name}.${note ? ` ${note}` : ""}` };
  } catch (error) {
    return { ok: false, text: saveRefusal(error, why) };
  }
}

/**
 * Save every starred picture, one after another, from one click. JimmyHQ allows only so many
 * from one click and in a minute: when it refuses one, the run stops and says why and how
 * many are left. `resume` holds the ids a run that stopped already saved, so the next press
 * goes on with the rest.
 */
export async function saveAllStarred(ctx, resume) {
  const got = await ctx.call("pictures.list", { starred: true, limit: 200, offset: 0 });
  const result = await saveAll(got.rows, {
    picture: (row) => ctx.picture(row.ref),
    save: async (row, blob) => {
      const typed = await typedPicture(blob, row.mime);
      if (!typed) throw Object.assign(new Error(NOT_A_PICTURE), { code: "invalid" });
      return ctx.save({ name: detailsName(row), blob: typed });
    },
    sentence: (error) => saveRefusal(error, why),
  }, resume);
  const more = got.total > got.rows.length
    ? ` Only the newest ${got.rows.length} of your ${got.total} starred pictures were listed.` : "";
  return { result, text: saveAllLine(result) + more };
}
