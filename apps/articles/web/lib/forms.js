// Turning what the owner typed into the shapes the app takes, and back. The app checks
// everything again and refuses with a sentence; this only reads the fields. Pure.

/** The line written between two writing samples when they are kept as one text. */
export const SAMPLE_BREAK = "\n\n---\n\n";
const BREAK_LINE = /\n[ \t]*-{3,}[ \t]*\n/;

/** A site's kept samples as rows, one sample a row. */
export function splitSamples(text) {
  return String(text ?? "").replace(/\r\n?/g, "\n").split(BREAK_LINE).map((s) => s.trim()).filter(Boolean);
}

/** The rows as the one text a site keeps, leaving out empty rows. */
export function joinSamples(rows) {
  return (rows ?? []).map((s) => String(s ?? "").replace(/\r\n?/g, "\n").trim()).filter(Boolean).join(SAMPLE_BREAK);
}

export const SITE_FIELDS = ["name", "audience", "register", "length", "headings", "closing", "publisher", "wordpress_url", "timezone"];

/** What the form says where the address would be chosen, when the owner has given none. */
export const NO_HOSTS = "Articles has none of your own addresses yet. Add your WordPress site's address under Settings, Apps, Articles, "
  + "and type its user name and application password there, beside it. Then come back and choose it here.";

/** The button that takes the owner to Articles' own page under Settings, where addresses are given. */
export const OPEN_SETTINGS = "Open Articles settings";
export const SETTINGS_KEPT = "Settings opens over this page, so what you have typed here is kept. Add the address there, close Settings, then use Check again.";

/** The host of a site's kept address ("https://blog.example.com" gives "blog.example.com"), or "". */
export function hostOf(address) {
  return String(address ?? "").trim().toLowerCase().replace(/^[a-z]+:\/\//, "").replace(/[/?#].*$/, "");
}

/** The address a site keeps for one of the owner's hosts. */
export function addressOf(host) {
  const name = hostOf(host);
  return name ? `https://${name}` : "";
}

function granted(hosts) {
  return (hosts ?? []).map(hostOf).filter(Boolean);
}

/**
 * What the WordPress address list offers: the owner's own hosts as {value, host}, which
 * one starts chosen ("" for none), and `gone`: the host of a kept address the owner no
 * longer gives Articles. A new site with only one address to choose starts on it; a site
 * whose address has gone starts on none, so another is chosen knowingly.
 */
export function hostChoices(hosts, kept) {
  const names = granted(hosts);
  const was = hostOf(kept);
  const gone = was && !names.includes(was) ? was : "";
  const chosen = was && !gone ? addressOf(was) : !was && names.length === 1 ? addressOf(names[0]) : "";
  return { choices: names.map((host) => ({ value: addressOf(host), host })), chosen, gone };
}

/** The host a WordPress site still names though the owner no longer gives it to Articles, or "". */
export function hostGone(site, hosts) {
  if (site?.publisher !== "wordpress") return "";
  return hostChoices(hosts, site.wordpress_url).gone;
}

/** The sentence shown for a site whose address has gone. */
export function goneSaid(host) {
  return `${host} is no longer one of your own addresses, so publishing will be refused until you `
    + "add it again under Settings, Apps, Articles, or choose another address for this site.";
}

function hasId(id) {
  return id !== undefined && id !== null && id !== "";
}

/**
 * The site as `sites.save` takes it, from the form's fields. `hosts` are the owner's own
 * addresses as `sites.hosts` answers them: a site that publishes to WordPress must name one.
 * The one exception is a site being edited that already publishes to WordPress at an address
 * the owner no longer gives Articles (`kept`, its saved address): it may be saved keeping that
 * same address, with `warning` saying publishing will be refused, so an unrelated field can be
 * fixed. It is never moved to another address, and a new site or one switching to WordPress
 * (no `kept`) still needs a granted one.
 */
export function siteFromForm(fields, id, hosts = [], kept = "") {
  const site = {};
  let warning = "";
  for (const key of SITE_FIELDS) site[key] = String(fields[key] ?? "").trim();
  site.samples = joinSamples(fields.samples);
  if (!site.name) return { error: "Give the site a name. Nothing was saved." };
  if (site.publisher !== "wordpress") {
    site.wordpress_url = "";
  } else {
    const names = granted(hosts);
    const host = hostOf(site.wordpress_url);
    const keeping = Boolean(host) && host === hostOf(kept) && hasId(id) && !names.includes(host);
    if (keeping) {
      warning = goneSaid(host);
    } else {
      if (!names.length) return { error: `${NO_HOSTS} Nothing was saved.` };
      if (!host) return { error: "Choose which of your own addresses this site publishes to. Nothing was saved." };
      if (!names.includes(host)) return { error: `${goneSaid(host)} Nothing was saved.` };
    }
    site.wordpress_url = addressOf(host);
  }
  if (hasId(id)) site.id = Number(id);
  return warning ? { site, warning } : { site };
}

/**
 * The style guidance to save, or null when there is nothing to save: it was never shown
 * (`loaded` is null), or it is what was loaded.
 */
export function styleChange(loaded, typed) {
  if (loaded === null || loaded === undefined) return null;
  const tidy = (text) => String(text ?? "").replace(/\r\n?/g, "\n").trim();
  return tidy(typed) === tidy(loaded) ? null : tidy(typed);
}

/** A decline reason as the app reads it: the chosen reason first, then the owner's note. */
export function declineReason(reason, note) {
  const r = String(reason ?? "").trim();
  const n = String(note ?? "").trim();
  if (!r) return n;
  return n ? `${r}: ${n}` : r;
}

/** The new article as `new.topic` takes it. */
export function topicFromForm(form) {
  const topic = String(form.topic ?? "").trim();
  if (!topic) return { error: "Say what the article is about. Nothing was started." };
  if (!form.site) return { error: "Choose which site the article is for. Nothing was started." };
  return { args: { site_id: Number(form.site), topic, angle: String(form.angle ?? "").trim(), notes: String(form.notes ?? "").trim() } };
}

/** "Kept 3 replies; 1 was too short and 2 were repeats." */
export function pasteSaid(got) {
  const kept = got.kept ?? 0;
  const first = `Kept ${kept} ${kept === 1 ? "reply" : "replies"}`;
  const more = [];
  if (got.too_short) more.push(`${got.too_short} ${got.too_short === 1 ? "was" : "were"} too short`);
  if (got.repeats) more.push(`${got.repeats} ${got.repeats === 1 ? "was a repeat" : "were repeats"}`);
  return more.length ? `${first}; ${more.join(" and ")}.` : `${first}.`;
}

/**
 * The sites an idea's article can be started for: {choices: [{value, name}], chosen}.
 * With one site it is chosen; with several, none is chosen until the owner chooses one
 * (`remembered` is the site they chose for an idea before), so an article is never
 * started for a site only because it comes first.
 */
export function ideaSites(sites, remembered = "") {
  const choices = (sites ?? []).map((s) => ({ value: String(s.id), name: s.name }));
  const kept = String(remembered ?? "");
  const chosen = choices.length === 1 ? choices[0].value : choices.some((c) => c.value === kept) ? kept : "";
  return { choices, chosen };
}

/** An idea's start as `idea.start` takes it. */
export function ideaStart(ideaId, site) {
  if (!String(site ?? "").trim()) return { error: "Choose which site this idea's article is for. Nothing was started." };
  return { args: { id: ideaId, site_id: Number(site) } };
}
