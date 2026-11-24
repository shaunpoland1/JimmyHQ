import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { ApiError, request, setUnauthorizedHandler } from "../src/api.ts";

// A stand-in server: answers each path from a list, in order, and notes what was asked.
type Answer = { status: number; body: unknown };
const realFetch = globalThis.fetch;

function serve(answers: Record<string, Answer[]>): string[] {
  const asked: string[] = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    asked.push(`${init?.method ?? "GET"} ${path}`);
    const next = answers[path]?.shift();
    if (!next) throw new Error(`nothing left to answer ${path}`);
    return new Response(JSON.stringify(next.body), { status: next.status });
  }) as typeof fetch;
  return asked;
}

function countingSessionEnds(): () => number {
  let ended = 0;
  setUnauthorizedHandler(() => {
    ended += 1;
  });
  return () => ended;
}

afterEach(() => {
  globalThis.fetch = realFetch;
  setUnauthorizedHandler(null);
});

const REFUSED = { status: 401, body: { detail: "Sign in to continue." } };
const signedIn = (yes: boolean): Answer => ({ status: 200, body: { setup_needed: false, signed_in: yes, owner_name: null } });

test("a request refused for a session that was just replaced is sent again and nobody is signed out", async () => {
  const ended = countingSessionEnds();
  const asked = serve({ "/api/things": [REFUSED, { status: 200, body: { n: 1 } }], "/api/auth/state": [signedIn(true)] });
  assert.deepEqual(await request("POST", "/api/things", { body: { a: 1 } }), { n: 1 });
  assert.deepEqual(asked, ["POST /api/things", "GET /api/auth/state", "POST /api/things"]);
  assert.equal(ended(), 0);
});

test("a request refused because the session really ended signs the app out and is not sent again", async () => {
  const ended = countingSessionEnds();
  const asked = serve({ "/api/things": [REFUSED], "/api/auth/state": [signedIn(false)] });
  await assert.rejects(request("GET", "/api/things"), (error) => error instanceof ApiError && error.status === 401);
  assert.deepEqual(asked, ["GET /api/things", "GET /api/auth/state"]);
  assert.equal(ended(), 1);
});

test("a request is sent again once only, so a server that keeps refusing cannot make a loop", async () => {
  const ended = countingSessionEnds();
  const asked = serve({ "/api/things": [REFUSED, REFUSED], "/api/auth/state": [signedIn(true)] });
  await assert.rejects(request("GET", "/api/things"), (error) => error instanceof ApiError && error.status === 401);
  assert.equal(asked.length, 3);
  assert.equal(ended(), 1);
});

test("requests refused together share one check of the session", async () => {
  const ended = countingSessionEnds();
  const asked = serve({
    "/api/a": [REFUSED, { status: 200, body: { a: 1 } }],
    "/api/b": [REFUSED, { status: 200, body: { b: 1 } }],
    "/api/auth/state": [signedIn(true)],
  });
  assert.deepEqual(await Promise.all([request("GET", "/api/a"), request("GET", "/api/b")]), [{ a: 1 }, { b: 1 }]);
  assert.equal(asked.filter((line) => line.endsWith("/api/auth/state")).length, 1);
  assert.equal(ended(), 0);
});

test("when the session cannot be checked the app signs out as before", async () => {
  const ended = countingSessionEnds();
  serve({ "/api/things": [REFUSED], "/api/auth/state": [] });
  await assert.rejects(request("GET", "/api/things"), (error) => error instanceof ApiError && error.status === 401);
  assert.equal(ended(), 1);
});

test("a request that does not need a session is never sent again", async () => {
  const ended = countingSessionEnds();
  const asked = serve({ "/api/auth/login": [{ status: 401, body: { detail: "That password is not right." } }] });
  await assert.rejects(request("POST", "/api/auth/login", { body: {}, authed: false }), (error) => error instanceof ApiError && error.detail === "That password is not right.");
  assert.deepEqual(asked, ["POST /api/auth/login"]);
  assert.equal(ended(), 0);
});
