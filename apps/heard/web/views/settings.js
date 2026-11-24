// Settings: how to connect a recorder, what has been heard and refused, the few settings
// that matter (changed on JimmyHQ's own page for this app), and your data: export, import
// and delete. Importing and deleting ask inside the page, never in the browser's own box.
import { dialog, field, fill, h, say, why } from "../_ui.js";
import { batches, importWords, plural, readExport, whenWords } from "../lib/format.js";

/** The steps to connect a recorder, as a list. Shown here and on an empty first screen. */
export function setupSteps() {
  return h("ol", { class: "steps" },
    h("li", {}, "Open Heard's page in Apps. Under \"Messages from your devices\", make a key, name it (such as \"Pendant\") and tick \"For a sender that can only be given an address\"."),
    h("li", {}, "The page then shows one address for each kind of message, once, with a copy button. In your Omi app, under Developer Settings, paste the address labelled \"Omi: Memory Creation Webhook\" into the Memory Creation Webhook field and, if you want words as they are said, the one labelled \"Omi: Real-Time Transcript Webhook\" into the Real-Time Transcript Webhook field. Leave the Audio Bytes Webhook empty: Heard keeps words only."),
    h("li", {}, "If Heard's page says it expects larger messages than you allow, raise \"Largest message from a device\" under Settings, Apps."),
    h("li", {}, "Say something near the pendant and open Conversations. A finished conversation appears once Omi has closed it, which it does after a quiet spell."));
}

export async function show(ctx, root, current) {
  await ctx.reloadOverview();
  if (!current()) return;
  const o = ctx.state.overview;
  const problem = h("p", { class: "problem", role: "alert" });
  const importNote = h("p", { class: "notice", role: "status", hidden: true });
  const parts = h("div", { class: "row" });

  async function exportPlan() {
    problem.textContent = "";
    try {
      const plan = await ctx.call("export.parts", {});
      fill(parts, plan.parts.length
        ? plan.parts.map((p) => h("button", { type: "button", class: "btn", onclick: () => savePart(p.index, plan.parts.length) },
          plan.parts.length === 1 ? "Save the file" : `Save part ${p.index + 1} of ${plan.parts.length}`))
        : h("span", { class: "muted" }, "There is nothing to export yet."));
      if (plan.parts.length > 1) say("Everything is too big for one file, so it is in parts. Save each one.", "ok");
    } catch (error) {
      problem.textContent = why(error);
    }
  }

  async function savePart(index, total) {
    problem.textContent = "";
    try {
      const out = await ctx.call("export.part", { index });
      await ctx.save({ name: out.name, type: "application/json", text: out.text });
      say(`Part ${index + 1} of ${total} is ready to save.`, "ok");
    } catch (error) {
      problem.textContent = why(error);
    }
  }

  async function importFile(file) {
    problem.textContent = "";
    importNote.hidden = true;
    let text;
    try {
      text = await file.text();
    } catch {
      problem.textContent = "That file could not be read, so nothing was imported.";
      return;
    }
    const read = readExport(text);
    if (read.error) {
      problem.textContent = read.error;
      return;
    }
    let made = 0;
    let lines = 0;
    let skipped = 0;
    try {
      for (const group of batches(read.conversations)) {
        const done = await ctx.call("import.batch", { conversations: group });
        made += done.conversations;
        lines += done.lines;
        skipped += done.skipped_count;
      }
    } catch (error) {
      problem.textContent = why(error);
    }
    importNote.hidden = false;
    importNote.textContent = importWords(made, lines, skipped);
    await ctx.reloadOverview();
  }

  function askToDeleteAll() {
    const word = h("input", { autocomplete: "off", spellcheck: "false", "aria-label": "Type DELETE to confirm", placeholder: "DELETE" });
    return dialog({
      title: "Delete everything?",
      intro: "Every conversation, line, summary and to-do Heard holds is deleted for good, and overwritten so it cannot be read back. This cannot be undone. Save a copy first if you want one.",
      body: field("Type the word DELETE to go ahead", word),
      actions: [{
        label: "Delete everything", kind: "danger",
        run: async () => {
          if (word.value.trim() !== "DELETE") return "Type the word DELETE, in capital letters, to go ahead. Nothing was deleted.";
          const done = await ctx.call("delete.all", { typed: "DELETE" });
          say(done.said, "ok");
          return undefined;
        },
      }],
    }).then(async (label) => { if (label) await ctx.go("settings"); });
  }

  const picker = h("input", { type: "file", accept: ".json,application/json", "aria-label": "Choose a Heard export to import" });
  picker.addEventListener("change", () => { if (picker.files?.[0]) importFile(picker.files[0]); });

  fill(root,
    h("h1", { class: "visually-hidden" }, "Settings"),
    h("section", { class: "box" },
      h("h2", {}, "Connect your recorder"),
      setupSteps(),
      h("p", { class: "muted small" }, "Heard keeps words only. If a message carries audio, or the address of audio, it is ignored and never fetched."),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", onclick: () => ctx.openSettings().catch((e) => say(why(e), "bad")) }, "Open this app's settings"))),
    h("section", { class: "box" },
      h("h2", {}, "What Heard has heard"),
      o.connected
        ? h("p", {}, `${plural(o.conversations, "conversation", "conversations")} kept. The last message came in on ${whenWords(o.last_heard_ms, ctx.zone)}.`)
        : h("p", { class: "muted" }, "Nothing has arrived yet."),
      o.refused.length
        ? h("div", {}, h("p", { class: "muted small" }, "Messages that could not be kept. What they said is never stored."),
          h("ul", { class: "rows" }, o.refused.map((r) => h("li", {}, h("span", {}, `${r.words} `), h("span", { class: "muted small" }, `Seen ${plural(r.n, "time", "times")}.`)))))
        : null),
    h("section", { class: "box" },
      h("h2", {}, "How it is set"),
      h("ul", { class: "rows" },
        h("li", {}, `A conversation ends after ${plural(o.gap_minutes, "minute", "minutes")} of quiet.`),
        h("li", {}, o.keep_days ? `Conversations older than ${plural(o.keep_days, "day", "days")} are deleted for good.` : "Conversations are kept until you delete them."),
        h("li", {}, o.only_recorder ? "Only messages that carry the recorder id you chose are kept." : "Messages from any recorder are kept, with the recorder's id."),
        h("li", {}, o.model_reads ? "A model on this computer may read one conversation when you ask." : "No model may read your conversations."),
        h("li", {}, o.file_tasks ? "An accepted to-do is also added to your tasks, with its title only." : "An accepted to-do stays in Heard only."),
        h("li", {}, o.fast_search ? "Search uses this computer's full-text index." : "Search scans the lines one by one.")),
      h("p", { class: "muted small" }, "Change these, and the rest, on this app's own settings page."),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => ctx.openSettings().catch((e) => say(why(e), "bad")) }, "Change settings"))),
    h("section", { class: "box" },
      h("h2", {}, "Your data"),
      h("p", { class: "muted" }, "Export everything as JSON files, and import them back. A very large set is saved in parts. Importing twice adds nothing twice."),
      problem,
      h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", onclick: exportPlan }, "Export everything")),
      parts,
      h("div", { class: "row" }, field("Import an export", picker)),
      importNote),
    h("section", { class: "danger-zone" },
      h("h2", {}, "Danger"),
      h("p", {}, "Delete everything removes every conversation for good. Nothing can bring it back."),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn danger", onclick: askToDeleteAll }, "Delete everything"))));
}
