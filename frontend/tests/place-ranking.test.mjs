// place-ranking.test.mjs: tests for ranking places by saved preferences (rankPlaces) and the Plan a Trip message
// (planPrompt), both in src/data/places.js. Node's built-in test runner:  cd frontend && node --test
// The real source is loaded through Vite's server-side loader: no browser, no network. The React wiring needs a browser;
// what it decides lives in these pure functions.

import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { createServer } = await import(pathToFileURL(path.join(root, "node_modules", "vite", "dist", "node", "index.js")).href);

let server, places, prefsApi;
before(async () => {
  server = await createServer({ root, server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  places = await server.ssrLoadModule("/src/data/places.js");
  prefsApi = await server.ssrLoadModule("/src/api/preferences.js");
});
after(async () => {
  await server.close();
});

const ids = (list) => list.map((p) => p.id);
const EMPTY = { travel_mode: [], budget: null, interests: [], reply_language: null };

describe("ranking without preferences", () => {
  test("no preferences, empty ones, or garbage keep the original order", () => {
    const original = ids(places.PLACES);
    for (const prefs of [undefined, null, EMPTY, "x", 5, { interests: "beach" }, { interests: [] }]) {
      assert.deepEqual(ids(places.rankPlaces(places.PLACES, prefs)), original);
    }
  });
});

describe("ranking with preferences", () => {
  test("places matching the chosen interests move up", () => {
    const order = ids(places.rankPlaces(places.PLACES, { ...EMPTY, interests: ["mountains"] }));
    assert.equal(order[0], "ladakh"); // the only place tagged mountains
  });

  test("more matching tags rank higher; ties keep the original order", () => {
    // heritage + culture: jaipur, varanasi and hampi score 2 (jaipur, varanasi, hampi in original order); goa scores 1 (culture).
    const order = ids(places.rankPlaces(places.PLACES, { ...EMPTY, interests: ["heritage", "culture"] }));
    assert.deepEqual(order.slice(0, 4), ["jaipur", "varanasi", "hampi", "goa"]);
  });

  test("an interest no place has (wildlife) changes nothing", () => {
    assert.deepEqual(ids(places.rankPlaces(places.PLACES, { ...EMPTY, interests: ["wildlife"] })), ids(places.PLACES));
  });

  test("budget style and travel mode do not change the order (places have no field for them)", () => {
    const base = ids(places.rankPlaces(places.PLACES, { ...EMPTY, interests: ["beach"] }));
    const withOthers = ids(places.rankPlaces(places.PLACES, { ...EMPTY, interests: ["beach"], budget: "premium", travel_mode: ["train", "bus"] }));
    assert.deepEqual(withOthers, base);
    assert.deepEqual(ids(places.rankPlaces(places.PLACES, { ...EMPTY, budget: "budget", travel_mode: ["bus"] })), ids(places.PLACES));
  });
});

describe("no place is ever removed", () => {
  test("every combination of the ten interests returns all places, each once", () => {
    const all = prefsApi.INTERESTS.map((o) => o.value);
    const expected = ids(places.PLACES).sort();
    for (let mask = 0; mask < 1 << all.length; mask++) {
      const interests = all.filter((_, i) => mask & (1 << i));
      const ranked = places.rankPlaces(places.PLACES, { ...EMPTY, interests });
      assert.equal(ranked.length, places.PLACES.length);
      assert.deepEqual(ids(ranked).sort(), expected); // same places, no duplicates
    }
  });

  test("a place without tags is kept (and ranked last among equals)", () => {
    const list = [{ id: "a" }, { id: "b", tags: ["beach"] }, { id: "c", tags: [] }];
    assert.deepEqual(ids(places.rankPlaces(list, { interests: ["beach"] })), ["b", "a", "c"]);
  });

  test("the input list is not changed and a new array is returned", () => {
    const before = ids(places.PLACES);
    const ranked = places.rankPlaces(places.PLACES, { ...EMPTY, interests: ["mountains", "beach"] });
    assert.notEqual(ranked, places.PLACES);
    assert.deepEqual(ids(places.PLACES), before);
  });
});

describe("the tags", () => {
  test("every place has tags, and every tag is one of the ten allowed interests", () => {
    const allowed = prefsApi.INTERESTS.map((o) => o.value);
    for (const p of places.PLACES) {
      assert.ok(Array.isArray(p.tags) && p.tags.length > 0, `${p.id} has tags`);
      for (const t of p.tags) assert.ok(allowed.includes(t), `${p.id}: ${t}`);
      assert.equal(new Set(p.tags).size, p.tags.length, `${p.id}: no repeated tags`);
    }
  });
});

describe("planPrompt", () => {
  test("without preferences it is the same message as before", () => {
    assert.equal(places.planPrompt("Goa"), "Plan a trip to Goa");
    assert.equal(places.planPrompt("Goa", EMPTY), "Plan a trip to Goa");
    assert.equal(places.planPrompt("Goa", { ...EMPTY, interests: ["beach"], reply_language: "hi" }), "Plan a trip to Goa");
  });

  test("a budget style and a travel mode are added when set", () => {
    assert.equal(places.planPrompt("Goa", { ...EMPTY, budget: "comfort", travel_mode: ["train", "bus"] }), "Plan a trip to Goa. Budget style: comfort. Travel mode: train or bus.");
  });

  test("only what is set is added", () => {
    assert.equal(places.planPrompt("Goa", { ...EMPTY, budget: "premium" }), "Plan a trip to Goa. Budget style: premium.");
    assert.equal(places.planPrompt("Goa", { ...EMPTY, travel_mode: ["bus"] }), "Plan a trip to Goa. Travel mode: bus.");
  });

  test("it never states a price or a timing", () => {
    const text = places.planPrompt("Goa", { ...EMPTY, budget: "budget", travel_mode: ["train"] });
    assert.doesNotMatch(text, /₹|rs\.?\s?\d|\d|hour|minute|cost|price/i);
  });
});
