// Queue: approved and booked posts in slot order, under a calendar strip by day. Book
// (the proposed time filled in, in the page's time zone), Move, Edit, Posted by hand,
// Cancel, and for a post in trouble with its scheduler the ways out the server names:
// "I checked, it is not there" for no answer, Put it back in the queue or Decline for one
// that failed or vanished, Move to one time for one out of step.

import { headline, pageName, queue } from "../lib/lists.js";
import { calendar, formatSlot, fromInputValue, toInputValue } from "../lib/time.js";
import { askChips, buttonKind, cancelQuestion, troubleButtons, troubleChip, wayQuestion } from "../lib/trouble.js";
import { isPractice, notices, toolLabelOfPost, waitNotices } from "../lib/tools.js";
import { ask, dialog, field, h, say, why } from "../_ui.js";

const MINUTE = 60_000;

/** The notices that say what booking means for the pages on a tool that books nothing. */
export function banners(data, zone) {
  const when = (ms) => formatSlot(ms, zone, data.now_ms);
  return [...notices(data), ...waitNotices(data, when)].map((n) => n.kind === "bad"
    ? h("div", { class: "banner bad", role: "note" }, h("strong", {}, n.strong), n.text)
    : h("div", { class: n.kind === "practice" ? "banner practice" : "banner", role: "note" }, h("strong", {}, n.strong), n.text));
}

export function render(ctx) {
  const data = ctx.data();
  const rows = queue(data.posts);
  const zone = ctx.ownerZone;
  const strip = calendar(rows, { zone, nowMs: data.now_ms, days: data.calendar_days || 14 });
  const calendarStrip = h("ol", { class: "strip", "aria-label": `The next ${strip.days.length} days` }, strip.days.map((day) => h("li", { class: `${day.today ? "today" : ""} ${day.posts.length ? "busy" : ""}`.trim() || undefined },
    h("span", { class: "small" }, day.label),
    h("span", { class: "count", "aria-label": `${day.posts.length} posts` }, day.posts.length ? String(day.posts.length) : "·"))));
  const extra = [strip.later ? `${strip.later} later` : "", strip.unset ? `${strip.unset} with no time yet` : ""].filter(Boolean).join(", ");
  return h("div", {},
    banners(data, zone),
    h("h2", {}, "Calendar"),
    calendarStrip,
    extra ? h("p", { class: "small muted" }, `Also: ${extra}.`) : null,
    h("h2", {}, rows.length ? `In the queue (${rows.length})` : "In the queue"),
    rows.length ? h("ol", { class: "rows" }, rows.map((post) => row(ctx, post)))
      : h("p", { class: "muted" }, "Nothing is queued. A draft you approve comes here with the next free posting time."));
}

function row(ctx, post) {
  const data = ctx.data();
  const zone = ctx.zoneOfPage(post.page_id);
  const buttons = [];
  const button = (words, run, kind = "") => buttons.push(h("button", { type: "button", class: `btn small ${kind}`.trim(), onclick: run }, words));
  if (post.state === "queued") {
    button("Book", () => book(ctx, post), "primary");
    button("Move", () => move(ctx, post));
    button("Edit", () => ctx.open(post.piece_id, { edit: true }));
    button("Posted by hand", () => postedByHand(ctx, post));
    button("Cancel", () => cancel(ctx, post), "danger");
  } else if (post.state === "booked") {
    button("Move", () => move(ctx, post));
    button("Edit", () => ctx.open(post.piece_id, { edit: true }));
    if (post.scheduler === "by_hand") button("Posted", () => postedByHand(ctx, post), "primary");
    button("Cancel", () => cancel(ctx, post), "danger");
  }
  for (const way of troubleButtons(post.trouble)) {
    if (["queued", "booked"].includes(post.state) && (way.call === "post.move" || way.call === "post.cancel")) continue; // already there
    button(way.label, () => resolve(ctx, post, way), buttonKind(way, !buttons.length));
  }
  const chip = troubleChip(post.trouble, toolLabelOfPost(data, post));
  const marks = [
    chip ? h("span", { class: `chip ${chip.kind}` }, chip.text) : null,
    ...askChips(post.asks).map((a) => h("span", { class: `chip ${a.kind}` }, a.text)),
  ];
  return h("li", { class: `qrow ${post.state}`, id: `post-${post.id}` },
    h("div", { class: "when" }, h("span", { class: "strong" }, formatSlot(post.slot_ms, zone, data.now_ms)), h("span", { class: "small muted" }, zone)),
    h("div", { class: "what" },
      h("span", { class: "small muted" }, `${pageName(data.pages, post.page_id)} · ${post.words}`),
      h("button", { type: "button", class: "link strong", onclick: () => ctx.open(post.piece_id) }, headline(post)),
      h("span", { class: "chips" }, marks),
      post.note ? h("span", { class: "small note" }, post.note) : null),
    h("div", { class: "row wrap" }, buttons));
}

function timeField(ctx, post, intro) {
  const data = ctx.data();
  const zone = ctx.zoneOfPage(post.page_id);
  const earliest = data.now_ms + (data.book_lead_minutes + 5) * MINUTE;
  const start = post.slot_ms && post.slot_ms > earliest ? post.slot_ms : earliest;
  const input = h("input", { type: "datetime-local", value: toInputValue(start, zone), required: true });
  return { input, zone, body: h("div", { class: "stack" }, intro ? h("p", { class: "small" }, intro) : null, field(`Time, in ${zone}`, input, `Posts books at least ${data.book_lead_minutes} minutes ahead.`)) };
}

async function book(ctx, post) {
  const { input, zone, body } = timeField(ctx, post, post.slot_ms ? "The proposed time is filled in." : "This post has no time yet.");
  const data = ctx.data();
  await dialog({
    title: "Book this post",
    intro: isPractice(data, post) ? "Practice: nothing is really booked." : `Booked through ${toolLabelOfPost(data, post)}.`,
    body,
    actions: [{ label: "Book", kind: "primary", run: async () => {
      const read = fromInputValue(input.value, zone);
      if (read.error) return read.error;
      const done = await ctx.call("post.book", { id: post.id, slot_ms: read.ms });
      say(done.state === "booked" ? `Booked for ${formatSlot(done.slot_ms, zone, ctx.data().now_ms)}.` : done.note || done.state, done.state === "booked" ? "ok" : "warn");
      await ctx.refresh();
    } }],
  });
}

async function move(ctx, post) {
  const { input, zone, body } = timeField(ctx, post, post.state === "booked" ? "The scheduler is told of the new time." : "");
  await dialog({
    title: "Move this post", body,
    actions: [{ label: "Move", kind: "primary", run: async () => {
      const read = fromInputValue(input.value, zone);
      if (read.error) return read.error;
      const done = await ctx.call("post.move", { id: post.id, slot_ms: read.ms });
      say(done.out_of_step ? done.note : `Moved to ${formatSlot(done.slot_ms, zone, ctx.data().now_ms)}.`, done.out_of_step ? "warn" : "ok");
      await ctx.refresh();
    } }],
  });
}

async function postedByHand(ctx, post) {
  if (!(await ask("Mark it as posted?", "Say this only once you have posted it yourself. It moves to Went out.", "It is posted"))) return;
  try {
    await ctx.call("post.posted", { id: post.id });
    say("Marked as posted.", "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

async function cancel(ctx, post) {
  const q = cancelQuestion(post, toolLabelOfPost(ctx.data(), post));
  // The way out says "Keep the post": "Cancel" beside "Cancel the post" reads both ways.
  const chosen = await dialog({ title: q.title, intro: q.intro, cancel: q.no, actions: [{ label: q.yes, kind: "danger" }] });
  if (chosen !== q.yes) return;
  try {
    await ctx.call("post.cancel", { id: post.id });
    say(q.done, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

/** One of the ways out the server named for a post in trouble. */
function resolve(ctx, post, way) {
  const call = way.call;
  if (call === "post.move") return move(ctx, post);
  if (call === "post.cancel") return cancel(ctx, post);
  if (call === "post.clear") return clear(ctx, post, way);
  if (call === "post.book_again") return bookAgain(ctx, post);
  if (call === "post.book") return bookRest(ctx, post, way);
  say("This page does not know that action yet. Nothing was changed.", "bad");
  return undefined;
}

async function bookAgain(ctx, post) {
  const sched = toolLabelOfPost(ctx.data(), post);
  if (!(await ask("Put it back in the queue?", `Nothing is sent to ${sched} now. It gets the next free time, and you book it again when you are ready.`, "Put it back"))) return;
  try {
    await ctx.call("post.book_again", { id: post.id });
    say("Back in the queue. Book it again when you are ready.", "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

async function clear(ctx, post, way) {
  const sched = toolLabelOfPost(ctx.data(), post);
  if (!(await ask("Is it really not there?", wayQuestion(way, sched), way.label))) return;
  try {
    const done = await ctx.call("post.clear", { id: post.id });
    say(done.state === "queued" ? "Back in the queue. Book it again when you are ready." : done.note || "Done.", "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

async function bookRest(ctx, post, way) {
  const sched = toolLabelOfPost(ctx.data(), post);
  if (!(await ask("Book the rest?", wayQuestion(way, sched), way.label))) return;
  try {
    const done = await ctx.call("post.book", { id: post.id });
    say(done.note || "Booked.", done.state === "booked" && !done.note ? "ok" : "warn");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}
