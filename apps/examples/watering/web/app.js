import { hq } from "./_bridge.js";

await hq.ready;
const state = document.getElementById("state");
const list = document.getElementById("rounds");
const words = { watered: "Watered", skipped: "Not this time" };

async function show() {
  try {
    const rounds = await hq.app.call("rounds", {});
    state.textContent = rounds.length ? "" : "No rounds yet. The first one is on your Bench soon.";
    list.replaceChildren(...rounds.map((round) => {
      const item = document.createElement("li");
      const when = round.answered ? new Date(round.answered).toLocaleString() : "";
      item.textContent = round.answer ? `${words[round.answer] ?? round.answer}, ${when}` : "Waiting for your answer";
      return item;
    }));
  } catch (error) {
    state.textContent = error.message;
  }
}

hq.on("rounds.changed", show);
show();
