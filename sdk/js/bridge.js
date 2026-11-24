// The JimmyHQ bridge for an app's page. JimmyHQ serves this file beside your
// own as `./_bridge.js`, so it always matches the JimmyHQ it runs in:
//
//   import { hq } from "./_bridge.js";
//   const { app, version, theme, timezone, week_start, permissions } = await hq.ready;
//   await hq.storage.set("count", 3);
//   const count = await hq.storage.get("count");
//   hq.on("theme", ({ theme }) => { ... });
//
// Your page runs in a sandboxed frame: it has no network, no cookies and no
// reach into JimmyHQ. Everything goes through the calls below. Each returns a
// promise. A refusal rejects with an Error that has `code` (not_allowed, quota,
// invalid, not_found, too_large, unavailable, internal), `message` (a sentence
// that says what to do instead), for a quota `retry_after_s`, and sometimes
// `owner_message`: the same said to the owner, ready to show (`why()` in `_ui.js` uses it).
//
// `timezone` (an IANA name) and `week_start` ("monday", "sunday" or "saturday") are the
// only two things your app is told about the person using it.
//
// An app is one page. If the page loads a second time (a link to another page, a form
// that submits, location.reload()), JimmyHQ closes the bridge and takes the frame down.
// Change what is shown with script, and keep what must last in hq.storage or hq.records.
// Import this file from a script in the page itself, not later with import().
//
// Pictures: in an <input type="file"> change handler, `const {ref, name, size, type} =
// await hq.files.upload(file)` keeps one in your app's storage; `hq.files.thumbnail(ref)`
// gives it back small as a Blob, `hq.files.picture(ref)` large. `hq.ui.save` (a file to
// keep), `hq.ui.limits` (the owner's numbers, to size your work to) and `hq.speech.dictate`:
// see docs/apps.md.

/** Every call, by group. `hq.tasks.create(args)` sends the call "tasks.create". */
const CALLS = {
  storage: ["get", "set", "delete", "keys"],
  records: ["list", "get", "create", "update", "delete"],
  tasks: ["list", "create", "complete"],
  bench: ["file"],
  settings: ["get"],
  model: ["ask"],
  memory: ["recall"],
  net: ["fetch"],
  app: ["call"],
  ui: ["toast", "badge", "settings", "save", "touch", "limits"],
  files: ["upload", "thumbnail", "picture", "picking"],
  speech: ["dictate", "available"],
};

/** Calls with a short form: `hq.storage.set("k", 1)` means `hq.storage.set({key: "k", value: 1})`. */
const SHORT = {
  "storage.get": ["key"],
  "storage.set": ["key", "value"],
  "storage.delete": ["key"],
  "settings.get": ["key"],
  "app.call": ["name", "args"],
  "ui.toast": ["message"],
  "ui.badge": ["count"],
  "files.upload": ["file"],
  "files.thumbnail": ["ref", "size"],
  "files.picture": ["ref", "size"],
};

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isPlain(value) {
  if (!isObject(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function toArgs(call, given) {
  const names = SHORT[call];
  if (given.length === 0) return {};
  // A File is an object too, but not the arguments themselves: hq.files.upload(file).
  if (!names || (given.length === 1 && isPlain(given[0]))) return given[0];
  const args = {};
  // An argument left out, or passed on as undefined, is not sent: undefined is not JSON.
  names.forEach((name, i) => {
    if (i < given.length && given[i] !== undefined) args[name] = given[i];
  });
  return args;
}

function refusal(error) {
  const failure = new Error((error && error.message) || "The call failed, so nothing was done. Try again.");
  failure.code = (error && error.code) || "internal";
  if (error && typeof error.retry_after_s === "number") failure.retry_after_s = error.retry_after_s;
  if (error && typeof error.owner_message === "string" && error.owner_message) failure.owner_message = error.owner_message;
  return failure;
}

/** Builds the bridge for one window. `hq` below is this for the page's own window. */
export function connect(win) {
  let port = null;
  let nextId = 1;
  const unsent = [];
  const waiting = new Map();
  const listeners = new Map();
  let resolveReady;
  const ready = new Promise((resolve) => {
    resolveReady = resolve;
  });

  function emit(event, data) {
    for (const fn of listeners.get(event) ?? []) {
      try {
        fn(data);
      } catch (error) {
        console.error(error);
      }
    }
  }

  function setTheme(data) {
    if (win.document && data && (data.theme === "light" || data.theme === "dark")) {
      win.document.documentElement.dataset.theme = data.theme;
    }
  }

  function receive(message) {
    if (!isObject(message)) return;
    if (typeof message.event === "string") {
      if (message.event === "ready") resolveReady(message.data);
      if (message.event === "ready" || message.event === "theme") setTheme(message.data);
      emit(message.event, message.data);
      return;
    }
    const pending = waiting.get(message.id);
    if (!pending) return;
    waiting.delete(message.id);
    if (message.ok === true) pending.resolve(message.result);
    else pending.reject(refusal(message.error));
  }

  function request(call, args) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      waiting.set(id, { resolve, reject });
      const message = { id, call, args };
      try {
        if (port) port.postMessage(message);
        else unsent.push(message);
      } catch {
        // The data could not be copied (a function, a DOM node, ...).
        waiting.delete(id);
        reject(refusal({ code: "invalid", message: "Your data could not be sent, so nothing was done. Send only plain JSON values." }));
      }
    });
  }

  // JimmyHQ hands over one private channel. Only the page that framed us may do that.
  win.addEventListener("message", (event) => {
    const data = event.data;
    if (port || event.source !== win.parent || !isObject(data) || data.jimmyhq !== 1 || !event.ports[0]) return;
    port = event.ports[0];
    port.onmessage = (reply) => receive(reply.data);
    for (const message of unsent.splice(0)) {
      try {
        port.postMessage(message);
      } catch {
        const pending = waiting.get(message.id);
        waiting.delete(message.id);
        pending?.reject(refusal({ code: "invalid", message: "Your data could not be sent, so nothing was done. Send only plain JSON values." }));
      }
    }
  });
  // Each click or key press of the owner's: tell JimmyHQ, so it looks at the browser's activation
  // at that moment. It is a nudge and never a claim: JimmyHQ cannot tell a click from a page
  // that says so, so it does not count as a new touch (web/src/modules/apps/lib/touch.ts).
  // Opening a file picker is told too.
  let touched = 0;
  const touch = (event) => {
    const now = Date.now();
    if (!event.isTrusted || event.repeat || now - touched < 250) return;
    touched = now;
    request("ui.touch", {}).catch(() => {});
  };
  if (win.document && typeof win.document.addEventListener === "function") {
    win.document.addEventListener("click", touch, true);
    win.document.addEventListener("keydown", touch, true);
    win.document.addEventListener(
      "click",
      (event) => {
        const target = event.target;
        if (target && target.tagName === "INPUT" && String(target.type).toLowerCase() === "file") {
          request("files.picking", {}).catch(() => {});
        }
      },
      true,
    );
  }
  // Say we are listening, so the channel can arrive before the whole page has loaded.
  win.parent.postMessage({ jimmyhq: 1, hello: true }, "*");

  const hq = {
    ready,
    /** Listens for an event: theme, settings.changed, records.changed, bench.decided, visible, reload, or one a full app's own program sent with app.emit. Returns the function that stops listening. */
    on(event, fn) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(fn);
      return () => listeners.get(event).delete(fn);
    },
  };
  for (const [group, names] of Object.entries(CALLS)) {
    hq[group] = {};
    for (const name of names) {
      const call = `${group}.${name}`;
      hq[group][name] = (...given) => request(call, toArgs(call, given));
    }
  }
  // hq.ui.save({name, blob}) sends a Blob's bytes (a picture from canvas.toBlob), typed as the Blob is.
  const save = hq.ui.save;
  hq.ui.save = async (...given) => {
    const args = toArgs("ui.save", given);
    if (!(isPlain(args) && typeof Blob !== "undefined" && args.blob instanceof Blob)) return save(args);
    const { blob, ...rest } = args;
    return save({ type: blob.type, ...rest, bytes: await blob.arrayBuffer() });
  };
  return hq;
}

export const hq = typeof window === "undefined" ? undefined : connect(window);
