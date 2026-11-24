// An app's page imports the helpers JimmyHQ supplies beside its own files ("./_ui.js",
// "../_ui.js"), as the app file server answers them (server/jimmyhq/apps/files.py). In
// these tests the same file is found where it lives in the repository, so a view can be
// loaded without a server. Import this before importing a view, with `await import`.

import { registerHooks } from "node:module";

const SUPPLIED: Record<string, string> = {
  "_ui.js": new URL("../../sdk/js/ui.js", import.meta.url).href,
};

registerHooks({
  resolve(specifier, context, nextResolve) {
    const name = specifier.split("/").pop() ?? "";
    const found = SUPPLIED[name];
    if (found && (specifier.startsWith("./") || specifier.startsWith("../"))) return { url: found, shortCircuit: true };
    return nextResolve(specifier, context);
  },
});

export const SUPPLIED_FILES = Object.keys(SUPPLIED);
