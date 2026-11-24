// Today: what needs the owner, in the order it is best dealt with. Pure.

import { checksLine, headline, siteName, toDecide } from "./lists.js";

export const NOTHING = "Nothing needs you right now.";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Whether a site sends anything off this computer when it publishes. */
function sendsOut(data, siteId) {
  return Boolean((data?.sites ?? []).find((s) => s.id === siteId)?.sends_out);
}

/**
 * The groups of Today, each {key, title, items: [{text, detail, go: {view, piece?}}]},
 * leaving out a group with nothing in it. An empty list means nothing needs the owner.
 */
export function needs(data) {
  const pieces = data?.pieces ?? [];
  const sites = data?.sites ?? [];
  const groups = [];
  const add = (key, title, items) => { if (items.length) groups.push({ key, title, items }); };
  const line = (p, detail, view = "articles") => ({ text: `${siteName(sites, p.site_id)}: ${headline(p)}`, detail, go: { view, piece: p.id } });

  if (!sites.length) {
    add("sites", "Set up a site", [{ text: "Add a site so Articles knows who it writes for and where it publishes.", detail: "", go: { view: "sites" } }]);
  }
  const deciding = toDecide(pieces);
  const open = deciding.reduce((n, p) => n + (p.questions || 0), 0);
  add("decide", `${plural(deciding.length, "article")} to decide${open ? `, ${plural(open, "question")} open` : ""}`,
    deciding.map((p) => line(p, checksLine(p))));

  add("unsure", "Publishing with no answer", pieces.filter((p) => p.state === "unsure").map((p) => line(p,
    "The site gave no answer, so it may be there already. Look on the site, then say what you found.", "publish")));

  add("close", "Held: close to an earlier article", pieces.filter((p) => p.state === "held_coverage").map((p) => line(p,
    `${p.close?.said ?? "Close to an earlier article."} Nothing has been spent on it. Write anyway, or drop it.`)));
  add("sources", "Held: too few good sources", pieces.filter((p) => p.state === "stopped_research").map((p) => line(p,
    p.error || "Too few readable, different sources were found. Retry, or drop it.")));
  add("stuck", "Stuck", pieces.filter((p) => p.state === "stalled").map((p) => line(p, p.error || "A step did not finish. Retry, or drop it.")));

  add("approved", "Approved, waiting for Publish", pieces.filter((p) => p.state === "approved").map((p) => line(p,
    p.error || (sendsOut(data, p.site_id) ? "Press Publish to send it." : "Press Publish."), "publish")));
  add("sent", "Sent, waiting for you to say it is live", pieces.filter((p) => p.state === "sent").map((p) => line(p,
    p.attempt?.detail || "Press Published once it is on the site.", "publish")));

  const ideas = data?.ideas ?? [];
  const unread = data?.replies?.unread ?? 0;
  const said = [];
  if (ideas.length) said.push({ text: `${plural(ideas.length, "idea")} from readers waiting`, detail: "Start one, or dismiss it.", go: { view: "ideas" } });
  if (unread) said.push({ text: `${plural(unread, "reply", "replies")} not looked at yet`, detail: "Press Find ideas when you are ready.", go: { view: "ideas" } });
  add("ideas", "Ideas from readers", said);
  return groups;
}

/** One quiet line on what is under way and needs nothing: "2 articles being worked on." */
export function underWay(data) {
  const working = (data?.pieces ?? []).filter((p) => !["ready", "held_coverage", "stopped_research", "stalled", "approved",
    "unsure", "sent", "published", "declined", "dropped"].includes(p.state));
  return working.length ? `${plural(working.length, "article")} being worked on.` : "";
}

/** How many things Today lists: the count on the app's sidebar line. */
export function count(data) {
  return needs(data).filter((g) => g.key !== "sites").reduce((n, g) => n + g.items.length, 0);
}
