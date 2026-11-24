// Pictures from the app's own storage, drawn by JimmyHQ (`hq.files.thumbnail`) and fetched
// only when they scroll into view. JimmyHQ never gives a page more pixels than the owner's
// "Longest side of a small picture" setting under Settings, Apps, advanced.
import { hq } from "../_bridge.js";

const made = new Map();

export function thumbUrl(ref, size) {
  const key = `${ref}@${size ?? ""}`;
  if (!made.has(key)) {
    made.set(key, hq.files.thumbnail(ref, size).then((blob) => URL.createObjectURL(blob)).catch(() => null));
  }
  return made.get(key);
}

let watcher = null;

/** An <img> that loads its picture when it is near the screen. */
export function lazyImage(h, ref, alt, size) {
  const img = h("img", { alt: alt || "" });
  watcher ??= new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      watcher.unobserve(entry.target);
      const job = entry.target.__job;
      job().then((url) => { if (url) entry.target.src = url; });
    }
  }, { rootMargin: "300px" });
  img.__job = () => thumbUrl(ref, size);
  watcher.observe(img);
  return img;
}
