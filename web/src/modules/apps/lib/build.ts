// "Build your own": the three ways to make an app and the first step for each, in the
// words the Apps screen shows. Pure; the dialog only draws this. It says what this
// version of JimmyHQ really does: described and sandboxed apps install and run, a full
// app runs as the owner, started by JimmyHQ.

import type { TierKey } from "./status.ts";

/** The file every app has at the top of its folder. */
export const MANIFEST_FILE = "jimmyhq.app.json";

// A complete described app, small enough to copy. A server test reads it from between
// these markers and loads it with the real manifest reader, so it cannot go stale.
// --- described example ---
export const DESCRIBED_EXAMPLE = `{
  "manifest": 1,
  "id": "reading",
  "name": "Reading",
  "summary": "Books I want to read.",
  "version": "1.0.0",
  "tier": "sandboxed",
  "api": "1.0",
  "licence": "MIT",
  "permissions": [],
  "screens": [{"id": "main", "label": "Reading", "entry": "described"}],
  "tables": {
    "book": {
      "label": "Books",
      "fields": {
        "title": {"type": "text", "label": "Title", "required": true},
        "finished": {"type": "date", "label": "Finished on"},
        "stars": {"type": "int", "label": "Stars", "min": 1, "max": 5}
      }
    }
  },
  "views": [
    {"id": "all", "label": "All books", "table": "book", "sort": ["-finished", "title"]}
  ]
}
`;
// --- end described example ---

export type BuildKind = {
  key: TierKey;
  heading: string;
  /** What this kind is, in a sentence or two. */
  what: string;
  /** The first steps, in order. */
  steps: string[];
  /** Said plainly when this kind cannot do everything yet. */
  caveat: string | null;
};

export const BUILD_INTRO =
  "Every app is a folder with one description file in it, " +
  `${MANIFEST_FILE}. ` +
  "The file says what the app is called, what it may do and what it adds to JimmyHQ. There are three ways to make one, from the simplest up.";

export const BUILD_KINDS: readonly BuildKind[] = [
  {
    key: "described",
    heading: "Records and views: just the description file",
    what: "You describe tables of records and how to show them, and JimmyHQ draws the screen for you. There is no code to write.",
    steps: [
      `Make a new folder, for example one called reading, and save the description below in it as ${MANIFEST_FILE}.`,
      "On this screen, choose Install an app, then A folder on this computer, and give the full path of that folder.",
      "Read what the install window shows, then install it. Reading appears in the sidebar as a table you can add books to.",
    ],
    caveat: null,
  },
  {
    key: "sandboxed",
    heading: "Sandboxed: your own web page",
    what: "You write a page (HTML and JavaScript) and JimmyHQ shows it in a locked box. The page can only do what its description lists, and it reaches JimmyHQ only through a small bridge that JimmyHQ supplies beside your files.",
    steps: [
      'Start from a copy of the Water example, in apps/examples/water in JimmyHQ\'s source. It is a description file, an index.html and an app.js.',
      'In the description, give it your own id and name, point the screen\'s "entry" at your page, and list in "permissions" only what it needs, such as "tasks.write".',
      'In your script, import { hq } from "./_bridge.js" to keep data, read settings or add tasks. Then install the folder the same way.',
    ],
    caveat: null,
  },
  {
    key: "full",
    heading: "Full: a program that runs as you",
    what: 'A Python program with "tier": "full" and a "server" entry in its description. It could do anything you can do on this computer, which is why the install window warns about it.',
    steps: [
      "Run jimmyhq apps new my_app to write a working skeleton: a job, a card type and a screen.",
      "Run jimmyhq apps dev my_app to install it here and put each change you save in place.",
      "Run jimmyhq apps check my_app before you give it to anyone. The Watering example, in apps/examples/watering in JimmyHQ's source, shows a whole one.",
    ],
    caveat:
      "A full app runs as you, so whoever installs it trusts whoever wrote it. Ask for only the permissions it needs, and keep its locked dependencies small.",
  },
];

export const GUIDE_LINK_TEXT = "Read the full guide to apps";
export const GUIDE_LINK_NOTE = "Opens the JimmyHQ docs on GitHub in a new tab, outside JimmyHQ.";

/** The full guide in the docs, from the project's address. */
export function guideUrl(projectUrl: string): string {
  return `${projectUrl.replace(/\/+$/, "")}/blob/main/docs/apps.md`;
}
