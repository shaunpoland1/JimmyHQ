import { hq } from "./_bridge.js";
await hq.ready;
const list = document.getElementById("list");
for (const [id, body, state] of await hq.app.call("board", {})) {
  const item = document.createElement("li");
  item.textContent = `${id} (${state}): ${body}`;
  list.append(item);
}
