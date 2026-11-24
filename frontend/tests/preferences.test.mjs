// preferences.test.mjs: tests for the saved-preferences code (src/api/preferences.js and the PUT/GET helpers in
// src/api/client.js). Node's built-in test runner, no extra library:  cd frontend && node --test
// The real source is loaded through Vite's server-side loader; fetch and localStorage are replaced by fakes, so
// there is no browser, no backend and no network. The React hook and page need a browser; what they decide lives
// in the pure helpers tested here.

import assert from "node:assert/strict";
import { after, before, beforeEach, describe, test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { createServer } = await import(pathToFileURL(path.join(root, "node_modules", "vite", "dist", "node", "index.js")).href);

let server, prefs, client;
before(async () => {
  server = await createServer({ root, server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  prefs = await server.ssrLoadModule("/src/api/preferences.js");
  client = await server.ssrLoadModule("/src/api/client.js");
});
after(async () => {
  await server.close();
});

// A fake localStorage (a Map). Set `broken` to make every call throw, like a browser with storage blocked.
let store, broken;
beforeEach(() => {
  store = new Map();
  broken = false;
  globalThis.localStorage = {
    getItem: (k) => {
      if (broken) throw new Error("blocked");
      return store.has(k) ? store.get(k) : null;
    },
    setItem: (k, v) => {
      if (broken) throw new Error("blocked");
      store.set(k, String(v));
    },
    removeItem: (k) => {
      if (broken) throw new Error("blocked");
      store.delete(k);
    },
  };
});

const FULL = { travel_mode: ["train", "bus"], budget: "comfort", interests: ["beach", "food"], reply_language: "hi" };

describe("options match the backend", () => {
  test("the allowed values are exactly the ones the backend accepts", () => {
    assert.deepEqual(prefs.TRAVEL_MODES.map((o) => o.value), ["train", "bus"]);
    assert.deepEqual(prefs.BUDGETS.map((o) => o.value), ["budget", "comfort", "premium"]);
    assert.deepEqual(prefs.INTERESTS.map((o) => o.value), ["beach", "mountains", "heritage", "spiritual", "adventure", "nature", "food", "culture", "relaxing", "wildlife"]);
    assert.deepEqual(prefs.LANGUAGES.map((o) => o.value), ["en", "hi", "kn"]);
    assert.deepEqual(prefs.LANGUAGES.map((o) => o.label), ["English", "हिन्दी", "ಕನ್ನಡ"]);
  });
});

describe("sanitize", () => {
  test("keeps valid choices and drops unknown values, unknown fields and duplicates", () => {
    const clean = prefs.sanitize({ travel_mode: ["bus", "plane", "bus"], budget: "cheap", interests: ["food", "x"], reply_language: "fr", sub: "evil" });
    assert.deepEqual(clean, { travel_mode: ["bus"], budget: null, interests: ["food"], reply_language: null });
  });
  test("anything that is not an object gives the empty preferences", () => {
    for (const bad of [null, undefined, "x", 5, [], { travel_mode: "train" }]) assert.ok(prefs.isEmpty(prefs.sanitize(bad)));
  });
});

describe("decideAfterLogin", () => {
  test("empty account + browser choices: copy them to the account", () => {
    assert.equal(prefs.decideAfterLogin(prefs.EMPTY_PREFERENCES, FULL), "copy");
  });
  test("an account that already has preferences keeps them, even if the browser has others", () => {
    assert.equal(prefs.decideAfterLogin({ ...prefs.EMPTY_PREFERENCES, budget: "premium" }, FULL), "keep-account");
  });
  test("nothing in either place: nothing to do", () => {
    assert.equal(prefs.decideAfterLogin(prefs.EMPTY_PREFERENCES, prefs.EMPTY_PREFERENCES), "none");
  });
});

describe("guest copy in the browser", () => {
  test("a saved copy is read back", () => {
    assert.ok(prefs.saveLocal(FULL));
    assert.deepEqual(prefs.loadLocal(), FULL);
  });
  test("nothing saved gives the empty preferences", () => {
    assert.deepEqual(prefs.loadLocal(), prefs.EMPTY_PREFERENCES);
  });
  test("damaged or edited storage is cleaned or ignored", () => {
    store.set(prefs.STORAGE_KEY, "{not json");
    assert.deepEqual(prefs.loadLocal(), prefs.EMPTY_PREFERENCES);
    store.set(prefs.STORAGE_KEY, JSON.stringify({ budget: "premium", interests: ["bogus"], extra: 1 }));
    assert.deepEqual(prefs.loadLocal(), { ...prefs.EMPTY_PREFERENCES, budget: "premium" });
  });
  test("blocked storage never throws: reads are empty, writes report false", () => {
    broken = true;
    assert.deepEqual(prefs.loadLocal(), prefs.EMPTY_PREFERENCES);
    assert.equal(prefs.saveLocal(FULL), false);
    assert.doesNotThrow(() => prefs.clearLocal());
  });
  test("clearLocal removes the copy", () => {
    prefs.saveLocal(FULL);
    prefs.clearLocal();
    assert.deepEqual(prefs.loadLocal(), prefs.EMPTY_PREFERENCES);
  });
});

describe("the API calls", () => {
  let calls;
  beforeEach(() => {
    calls = [];
    client.setAuthHeaderProvider(async () => ({ Authorization: "Bearer test-token" }));
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init });
      return { ok: true, status: 200, json: async () => FULL };
    };
  });

  test("fetchPreferences does a GET of /me/preferences with the token and no body", async () => {
    assert.deepEqual(await prefs.fetchPreferences(), FULL);
    assert.match(calls[0].url, /\/me\/preferences$/);
    assert.equal(calls[0].init.method, "GET");
    assert.equal(calls[0].init.body, undefined);
    assert.equal(calls[0].init.headers.Authorization, "Bearer test-token");
  });

  test("savePreferences does a PUT with only the four fields: no user id", async () => {
    await prefs.savePreferences({ ...FULL, sub: "someone-else", user_id: "x" });
    assert.equal(calls[0].init.method, "PUT");
    assert.equal(calls[0].init.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(calls[0].init.body), FULL);
  });

  test("a failed save becomes an error with a friendly message", async () => {
    globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({}) });
    await assert.rejects(prefs.savePreferences(FULL), /sign-in has expired/);
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
    await assert.rejects(prefs.fetchPreferences(), /not available right now/);
  });

  test("a reply with unknown values is cleaned before the app uses it", async () => {
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ budget: "luxury", interests: ["food", "zzz"] }) });
    assert.deepEqual(await prefs.fetchPreferences(), { ...prefs.EMPTY_PREFERENCES, interests: ["food"] });
  });
});
