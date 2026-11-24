// The status line with one button beside its words, such as Undo. The button goes when
// the words do; what it does can still be done from Offered and Articles.
import { afterTakenBack } from "./lib/offers.js";
import { h, say, why } from "./_ui.js";

export function sayWith(text, tone, label, act) {
  say(text, tone);
  const line = document.getElementById("status");
  if (!line || !label) return;
  line.append(" ", h("button", { type: "button", class: "link", onclick: act }, label));
}

/** Take back the offer of article `id`, say what happened, and draw again. */
export async function takeBack(ctx, id) {
  try {
    const row = await ctx.call("offer.withdraw", { id });
    say(afterTakenBack(row), "ok");
    if (ctx.state.opened?.id === id) ctx.state.opened = await ctx.call("article.get", { id });
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}
