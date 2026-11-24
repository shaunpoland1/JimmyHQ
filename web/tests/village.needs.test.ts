import assert from "node:assert/strict";
import { test } from "node:test";
import { computeMood, describeMood, moodTone, mostStrained, needSentence, readNeed, readNeeds, satisfaction } from "../src/modules/village/lib/needs.ts";
import { describeNight, describeTemperament } from "../src/modules/village/lib/temperament.ts";

test("energy, company and purpose are good when high", () => {
  assert.equal(satisfaction("energy", 90), 0.9);
  assert.equal(readNeed("energy", 90).tone, "good");
  assert.equal(readNeed("energy", 10).tone, "strained");
  assert.equal(readNeed("purpose", 80).word, "driven");
  assert.equal(readNeed("social", 10).word, "lonely");
});

test("for frustration and curiosity a high number is the bad end", () => {
  assert.ok(Math.abs(satisfaction("frustration", 90) - 0.1) < 1e-9);
  assert.equal(readNeed("frustration", 90).tone, "strained");
  assert.equal(readNeed("frustration", 90).word, "very frustrated");
  assert.equal(readNeed("frustration", 5).tone, "good");
  assert.equal(readNeed("frustration", 5).word, "calm");
  assert.equal(readNeed("curiosity", 85).tone, "strained");
  assert.equal(readNeed("curiosity", 85).highIsBad, true);
  assert.equal(readNeed("energy", 85).highIsBad, false);
});

test("the bar always shows the honest number, whatever the polarity", () => {
  assert.equal(readNeed("frustration", 72.4).value, 72);
  assert.equal(readNeed("energy", 140).value, 100);
  assert.equal(readNeed("energy", -3).value, 0);
  assert.equal(readNeed("energy", Number.NaN).value, 0);
});

test("a need the server did not send is left out, not guessed", () => {
  assert.deepEqual(readNeeds(undefined), []);
  assert.deepEqual(readNeeds({ purpose: 50, energy: 80 }).map((reading) => reading.need), ["energy", "purpose"]);
});

test("a screen reader hears that lower is better for an urge", () => {
  assert.equal(needSentence(readNeed("energy", 72)), "Energy 72 of 100, steady.");
  assert.equal(needSentence(readNeed("frustration", 60)), "Frustration 60 of 100, frustrated. Lower is better.");
});

test("the need to watch is the least satisfied one, and only when it is strained", () => {
  assert.equal(mostStrained(readNeeds({ energy: 80, frustration: 10, social: 55, curiosity: 40, purpose: 50 })), null);
  assert.equal(mostStrained(readNeeds({ energy: 30, frustration: 88, social: 55, curiosity: 40, purpose: 50 }))?.need, "frustration");
});

test("mood is computed from the needs the way the server does", () => {
  const weights = { energy: 0.2, frustration: 0.2, social: 0.2, curiosity: 0.2, purpose: 0.2 };
  const mood = computeMood({ energy: 80, frustration: 10, social: 55, curiosity: 40, purpose: 50 }, weights);
  assert.ok(mood);
  // Satisfactions 0.8, 0.9, 0.55, 0.6 and 0.5 average to 0.67, so valence is 0.34.
  assert.ok(Math.abs(mood.valence - 0.34) < 1e-9);
  // 0.5 * 0.6 + 0.3 * -0.8 + 0.2 * -0.2 = 0.02
  assert.ok(Math.abs(mood.arousal - 0.02) < 1e-9);
  assert.equal(computeMood({ energy: 80 }, weights), null);
});

test("mood is said as one plain phrase", () => {
  assert.equal(describeMood({ valence: 0.6, arousal: 0.5 }), "In good spirits and full of go");
  assert.equal(describeMood({ valence: 0.6, arousal: 0 }), "Content");
  assert.equal(describeMood({ valence: 0, arousal: 0 }), "On an even keel");
  assert.equal(describeMood({ valence: -0.5, arousal: 0.6 }), "Tense and on edge");
  assert.equal(describeMood({ valence: -0.5, arousal: -0.6 }), "Worn down");
  assert.equal(describeMood(null), null);
  assert.equal(moodTone({ valence: -0.5, arousal: 0 }), "strained");
});

test("temperament is described in words, one sentence per trait that is known", () => {
  const lines = describeTemperament({
    energy_drain_rate: 1.35,
    boredom_rate: 0.65,
    social_capacity: 1.0,
    frustration_tolerance: 0.55,
    risk_appetite: 0.9,
    mood_weights: { energy: 0.1, frustration: 0.1, social: 0.1, curiosity: 0.1, purpose: 0.6 },
    seed_curiosities: ["old maps", "birds", "tea"],
    sleep_offset_minutes: -40,
  });
  assert.deepEqual(lines, [
    "Tires quickly.",
    "Patient with routine.",
    "Likes some company.",
    "Frustrated easily, so it steps away sooner.",
    "Bold.",
    "Its mood leans most on purpose.",
    "An early riser: its day runs 40 minutes ahead.",
    "Started out curious about old maps, birds and tea.",
  ]);
  assert.deepEqual(describeTemperament(undefined), []);
  assert.deepEqual(describeTemperament({ risk_appetite: 0.1 }), ["Cautious."]);
});

test("last night says what was done, by which model, and how much was rejected", () => {
  assert.deepEqual(describeNight({ ts: 1, day: "2026-01-05", by: "smart", ops: 12, rejected: 1 }), {
    summary: "It made 12 changes to its memory.",
    by: "Sorted by its night model.",
    rejected: "1 proposed change was rejected by the checks and left out.",
  });
  const fallback = describeNight({ ts: 1, day: "2026-01-05", by: "fallback", ops: 0 });
  assert.equal(fallback?.summary, "It made no changes to its memory.");
  assert.match(fallback?.by ?? "", /everyday model, because the night model kept failing/);
  assert.equal(fallback?.rejected, null);
  assert.equal(describeNight(null), null);
});
