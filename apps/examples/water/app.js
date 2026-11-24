import { hq } from "./_bridge.js";
await hq.ready;
const day = new Date().toISOString().slice(0, 10);
const out = document.getElementById("out");
async function show() {
  const n = (await hq.storage.get(day)) ?? 0;
  out.textContent = `${n} of ${await hq.settings.get("goal")}`;
  return n;
}
document.getElementById("add").onclick = async () => {
  const n = await show();
  await hq.storage.set(day, n + 1);
  if (n === 0) await hq.tasks.create({ title: "Keep drinking water" });
  show();
};
show();
