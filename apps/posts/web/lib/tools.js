// Which tool books each page: a page may choose its own, and otherwise follows Posts'
// setting. The app says, for every page, which tool books it (`books_through`: {id,
// label, sends_out, known, own, lost}) and, for every post, the one it is on (`tool`).
// This turns that into the words and the choices the screen shows. Pure.

const HOW = "Change it on the brand under Brands, or for every brand in Settings, then Apps, then Posts, “Book through (the default)”.";

/** The names as a sentence: "A", "A and B", "A, B and C". */
export function nameList(names) {
  const list = (names ?? []).filter(Boolean);
  if (list.length <= 1) return list[0] ?? "";
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/** The tool that books one page: its own `books_through`, else the setting (`data.scheduler`). */
export function toolOfPage(data, pageId) {
  const page = (data?.pages ?? []).find((p) => p.id === pageId);
  return page?.books_through ?? data?.scheduler ?? null;
}

/** A tool's label for a sentence about one post: the one it was booked with, else its page's. */
export function toolLabelOfPost(data, post) {
  return post?.tool?.label || toolOfPage(data, post?.page_id)?.label || "the scheduler";
}

/** Whether the tool that books this post's page is Practice (nothing is really booked). */
export function isPractice(data, post) {
  const id = post?.tool?.id || toolOfPage(data, post?.page_id)?.id;
  return id === "practice";
}

/**
 * The notices at the top of the queue, each {kind, strong, text}: "practice" (loud), "plain"
 * or "bad". With every page on one tool the words are about that tool; when only some pages
 * are on Practice (or By hand, or a tool Posts does not know) the notice names those pages
 * and says nothing about the rest. A tool that sends out needs no notice.
 */
export function notices(data) {
  const setting = data?.scheduler;
  if (!setting) return [];
  const pages = data?.pages ?? [];
  const entries = pages.length
    ? pages.map((p) => ({ name: p.name, tool: p.books_through ?? setting }))
    : [{ name: "", tool: setting }];
  const groups = new Map();
  for (const entry of entries) {
    const key = entry.tool.known === false ? `unknown:${entry.tool.id}` : entry.tool.id;
    if (!groups.has(key)) groups.set(key, { tool: entry.tool, names: [] });
    groups.get(key).names.push(entry.name);
  }
  const out = [];
  for (const { tool, names } of groups.values()) {
    const all = groups.size === 1 || !names[0];
    const who = nameList(names);
    if (tool.known === false) {
      out.push({ kind: "bad", strong: all ? "Nothing can be booked. " : `Nothing can be booked for ${who}. `,
        text: `Posts does not know the scheduler “${tool.label}”. ${HOW}` });
    } else if (tool.id === "practice") {
      out.push({ kind: "practice", strong: all ? "Practice: nothing is really being booked. " : `Practice: nothing is really being booked for ${who}. `,
        text: `Posts records what it would send${all ? "" : ` for ${names.length === 1 ? "it" : "them"}`}, and nothing leaves this computer. ${HOW}` });
    } else if (tool.id === "by_hand") {
      out.push({ kind: "plain", strong: all ? "By hand. " : `By hand for ${who}. `,
        text: `Post each one yourself at its time, then press Posted. ${HOW}` });
    }
  }
  return out;
}

/**
 * The notices for schedulers that asked Posts to wait (a rate limit): {kind, strong, text}
 * for each, saying until when nothing is asked of them. `when(ms)` writes the time.
 */
export function waitNotices(data, when = (ms) => new Date(ms).toISOString()) {
  return (data?.waits ?? []).filter((w) => w.until_ms > (data?.now_ms ?? 0)).map((w) => ({
    kind: "bad", strong: `${w.label} asked Posts to wait. `,
    text: `It allows only so many requests, so Posts asks it nothing, and books nothing through it, until ${when(w.until_ms)}. Checks start again by themselves after that.`,
  }));
}

/**
 * The choices of a page's "Book through" select (the setting itself is "Book through (the default)"): the setting first, then each tool Posts
 * offers ({id, label}), and a stored choice that is no longer offered, so editing a page
 * never loses it silently. Each is {value, label}; "" means Posts' setting.
 */
export function bookThroughChoices(offered, setting, stored = "") {
  const out = [{ value: "", label: `Posts' setting (currently ${setting?.label ?? "not known"})` }];
  for (const s of offered ?? []) out.push({ value: s.id, label: s.label });
  if (stored && !out.some((c) => c.value === stored)) out.push({ value: stored, label: `${stored} (no longer offered)` });
  return out;
}

/** One line on a page's card: which tool books it, and why. */
export function bookedThroughWords(page, setting) {
  const tool = page?.books_through;
  if (!tool) return "";
  if (tool.own) return `books through ${tool.label}`;
  const lost = tool.lost ? `, since the tool it chose is no longer offered` : "";
  return `books through ${tool.label} (Posts' setting${lost})`;
}
