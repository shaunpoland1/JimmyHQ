// Words and small rules for Imagery's page. Pure: no DOM, no bridge, so each is tested.

/** A stored time (unix ms, UTC) as the owner reads it, in their own time zone. */
export function whenText(ms, zone) {
  if (!Number.isFinite(ms)) return "";
  const options = { dateStyle: "medium", timeStyle: "short" };
  if (zone) options.timeZone = zone;
  try {
    return new Intl.DateTimeFormat("en-GB", options).format(new Date(ms));
  } catch {
    return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" }).format(new Date(ms));
  }
}

/** Bytes as the owner reads them: 840 KB, 3.4 MB. */
export function sizeText(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** The line that says how much of today's limit is used. */
export function todayLine(today, outside) {
  if (!outside) return "This image service sends nothing anywhere, so it uses none of today's limit.";
  const left = Math.max(0, today.limit - today.used);
  return `Today: ${today.used} of ${today.limit} pictures asked of outside services. ${left} left.`;
}

/** The line that says how full the gallery is. */
export function galleryLine(g) {
  return `${g.pictures} picture${g.pictures === 1 ? "" : "s"}, ${sizeText(g.bytes)} of ${sizeText(g.max_bytes)}. Most to keep: ${g.max_pictures}.`;
}

/** One run as a sentence: where it is, or how it ended. */
export function runLine(run) {
  const many = `${run.want} picture${run.want === 1 ? "" : "s"}`;
  if (run.state === "queued") return `Waiting to make ${many}.`;
  if (run.state === "working") return `Making ${many}: ${run.made} done.`;
  return run.said || (run.state === "done" ? `Made ${many}.` : "Stopped.");
}

export function runActive(run) {
  return run.state === "queued" || run.state === "working";
}

/** Whether any run is waiting or being made, so the page keeps asking how it is going. */
export function anyActive(runs) {
  return (runs ?? []).some(runActive);
}

/** What the make form starts with when the owner presses "More like this". */
export function likeThis(row, data) {
  const style = (data.presets ?? []).some((p) => p.name === row.style) ? row.style : "";
  const aspect = (data.provider?.aspects ?? []).includes(row.aspect) ? row.aspect : (data.provider?.aspects?.[0] ?? "");
  return { prompt: row.prompt, style, aspect, count: 1, reference: null };
}

/** The text of "Save details as a file": a Markdown note about one picture. */
export function detailsText(row, zone) {
  const lines = [`# Picture ${row.id}`, "", row.prompt || "(no description)", ""];
  if (row.kind === "given") lines.push("This is a picture you gave Imagery.", "");
  else {
    lines.push(`- Made: ${whenText(row.created, zone)}`);
    lines.push(`- Image service: ${row.provider}`);
    lines.push(`- Model: ${row.model}`);
    lines.push(`- Shape: ${row.aspect}`);
    if (row.style) lines.push(`- Style: ${row.style}`);
  }
  if (row.width && row.height) lines.push(`- Size: ${row.width} by ${row.height} pixels`);
  return `${lines.join("\n")}\n`;
}

/** A name for the saved file: the start of the description. The platform makes it safe and adds the ending. */
export function detailsName(row) {
  const stem = String(row.prompt || "picture").replace(/\s+/g, " ").trim().slice(0, 40);
  return `picture-${row.id}-${stem}`;
}

/** The search words as the program takes them: tidy, at most eight. */
export function cleanSearch(text) {
  return String(text ?? "").split(/\s+/).filter(Boolean).slice(0, 8).join(" ");
}

/** The numbers for "How many": 1 up to the most the owner allows. */
export function countChoices(most) {
  return Array.from({ length: Math.max(1, Math.min(4, Number(most) || 1)) }, (_, i) => i + 1);
}

/** Why the Make button cannot be pressed yet, or "" when it can. */
export function makeProblem(form, data) {
  if (data.provider?.problem) return data.provider.problem;
  if (!String(form.prompt ?? "").trim()) return "Say what the picture should show.";
  if (!form.aspect) return "Choose a shape.";
  return "";
}
