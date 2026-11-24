// The Articles page: reading the forms into what the app takes.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { NO_HOSTS, OPEN_SETTINGS, SETTINGS_KEPT, SAMPLE_BREAK, SITE_FIELDS, declineReason, goneSaid, hostChoices, hostGone, hostOf, ideaSites, ideaStart, joinSamples, pasteSaid, siteFromForm, splitSamples, styleChange, topicFromForm } from "../../apps/articles/web/lib/forms.js";

test("writing samples are one row each and come back as the rows they were", () => {
  const rows = ["First sample.\nWith two lines.", "  ", "Second sample."];
  const kept = joinSamples(rows);
  assert.equal(kept, `First sample.\nWith two lines.${SAMPLE_BREAK}Second sample.`);
  assert.deepEqual(splitSamples(kept), ["First sample.\nWith two lines.", "Second sample."]);
  assert.deepEqual(splitSamples("One pasted text\r\nwith no break."), ["One pasted text\nwith no break."]);
  assert.deepEqual(splitSamples(""), []);
});

test("a site from the form drops its WordPress address unless it publishes there", () => {
  const fields = { name: " Garden notes ", publisher: "practice", wordpress_url: "https://blog.example.com", samples: ["A"], timezone: "Europe/Lisbon" };
  const { site } = siteFromForm(fields, "3", ["blog.example.com"]);
  assert.equal(site.name, "Garden notes");
  assert.equal(site.wordpress_url, "");
  assert.equal(site.id, 3);
  assert.equal(site.timezone, "Europe/Lisbon");
  assert.equal(siteFromForm({ ...fields, publisher: "wordpress" }, null, ["blog.example.com"]).site.wordpress_url, "https://blog.example.com");
  assert.equal(siteFromForm({ ...fields, publisher: "wordpress" }, undefined, ["blog.example.com"]).site.id, undefined);
  assert.match(siteFromForm({ name: " " }).error, /Give the site a name\. Nothing was saved\./);
});

test("the page never sends a WordPress user name: it is typed beside the address in Settings", () => {
  assert.ok(!SITE_FIELDS.includes("wordpress_user"));
  const { site } = siteFromForm({ name: "Garden notes", publisher: "wordpress", wordpress_url: "https://blog.example.com", wordpress_user: "someone" }, null, ["blog.example.com"]);
  assert.ok(!("wordpress_user" in site));
});

test("a WordPress site is not saved without one of the owner's own addresses", () => {
  const fields = { name: "Garden notes", publisher: "wordpress", wordpress_url: "" };
  const none = siteFromForm({ ...fields, wordpress_url: "https://blog.example.com" }, null, []);
  assert.equal(none.site, undefined);
  assert.match(none.error, /under Settings, Apps, Articles/);
  assert.match(none.error, /user name and application password/);
  assert.match(none.error, /Nothing was saved\.$/);
  assert.equal(siteFromForm({ ...fields, wordpress_url: "https://blog.example.com" }).site, undefined); // no list is no address
  assert.match(siteFromForm(fields, null, ["blog.example.com"]).error, /Choose which of your own addresses.*Nothing was saved\./);
  const gone = siteFromForm({ ...fields, wordpress_url: "https://old.example.com" }, 4, ["blog.example.com"]);
  assert.match(gone.error, /old\.example\.com is no longer one of your own addresses.*Nothing was saved\./);
  // A new site, or one switching to WordPress (it has no kept address), cannot take a host that is not given.
  assert.match(siteFromForm({ ...fields, wordpress_url: "https://old.example.com" }, null, ["blog.example.com"], "https://old.example.com").error, /Nothing was saved\./);
  assert.match(siteFromForm({ ...fields, wordpress_url: "https://old.example.com" }, 4, ["blog.example.com"], "").error, /Nothing was saved\./);
  assert.equal(siteFromForm({ ...fields, wordpress_url: "HTTPS://Blog.Example.com/" }, null, ["blog.example.com"]).site.wordpress_url, "https://blog.example.com");
});

test("a site whose address has gone is saved keeping it, with a warning that publishing will be refused", () => {
  const fields = { name: "Garden notes", publisher: "wordpress", wordpress_url: "https://old.example.com", length: "Short" };
  const kept = "https://old.example.com";
  const made = siteFromForm(fields, 4, ["blog.example.com"], kept);
  assert.equal(made.error, undefined);
  assert.equal(made.site.wordpress_url, "https://old.example.com");
  assert.equal(made.site.id, 4);
  assert.equal(made.site.length, "Short");
  assert.match(made.warning, /old\.example\.com is no longer one of your own addresses, so publishing will be refused/);
  assert.match(made.warning, /Settings, Apps, Articles/);
  assert.equal(siteFromForm(fields, 4, [], kept).site.wordpress_url, kept); // none given at all: still kept, still warned
  assert.ok(siteFromForm(fields, 4, [], kept).warning);
});

test("a site whose address has gone is never moved to another address unasked", () => {
  const fields = { name: "Garden notes", publisher: "wordpress", wordpress_url: "https://old.example.com" };
  assert.equal(siteFromForm(fields, 4, ["blog.example.com"], "https://old.example.com").site.wordpress_url, "https://old.example.com");
  // The owner chose another given address: it is saved there, with no warning.
  const moved = siteFromForm({ ...fields, wordpress_url: "https://blog.example.com" }, 4, ["blog.example.com"], "https://old.example.com");
  assert.equal(moved.site.wordpress_url, "https://blog.example.com");
  assert.equal(moved.warning, undefined);
  // The address that was kept, still given: no warning either.
  assert.equal(siteFromForm({ ...fields, wordpress_url: "https://blog.example.com" }, 4, ["blog.example.com"], "https://blog.example.com").warning, undefined);
  // Another address that is not given is refused: only the kept one is tolerated.
  assert.match(siteFromForm({ ...fields, wordpress_url: "https://other.example.com" }, 4, ["blog.example.com"], "https://old.example.com").error, /Nothing was saved\./);
});

test("a site that no longer publishes to WordPress carries no warning and no address", () => {
  const made = siteFromForm({ name: "Garden notes", publisher: "by_hand", wordpress_url: "https://old.example.com" }, 4, [], "https://old.example.com");
  assert.equal(made.site.wordpress_url, "");
  assert.equal(made.warning, undefined);
});

test("the address list offers only the owner's own hosts and says when a kept one has gone", () => {
  assert.equal(hostOf("https://Blog.Example.com/"), "blog.example.com");
  assert.equal(hostOf("blog.example.com"), "blog.example.com");
  assert.equal(hostOf(""), "");
  const two = ["blog.example.com", "news.example.com"];
  assert.deepEqual(hostChoices(two, "https://news.example.com"), {
    choices: [{ value: "https://blog.example.com", host: "blog.example.com" }, { value: "https://news.example.com", host: "news.example.com" }],
    chosen: "https://news.example.com", gone: "",
  });
  assert.equal(hostChoices(two, "").chosen, ""); // two to choose from: the owner chooses
  assert.equal(hostChoices(["blog.example.com"], "").chosen, "https://blog.example.com"); // only one: it starts chosen
  const gone = hostChoices(["blog.example.com"], "https://old.example.com");
  assert.equal(gone.gone, "old.example.com");
  assert.equal(gone.chosen, ""); // never moved to another address unasked
  assert.deepEqual(hostChoices([], "https://old.example.com"), { choices: [], chosen: "", gone: "old.example.com" });
  assert.deepEqual(hostChoices(undefined, ""), { choices: [], chosen: "", gone: "" });
  assert.match(NO_HOSTS, /Settings, Apps, Articles/);
});

test("a site says its address has gone only when it publishes to WordPress", () => {
  const site = { publisher: "wordpress", wordpress_url: "https://old.example.com" };
  assert.equal(hostGone(site, ["blog.example.com"]), "old.example.com");
  assert.equal(hostGone(site, ["old.example.com"]), "");
  assert.equal(hostGone({ ...site, publisher: "by_hand" }, []), "");
  assert.equal(hostGone({ publisher: "wordpress", wordpress_url: "" }, []), "");
  assert.match(goneSaid("old.example.com"), /^old\.example\.com is no longer one of your own addresses/);
  assert.match(goneSaid("old.example.com"), /publishing will be refused.*Settings, Apps, Articles.*or choose another address/);
});

test("header image style is saved only when it was shown and then changed", () => {
  assert.equal(styleChange(null, "Soft watercolour"), null); // never loaded: left as it is
  assert.equal(styleChange("Soft watercolour", " Soft watercolour \r\n"), null);
  assert.equal(styleChange("Soft watercolour", "Line drawings"), "Line drawings");
  assert.equal(styleChange("Soft watercolour", "  "), ""); // cleared on purpose
  assert.equal(styleChange("", ""), null);
});

test("a decline reason starts with the chosen reason, then the note", () => {
  assert.equal(declineReason("Voice off", " too formal "), "Voice off: too formal");
  assert.equal(declineReason("Voice off", ""), "Voice off");
  assert.equal(declineReason("", "just a note"), "just a note");
  assert.equal(declineReason("", ""), "");
});

test("a new topic needs its words and its site", () => {
  assert.deepEqual(topicFromForm({ site: "2", topic: " Soil ", angle: "", notes: " n " }).args, { site_id: 2, topic: "Soil", angle: "", notes: "n" });
  assert.match(topicFromForm({ site: "2", topic: "" }).error, /Say what the article is about/);
  assert.match(topicFromForm({ site: "", topic: "Soil" }).error, /Choose which site/);
});

test("what a paste kept is said in counts only", () => {
  assert.equal(pasteSaid({ kept: 1, too_short: 0, repeats: 0 }), "Kept 1 reply.");
  assert.equal(pasteSaid({ kept: 3, too_short: 1, repeats: 2 }), "Kept 3 replies; 1 was too short and 2 were repeats.");
  assert.equal(pasteSaid({ kept: 0, too_short: 2, repeats: 1 }), "Kept 0 replies; 2 were too short and 1 was a repeat.");
});

test("an idea's article is started for a site the owner chose, never silently the first", () => {
  const two = [{ id: 4, name: "Garden notes" }, { id: 9, name: "Plot news" }];
  assert.deepEqual(ideaSites(two), { choices: [{ value: "4", name: "Garden notes" }, { value: "9", name: "Plot news" }], chosen: "" });
  assert.equal(ideaSites(two, "9").chosen, "9"); // the site chosen for an idea before
  assert.equal(ideaSites(two, "77").chosen, ""); // a site that has gone is not a choice
  assert.equal(ideaSites([two[0]]).chosen, "4"); // one site is no choice to make
  assert.deepEqual(ideaSites([]), { choices: [], chosen: "" });
  assert.match(ideaStart(3, "").error, /Choose which site .* Nothing was started\./);
  assert.deepEqual(ideaStart(3, "9"), { args: { id: 3, site_id: 9 } });
});

test("the Sites form offers the button to Articles' own settings when no address is available or a saved one is gone, and says what typed is kept", () => {
  assert.equal(OPEN_SETTINGS, "Open Articles settings");
  assert.match(SETTINGS_KEPT, /what you have typed here is kept/);
  const source = readFileSync(new URL("../../apps/articles/web/views/sites.js", import.meta.url), "utf8");
  // The one call the page makes to get there, and no address of its own.
  assert.match(source, /ctx\.openSettings\(\)/);
  assert.doesNotMatch(source, /#\/settings/);
  // Shown with no choices, and with a gone address (even when others exist).
  assert.match(source, /offered\.choices\.length && !offered\.gone \? null : settingsWay\(\)/);
  const app = readFileSync(new URL("../../apps/articles/web/app.js", import.meta.url), "utf8");
  assert.match(app, /openSettings: \(\) => hq\.ui\.settings\(\)/);
});

test("Articles says in its manifest what to do after adding an address, and the shell carries no words about it", () => {
  const manifest = JSON.parse(readFileSync(new URL("../../apps/articles/jimmyhq.app.json", import.meta.url), "utf8"));
  assert.equal(manifest.owner_hosts.next, "Then open Articles, go to Sites, and choose it under Where it publishes.");
  const shell = readFileSync(new URL("../src/modules/apps/AppHosts.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(shell, /WordPress|Articles/);
});
