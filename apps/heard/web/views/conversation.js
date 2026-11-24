// One conversation: who spoke and when, a summary, to-dos to accept or decline, and the
// housekeeping (rename a speaker, save as a file, delete for good). A model reads the
// conversation only when a button here is pressed, and only on this computer.
import { dialog, field, fill, h, say, why } from "../_ui.js";
import { clock, dayHeading, lengthWords, plural, speakerClass } from "../lib/format.js";
import { untilAnswered } from "../lib/poll.js";

export async function show(ctx, root, current) {
  const { id, line } = ctx.state.open;
  let data = await ctx.call("conversation", { id });
  if (!current()) return;
  const overview = ctx.state.overview;
  const view = { summaryNote: "", summaryBusy: false, todoNote: "", todoBusy: false, editing: null };
  const summaryBox = h("section", { class: "box" });
  const todoBox = h("section", { class: "box" });
  const peopleBox = h("section", { class: "box" });
  const transcript = h("ul", { class: "transcript" });

  async function reload() {
    data = await ctx.call("conversation", { id });
    drawAll();
  }

  // --- asking a model, then asking every moment whether it has answered ------------------

  async function wait(check, onDone, first) {
    try {
      const got = first ?? await untilAnswered({ check, stillWanted: () => current() });
      if (!got) return;
      await onDone(got);
    } catch (error) {
      say(why(error), "bad");
    }
  }

  async function summarise() {
    if (view.summaryBusy) return;
    view.summaryBusy = true;
    view.summaryNote = "Reading this conversation on your computer. This can take a minute, and you can carry on.";
    drawSummary();
    try {
      const started = await ctx.call("summarise", { id });
      await wait(() => ctx.call("summarise.check", { id }), async (got) => {
        view.summaryNote = got.said ?? "";
        if (got.summary) data = { ...data, summary: got.summary, has_summary: true };
      }, started.state === "running" ? null : started);
    } catch (error) {
      view.summaryNote = why(error);
    }
    view.summaryBusy = false;
    drawSummary();
  }

  async function pullOut() {
    if (view.todoBusy) return;
    view.todoBusy = true;
    view.todoNote = "Reading this conversation on your computer. This can take a minute, and you can carry on.";
    drawTodos();
    try {
      const started = await ctx.call("todos.ask", { id });
      await wait(() => ctx.call("todos.check", { id }), async (got) => {
        view.todoNote = got.said ?? "";
        if (got.todos) data = { ...data, todos: got.todos };
      }, started.state === "running" ? null : started);
    } catch (error) {
      view.todoNote = why(error);
    }
    view.todoBusy = false;
    drawTodos();
  }

  // Back on a conversation that was being read when the owner left: pick the question up.
  ctx.call("summarise.check", { id }).then((got) => {
    if (got.state === "running" && !view.summaryBusy) {
      view.summaryBusy = true;
      wait(() => ctx.call("summarise.check", { id }), async (done) => {
        view.summaryNote = done.said ?? "";
        if (done.summary) data = { ...data, summary: done.summary };
      }).finally(() => { view.summaryBusy = false; drawSummary(); });
      drawSummary();
    }
  }).catch(() => {});

  // --- the parts -----------------------------------------------------------------------------

  function modelHelp(asking) {
    if (overview.model_reads) {
      return h("p", { class: "muted small" }, `A model on this computer reads only this conversation, when you press ${asking}. Nothing is sent anywhere else.`);
    }
    return h("div", { class: "notice" },
      h("p", {}, "A model is not allowed to read your conversations. You can allow it in this app's settings."),
      h("button", { type: "button", class: "btn small", onclick: () => ctx.openSettings().catch((e) => say(why(e), "bad")) }, "Open settings"));
  }

  function drawSummary() {
    fill(summaryBox,
      h("h2", {}, "Summary"),
      data.overview ? h("p", { class: "muted small" }, "From your recorder:") : null,
      data.overview ? h("p", { class: "summary" }, data.overview) : null,
      data.summary ? h("p", { class: "summary" }, data.summary) : h("p", { class: "muted" }, "No summary yet."),
      modelHelp("Summarise"),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", disabled: view.summaryBusy || !overview.model_reads, "aria-busy": view.summaryBusy ? "true" : undefined, onclick: summarise },
          view.summaryBusy ? "Reading..." : data.summary ? "Summarise again" : "Summarise"),
        data.summary && !view.summaryBusy
          ? h("button", { type: "button", class: "btn", onclick: async () => {
            try {
              await ctx.call("summary.clear", { id });
              data = { ...data, summary: "", has_summary: false };
              view.summaryNote = "";
              drawSummary();
            } catch (error) {
              say(why(error), "bad");
            }
          } }, "Clear the summary")
          : null),
      view.summaryNote ? h("p", { class: "notice", role: "status" }, view.summaryNote) : null);
  }

  async function decide(todo, call, args, words) {
    try {
      const done = await ctx.call(call, { id: todo.id, ...args });
      data = { ...data, todos: (await ctx.call("conversation", { id })).todos };
      view.editing = null;
      say(done.said || words, "ok");
      drawTodos();
    } catch (error) {
      say(why(error), "bad");
    }
  }

  // Where a suggestion came from, in words: what the recorder itself lists (or anything kept
  // with no quote) has not been checked against what was said, and the page says so.
  function whereFrom(todo) {
    if (todo.source === "sender") return "From your recorder, not checked against what was said";
    if (!todo.quote) return "Kept with no quote from the conversation, not checked";
    return "Suggested by a model on this computer, with the words it rests on";
  }

  function suggestion(todo) {
    const editing = view.editing?.id === todo.id;
    const edit = view.editing;
    let input = null;
    if (editing) {
      input = h("input", { value: edit.text, "aria-label": "The to-do in your words", maxlength: "200" });
      input.addEventListener("input", () => { edit.text = input.value; });
    }
    return h("li", { class: "todo" },
      h("p", { class: "muted small" }, whereFrom(todo)),
      editing ? input : h("p", { class: "what" }, todo.text),
      todo.quote ? h("p", { class: "muted small" }, "What was said:") : null,
      todo.quote ? h("blockquote", { class: "quote" }, todo.quote) : null,
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn primary small", onclick: () => decide(todo, "todo.accept", editing ? { text: edit.text } : {}, "Kept.") }, editing ? "Accept as edited" : "Accept"),
        editing
          ? h("button", { type: "button", class: "btn small", onclick: () => { view.editing = null; drawTodos(); } }, "Cancel edit")
          : h("button", { type: "button", class: "btn small", onclick: () => { view.editing = { id: todo.id, text: todo.text }; drawTodos(); } }, "Edit"),
        h("button", { type: "button", class: "btn small", onclick: () => decide(todo, "todo.decline", {}, "Set aside.") }, "Decline")));
  }

  function accepted(todo) {
    return h("li", { class: "todo" },
      h("div", { class: "row spread" },
        h("p", { class: "what grow" }, todo.text, todo.task_id ? h("span", { class: "chip" }, "In your tasks") : null),
        h("button", { type: "button", class: "btn small", onclick: () => decide(todo, "todo.remove", {}, "Removed.") }, "Remove")));
  }

  function drawTodos() {
    const waiting = data.todos.filter((t) => t.status === "suggested");
    const kept = data.todos.filter((t) => t.status === "accepted");
    fill(todoBox,
      h("h2", {}, "To-dos"),
      modelHelp("Pull out to-dos"),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", disabled: view.todoBusy || !overview.model_reads, "aria-busy": view.todoBusy ? "true" : undefined, onclick: pullOut }, view.todoBusy ? "Reading..." : "Pull out to-dos")),
      view.todoNote ? h("p", { class: "notice", role: "status" }, view.todoNote) : null,
      waiting.length ? h("ul", {}, waiting.map(suggestion)) : null,
      kept.length ? h("h3", {}, "Accepted") : null,
      kept.length ? h("ul", {}, kept.map(accepted)) : null,
      !waiting.length && !kept.length ? h("p", { class: "muted" }, "None yet. Nothing becomes yours until you accept it.") : null);
  }

  function rename(speaker) {
    const input = h("input", { value: speaker.name, maxlength: "60", autocomplete: "off", "aria-label": "New name" });
    return dialog({
      title: `Rename ${speaker.name}`,
      intro: "The new name replaces it on every line of this conversation. Other conversations are not changed.",
      body: field("Name", input),
      actions: [{
        label: "Rename", kind: "primary",
        run: async () => {
          const name = input.value.trim();
          if (!name) return "Type the name to use. Nothing was changed.";
          const done = await ctx.call("speaker.rename", { id, from: speaker.name, to: name });
          say(done.said, "ok");
          return undefined;
        },
      }],
    }).then(async (label) => { if (label) await reload(); });
  }

  function drawPeople() {
    fill(peopleBox,
      h("h2", {}, "Who spoke"),
      h("div", { class: "speakers" }, data.speaker_list.map((s) => h("span", { class: "chip" },
        h("span", { class: speakerClass(s.name, data.speaker_list) }, s.name),
        h("span", { class: "muted" }, plural(s.lines, "line", "lines")),
        h("button", { type: "button", class: "btn small", onclick: () => rename(s) }, "Rename")))));
  }

  function drawTranscript() {
    fill(transcript, data.segments.map((s) => h("li", { "data-line": s.id, class: s.id === line ? "found" : "" },
      h("span", { class: "at" }, clock(s.start_ms, ctx.zone)),
      h("span", { class: "words" }, h("span", { class: `who ${speakerClass(s.speaker, data.speaker_list)}` }, s.speaker), s.text))));
  }

  function drawAll() {
    drawSummary();
    drawTodos();
    drawPeople();
    drawTranscript();
  }

  async function saveFile() {
    try {
      const out = await ctx.call("conversation.text", { id });
      await ctx.save({ name: out.name, type: "text/markdown", text: out.text });
      say("Your file is ready to save.", "ok");
    } catch (error) {
      say(why(error), "bad");
    }
  }

  function askToDelete() {
    return dialog({
      title: "Delete this conversation?",
      intro: "Every line, the summary and the to-dos kept here are deleted for good, and overwritten so they cannot be read back. A task you already added to your own tasks is not touched. This cannot be undone. Save a copy first if you want one.",
      actions: [{
        label: "Delete for good", kind: "danger",
        run: async () => {
          const done = await ctx.call("conversation.delete", { id });
          say(done.said, "ok");
          return undefined;
        },
      }],
    }).then(async (label) => { if (label) await ctx.closeConversation(); });
  }

  fill(root,
    h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => ctx.closeConversation() }, "Back")),
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, data.heading),
        h("p", { class: "muted" }, `${dayHeading(data.day, overview.today)} at ${clock(data.started_ms, ctx.zone)}, ${lengthWords(data.seconds)}, ${plural(data.words, "word", "words")}`),
        data.recorder ? h("p", { class: "muted small" }, `From recorder ${data.recorder}`) : null,
        data.open ? h("p", { class: "muted live" }, "Still listening: more may arrive.") : null),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", onclick: saveFile }, "Save as a file"),
        h("button", { type: "button", class: "btn danger", onclick: askToDelete }, "Delete"))),
    summaryBox, todoBox, peopleBox,
    h("h2", {}, "Transcript"), transcript);
  drawAll();
  if (line) transcript.querySelector(".found")?.scrollIntoView({ block: "center" });
}
