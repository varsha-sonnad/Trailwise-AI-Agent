// reply-actions.test.mjs: tests for the buttons under bot replies: the visibility rule (components/replyActions.js), the trip
// body, the save-then-email flow, the calls (api/trips.js), and how ReplyActions, LoginPrompt and MessageBubble render.
// Only Node's built-in test runner is used (run `node --test` in frontend/). Components are rendered to static HTML and fetch is
// faked, so no browser, backend or network is needed.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, describe, test } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "package.json"));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { createServer } = await import(pathToFileURL(path.join(root, "node_modules", "vite", "dist", "node", "index.js")).href);

let server, rules, tripsApi, clientApi, ReplyActions, ReplyActionsContext, LoginPrompt, MessageBubble;
const h = React.createElement;

before(async () => {
  server = await createServer({ root, server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  rules = await server.ssrLoadModule("/src/components/replyActions.js");
  tripsApi = await server.ssrLoadModule("/src/api/trips.js");
  clientApi = await server.ssrLoadModule("/src/api/client.js");
  ({ default: ReplyActions, ReplyActionsContext } = await server.ssrLoadModule("/src/components/ReplyActions.jsx"));
  ({ default: LoginPrompt } = await server.ssrLoadModule("/src/components/LoginPrompt.jsx"));
  ({ default: MessageBubble } = await server.ssrLoadModule("/src/components/MessageBubble.jsx"));
});
after(async () => {
  await server.close();
});

const source = (...parts) => readFileSync(path.join(root, ...parts), "utf-8").replace(/\r\n/g, "\n");
const code = (...parts) => source(...parts).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const HAMPI = { name: "Hampi", lat: 15.335, lng: 76.46 };
const BADAMI = { name: "Badami", lat: 15.915, lng: 75.676 };
const ROAD = { kind: "road", points: [[15.3, 76.4], [15.9, 75.6]] };
const bot = (over = {}) => ({ role: "bot", text: "A plan.", mode: "itinerary", places: [HAMPI], legs: [], ...over });
const TRIP_ID = "0123456789abcdef0123456789abcdef";

// ---------------------------------------------------------------- the visibility rule
describe("replyActions: which buttons appear", () => {
  test("an itinerary reply with a valid place is a plan reply", () => {
    assert.deepEqual(rules.replyActions(bot()), { kind: "plan" });
  });
  test("a route reply with legs is a plan reply; without legs it is not", () => {
    assert.deepEqual(rules.replyActions(bot({ mode: "route", places: [HAMPI, BADAMI], legs: [ROAD] })), { kind: "plan" });
    assert.deepEqual(rules.replyActions(bot({ mode: "route", places: [HAMPI, BADAMI], legs: [] })), { kind: "none" });
    assert.deepEqual(rules.replyActions(bot({ mode: "route", places: [HAMPI, BADAMI], legs: [{ kind: "flight", points: ROAD.points }, { kind: "road", points: [[1, 1]] }] })), { kind: "none" });
  });
  test("a route reply with legs is a plan reply even with no places (the legs alone decide)", () => {
    assert.deepEqual(rules.replyActions(bot({ mode: "route", places: [], legs: [ROAD] })), { kind: "plan" });
  });
  test("an explore reply about exactly one place gets one 'Plan a trip' button for that place", () => {
    assert.deepEqual(rules.replyActions(bot({ mode: "explore" })), { kind: "explore", place: "Hampi" });
  });
  test("an explore reply with several places, or none, gets nothing (short explore answers, greetings)", () => {
    assert.deepEqual(rules.replyActions(bot({ mode: "explore", places: [HAMPI, BADAMI] })), { kind: "none" });
    assert.deepEqual(rules.replyActions(bot({ mode: "explore", places: [] })), { kind: "none" });
  });
  test("clarifying questions and failure replies carry no places or legs, so they get nothing", () => {
    assert.deepEqual(rules.replyActions(bot({ mode: "itinerary", places: [], legs: [] })), { kind: "none" }); // a question
    assert.deepEqual(rules.replyActions(bot({ mode: "itinerary", places: [{ name: "X", lat: 999, lng: 0 }] })), { kind: "none" }); // unusable place
    assert.deepEqual(rules.replyActions(bot({ mode: null, places: [], legs: [] })), { kind: "none" });
    assert.deepEqual(rules.replyActions(bot({ mode: "email" })), { kind: "none" });
  });
  test("error and user messages never get buttons, whatever they carry", () => {
    for (const role of ["error", "user"]) {
      assert.deepEqual(rules.replyActions(bot({ role })), { kind: "none" });
      assert.deepEqual(rules.replyActions(bot({ role, mode: "explore" })), { kind: "none" });
    }
    for (const bad of [null, undefined, {}, "x", 5]) assert.deepEqual(rules.replyActions(bad), { kind: "none" });
  });
  test("the decision never reads the wording: the same fields give the same answer for any text", () => {
    const wordings = ["Here is your plan!", "I could not check live information right now.", "Which city do you start from?", "", "Hi!", "The assistant is busy right now."];
    for (const mode of ["itinerary", "route", "explore"]) {
      const first = rules.replyActions(bot({ mode, legs: [ROAD], text: wordings[0] }));
      for (const text of wordings) assert.deepEqual(rules.replyActions(bot({ mode, legs: [ROAD], text })), first);
    }
    const src = code("src", "components", "replyActions.js");
    assert.doesNotMatch(src.slice(src.indexOf("export function replyActions"), src.indexOf("export function planMessage")), /\.text\b|\.test\(|match\(|includes\(/);
  });
});

// ---------------------------------------------------------------- the trip that gets saved
describe("tripBody and tripTitle", () => {
  test("titles come from the places: itinerary 'Trip to X', route 'X to Y'", () => {
    assert.equal(rules.tripTitle(bot()), "Trip to Hampi");
    assert.equal(rules.tripTitle(bot({ mode: "route", places: [HAMPI, BADAMI], legs: [ROAD] })), "Hampi to Badami");
    assert.equal(rules.tripTitle(bot({ mode: "route", places: [], legs: [ROAD] })), "Trip plan");
  });
  test("the body has only the fields the server accepts, with its limits applied", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ name: `Place ${i}`, lat: 10 + i, lng: 70 + i }));
    const body = rules.tripBody(bot({ text: "x".repeat(9000), places: [{ name: "n".repeat(300), lat: 1, lng: 1 }, ...many] }));
    assert.deepEqual(Object.keys(body).sort(), ["places", "reply", "summary", "title"]);
    assert.equal(body.reply.length, 8000);
    assert.ok(body.places.length <= 12 && body.title.length <= 100);
    assert.ok(body.places.every((p) => Object.keys(p).sort().join() === "lat,lng,name"));
    assert.equal(JSON.stringify(body).includes("owner"), false); // the owner always comes from the login token
  });
});

// ---------------------------------------------------------------- Email plan: save first if needed
describe("emailPlan", () => {
  const make = (over = {}) => {
    const log = [];
    const api = {
      async saveTrip() { log.push("save"); return TRIP_ID; },
      async emailTrip(id) { log.push(`email:${id}`); },
      ...over,
    };
    return { log, api };
  };
  test("an unsaved plan is saved first, then emailed with the new id", async () => {
    const { log, api } = make();
    let kept = null;
    assert.equal(await rules.emailPlan(bot(), null, api, (id) => (kept = id)), TRIP_ID);
    assert.deepEqual(log, ["save", `email:${TRIP_ID}`]);
    assert.equal(kept, TRIP_ID);
  });
  test("an already saved plan is not saved again", async () => {
    const { log, api } = make();
    await rules.emailPlan(bot(), TRIP_ID, api, () => assert.fail("must not save again"));
    assert.deepEqual(log, [`email:${TRIP_ID}`]);
  });
  test("if the email step fails the id is already kept (the trip stays saved), and the error comes through", async () => {
    const { api } = make({ async emailTrip() { throw new clientApi.ApiError(429, "limit"); } });
    let kept = null;
    await assert.rejects(rules.emailPlan(bot(), null, api, (id) => (kept = id)), { status: 429 });
    assert.equal(kept, TRIP_ID);
  });
  test("if the save fails nothing is emailed", async () => {
    const { log, api } = make({ async saveTrip() { throw new clientApi.ApiError(401, "login"); } });
    await assert.rejects(rules.emailPlan(bot(), null, api, () => assert.fail("not saved")), { status: 401 });
    assert.deepEqual(log, []);
  });
});

// ---------------------------------------------------------------- the calls
describe("api/trips.js", () => {
  const calls = [];
  const fake = (status, body) => {
    calls.length = 0;
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init });
      return { ok: status >= 200 && status < 300, status, json: async () => body };
    };
  };
  const real = globalThis.fetch;
  after(() => {
    globalThis.fetch = real;
  });

  test("saveTrip posts the body to /trips and returns the id", async () => {
    fake(201, { id: TRIP_ID });
    assert.equal(await tripsApi.saveTrip(bot()), TRIP_ID);
    assert.match(calls[0].url, /\/trips$/);
    assert.equal(calls[0].init.method, "POST");
    assert.deepEqual(JSON.parse(calls[0].init.body), rules.tripBody(bot()));
  });
  test("saveTrip refuses an answer without a proper id", async () => {
    for (const body of [{}, { id: "x" }, { id: "../../me" }, null]) {
      fake(201, body);
      await assert.rejects(tripsApi.saveTrip(bot()), { name: "ApiError" });
    }
  });
  test("saveTrip maps the statuses to readable messages", async () => {
    for (const [status, pattern] of [[401, /log in/i], [409, /limit of 100/], [422, /could not be saved/], [503, /not available/], [500, /Could not save/]]) {
      fake(status, {});
      await assert.rejects(tripsApi.saveTrip(bot()), (e) => e.status === status && pattern.test(e.message));
    }
  });
  test("emailTrip posts to /trips/{id}/email with no recipient and is true only when sent is true", async () => {
    fake(200, { sent: true, to: "a***@example.com" });
    assert.equal(await tripsApi.emailTrip(TRIP_ID), true);
    assert.match(calls[0].url, new RegExp(`/trips/${TRIP_ID}/email$`));
    assert.equal(calls[0].init.body, "{}");
    for (const body of [{ sent: false }, {}, null, { sent: "true" }]) {
      fake(200, body);
      await assert.rejects(tripsApi.emailTrip(TRIP_ID), { name: "ApiError" });
    }
  });
  test("emailTrip refuses a bad id before any request", async () => {
    fake(200, { sent: true });
    for (const id of [null, "", "abc", "../me", `${TRIP_ID}/x`]) await assert.rejects(tripsApi.emailTrip(id), { name: "ApiError" });
    assert.equal(calls.length, 0);
  });
  test("emailTrip maps the statuses to readable messages", async () => {
    for (const [status, pattern] of [[401, /log in/i], [403, /not verified/], [404, /not found/], [429, /at most 5/], [502, /could not be sent/], [503, /not available/]]) {
      fake(status, {});
      await assert.rejects(tripsApi.emailTrip(TRIP_ID), (e) => e.status === status && pattern.test(e.message));
    }
  });
});

// ---------------------------------------------------------------- what is drawn
const render = (message, ctx = {}) =>
  renderToStaticMarkup(h(ReplyActionsContext.Provider, { value: { user: { name: "A" }, onLogin() {}, onSend() {}, pending: false, ...ctx } }, h(ReplyActions, { message })));

describe("ReplyActions rendering", () => {
  test("a plan reply shows Save trip and Email plan", () => {
    const html = render(bot());
    assert.match(html, />Save trip</);
    assert.match(html, />Email plan</);
    assert.doesNotMatch(html, /Plan a trip to/);
  });
  test("an explore reply for one place shows one 'Plan a trip to <place>' button", () => {
    const html = render(bot({ mode: "explore" }));
    assert.equal((html.match(/<button/g) || []).length, 1);
    assert.match(html, />Plan a trip to Hampi</);
  });
  test("it is disabled while a reply is pending", () => {
    assert.match(render(bot({ mode: "explore" }), { pending: true }), /disabled/);
    assert.doesNotMatch(render(bot({ mode: "explore" }), { pending: false }), /disabled/);
  });
  test("nothing is drawn for greetings, questions, short explore answers or errors", () => {
    for (const m of [bot({ mode: "explore", places: [] }), bot({ places: [] }), bot({ mode: "explore", places: [HAMPI, BADAMI] }), bot({ role: "error" }), bot({ mode: "route", legs: [] })]) {
      assert.equal(render(m), "");
    }
  });
  test("a guest sees the same buttons (the popup comes on press)", () => {
    assert.match(render(bot(), { user: null }), />Save trip</);
  });
  test("the explore button sends exactly 'Plan a trip to <place>'", () => {
    assert.equal(rules.planMessage("Hampi"), "Plan a trip to Hampi");
    assert.match(code("src", "components", "ReplyActions.jsx"), /onSend\(planMessage\(action\.place\)\)/);
  });
  test("the states read 'Saved ✓' and 'Sent to your email' and disable the buttons; a guest gets the login popup", () => {
    const src = code("src", "components", "ReplyActions.jsx");
    assert.match(src, /tripId \? "Saved ✓"/);
    assert.match(src, /sent \? "Sent to your email"/);
    assert.match(src, /disabled=\{Boolean\(tripId\) \|\| busy\}/);
    assert.match(src, /disabled=\{sent \|\| busy\}/);
    assert.match(src, /if \(!user\) \{\s*setShowLogin\(true\);\s*return;/); // a guest: popup, no request
    assert.match(src, /err\.status === 401\) setShowLogin\(true\)/); // an expired session too
    assert.match(src, /setSent\(true\)/);
    assert.match(src, /await emailPlan\(message, tripId, API, setTripId\)/); // saves first when needed
  });
});

describe("LoginPrompt", () => {
  test("is a labelled modal dialog with Log in and Cancel", () => {
    const html = renderToStaticMarkup(h(LoginPrompt, { onLogin() {}, onClose() {} }));
    assert.match(html, /role="dialog"/);
    assert.match(html, /aria-modal="true"/);
    assert.match(html, />Log in</);
    assert.match(html, />Cancel</);
  });
  test("closes on Escape and uses the login function it is given", () => {
    const src = code("src", "components", "LoginPrompt.jsx");
    assert.match(src, /event\.key === "Escape"/);
    assert.match(src, /onClick=\{\(\) => onLogin\(\)\}/);
  });
});

describe("MessageBubble puts the buttons under plan replies only", () => {
  const bubble = (props) =>
    renderToStaticMarkup(h(ReplyActionsContext.Provider, { value: { user: null, onLogin() {}, onSend() {}, pending: false } }, h(MessageBubble, props)));
  test("a bot itinerary reply has them; an error or a question does not", () => {
    assert.match(bubble({ role: "bot", text: "Day 1...", places: [HAMPI], mode: "itinerary", legs: [] }), /Save trip/);
    assert.doesNotMatch(bubble({ role: "error", text: "The assistant is busy right now.", places: [HAMPI], mode: "itinerary" }), /Save trip|Email plan/);
    assert.doesNotMatch(bubble({ role: "bot", text: "Which dates?", places: [], mode: "itinerary", legs: [] }), /Save trip|Email plan/);
  });
  test("the app passes mode and legs down and provides who is signed in", () => {
    assert.match(code("src", "components", "MessageList.jsx"), /mode=\{m\.mode\}\s+legs=\{m\.legs\}/);
    assert.match(code("src", "App.jsx"), /<ReplyActionsContext\.Provider value=\{\{ user, onLogin: login, onSend: send, pending \}\}>/);
  });
});
