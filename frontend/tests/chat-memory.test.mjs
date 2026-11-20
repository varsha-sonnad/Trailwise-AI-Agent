// chat-memory.test.mjs: tests for the chat's per-session memory (src/hooks/chatMemory.js) and for what src/api/chat.js
// sends: { message, history, last_mode, last_place }. Node's built-in test runner, no extra library:
//     cd frontend
//     node --test
// The real source files are loaded through Vite's server-side loader and fetch is replaced by a fake: no browser, no
// backend and no network. The hook itself (useChat.js) needs a browser to run, so what it does is kept in these small
// pure functions and checked here; the hook only wires them to React state.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, describe, test } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "package.json"));
const { createServer } = await import(pathToFileURL(path.join(root, "node_modules", "vite", "dist", "node", "index.js")).href);

let server, memory, chatApi;
before(async () => {
  server = await createServer({ root, server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  memory = await server.ssrLoadModule("/src/hooks/chatMemory.js");
  chatApi = await server.ssrLoadModule("/src/api/chat.js");
});
after(async () => {
  await server.close();
});

const msg = (id, role, text, extra = {}) => ({ id, role, text, images: [], ...extra });
const KEDARNATH = { name: "Kedarnath", lat: 30.7352, lng: 79.0669 };

describe("buildHistory", () => {
  test("keeps the user messages and the assistant's reply texts, in order, with the roles the backend wants", () => {
    const history = memory.buildHistory([msg(1, "user", "Tell me about Kedarnath"), msg(2, "bot", "A pilgrimage town."), msg(3, "user", "And the weather?")]);
    assert.deepEqual(history, [
      { role: "user", content: "Tell me about Kedarnath" },
      { role: "assistant", content: "A pilgrimage town." },
      { role: "user", content: "And the weather?" },
    ]);
  });

  test("never contains anything but a role and a content (no ids, no images, no other fields)", () => {
    const history = memory.buildHistory([msg(7, "bot", "Reply", { images: [{ url: "https://images.pexels.com/x.jpg" }], extra: "x" })]);
    assert.deepEqual(Object.keys(history[0]).sort(), ["content", "role"]);
    assert.doesNotMatch(JSON.stringify(history), /pexels|images|"id"/);
  });

  test("leaves out the welcome line, error messages and blank texts", () => {
    const history = memory.buildHistory([
      ...memory.initialMessages(), msg(1, "user", "hello"), msg(2, "error", "Cannot reach the server."), msg(3, "bot", "   "), msg(4, "bot", "Hi!"),
    ]);
    assert.deepEqual(history, [{ role: "user", content: "hello" }, { role: "assistant", content: "Hi!" }]);
    assert.doesNotMatch(JSON.stringify(history), /Tell me where you want to go|Cannot reach/);
  });

  test("a conversation with only the welcome line has no history", () => {
    assert.deepEqual(memory.buildHistory(memory.initialMessages()), []);
  });

  test("anything that is not a list of messages gives an empty history", () => {
    for (const value of [undefined, null, "text", 5, {}, [null, "x", 5, {}, { role: "user" }, { role: "user", text: 5 }, { role: "system", text: "x" }]]) {
      assert.deepEqual(memory.buildHistory(value), []);
    }
  });

  test("a long text is cut to 2000 characters", () => {
    const [m] = memory.buildHistory([msg(1, "bot", "x".repeat(5000))]);
    assert.equal(m.content.length, 2000);
  });

  test("only the newest 10 messages are kept", () => {
    const messages = Array.from({ length: 25 }, (_, i) => msg(i + 1, i % 2 === 0 ? "user" : "bot", `m${i + 1}`));
    const history = memory.buildHistory(messages);
    assert.equal(history.length, 10);
    assert.equal(history[0].content, "m16");
    assert.equal(history.at(-1).content, "m25");
  });

  test("the total is kept within 12000 characters by dropping the oldest messages first", () => {
    const messages = Array.from({ length: 10 }, (_, i) => msg(i + 1, i % 2 === 0 ? "user" : "bot", `${i + 1}`.padEnd(2000, "x")));
    const history = memory.buildHistory(messages);
    const total = history.reduce((n, m) => n + m.content.length, 0);
    assert.ok(total <= 12000, `total ${total}`);
    assert.equal(history.length, 6);
    assert.ok(history[0].content.startsWith("5x"), "the 4 oldest were dropped"); // messages 5 to 10 remain
    assert.ok(history.at(-1).content.startsWith("10"));
  });

  test("every message and the total are always inside the limits the backend accepts", () => {
    const messages = Array.from({ length: 40 }, (_, i) => msg(i + 1, i % 2 === 0 ? "user" : "bot", "y".repeat(1000 + i * 100)));
    const history = memory.buildHistory(messages);
    assert.ok(history.length <= memory.MAX_HISTORY_MESSAGES);
    assert.ok(history.every((m) => m.content.length <= memory.MAX_HISTORY_MESSAGE_CHARS));
    assert.ok(history.reduce((n, m) => n + m.content.length, 0) <= memory.MAX_HISTORY_TOTAL_CHARS);
  });

  test("control characters (which the backend refuses) become spaces; newlines and tabs stay", () => {
    const [m] = memory.buildHistory([msg(1, "bot", "a\u0000b\u001b[0mc\u007fd\nline\ttab")]);
    assert.equal(m.content, "a b [0mc d\nline\ttab");
    assert.doesNotMatch(m.content, /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/);
  });

  test("does not change the messages it is given", () => {
    const messages = [msg(1, "user", "a".repeat(3000))];
    memory.buildHistory(messages);
    assert.equal(messages[0].text.length, 3000);
  });
});

describe("the limits match the backend", () => {
  const backend = readFileSync(path.join(root, "..", "backend", "app", "models", "chat.py"), "utf-8");
  const number = (name) => Number(new RegExp(`^${name} = (\\d+)`, "m").exec(backend)[1]);
  test("message count, message length and total length", () => {
    assert.equal(memory.MAX_HISTORY_MESSAGES, number("MAX_HISTORY_MESSAGES"));
    assert.equal(memory.MAX_HISTORY_MESSAGE_CHARS, number("MAX_HISTORY_MESSAGE_CHARS"));
    assert.equal(memory.MAX_HISTORY_TOTAL_CHARS, number("MAX_HISTORY_TOTAL_CHARS"));
  });
  test("the fixed list of modes", () => {
    const modes = readFileSync(path.join(root, "..", "backend", "app", "agent", "modes.py"), "utf-8");
    const list = /^MODES = \(([^)]*)\)/m.exec(modes)[1].split(",").map((s) => s.trim().replace(/"/g, "")).filter(Boolean);
    assert.deepEqual(memory.MODES, list);
  });
});

describe("isValidPlace", () => {
  const good = [KEDARNATH, { name: "Pole", lat: -90, lng: -180 }, { name: "Edge", lat: 90, lng: 180 }, { name: "Zero", lat: 0, lng: 0 }];
  for (const place of good) test(`accepts ${place.name}`, () => assert.equal(memory.isValidPlace(place), true));
  const bad = [
    null, undefined, "Goa", 5, [], {}, { name: "", lat: 1, lng: 1 }, { name: "   ", lat: 1, lng: 1 }, { name: "x".repeat(101), lat: 1, lng: 1 },
    { name: "Goa", lat: 90.1, lng: 0 }, { name: "Goa", lat: 0, lng: -180.1 }, { name: "Goa", lat: "1", lng: 1 }, { name: "Goa", lat: NaN, lng: 1 },
    { name: "Goa", lat: Infinity, lng: 1 }, { name: "Goa", lat: null, lng: 1 }, { name: "Goa", lat: 1 }, { name: 5, lat: 1, lng: 1 },
  ];
  bad.forEach((place, i) => test(`refuses case ${i}`, () => assert.equal(memory.isValidPlace(place), false)));
});

describe("nextMemory", () => {
  const start = { lastMode: null, lastPlace: null }; // the same as emptyMemory() (checked in the next suite)

  test("remembers the mode of the reply and the first place it geocoded", () => {
    const next = memory.nextMemory(start, { mode: "explore", places: [KEDARNATH, { name: "Rishikesh", lat: 30.1, lng: 78.3 }] });
    assert.deepEqual(next, { lastMode: "explore", lastPlace: KEDARNATH });
  });

  test("every one of the four modes is remembered", () => {
    for (const mode of ["explore", "route", "itinerary", "email"]) assert.equal(memory.nextMemory(start, { mode, places: [] }).lastMode, mode);
  });

  test("an unknown mode is ignored and the earlier one is kept", () => {
    const before = { lastMode: "route", lastPlace: null };
    for (const mode of ["shopping", "", null, undefined, 5, ["email"], "EXPLORE"]) assert.equal(memory.nextMemory(before, { mode, places: [] }).lastMode, "route");
  });

  test("a reply with no places keeps the earlier place (so a follow-up still knows where)", () => {
    const before = { lastMode: "explore", lastPlace: KEDARNATH };
    for (const places of [[], undefined, null, "x", [null, { name: "Bad", lat: 99, lng: 0 }]]) assert.deepEqual(memory.nextMemory(before, { mode: "explore", places }).lastPlace, KEDARNATH);
  });

  test("a new place replaces the old one, and an invalid place before a valid one is skipped", () => {
    const before = { lastMode: "explore", lastPlace: KEDARNATH };
    const next = memory.nextMemory(before, { mode: "explore", places: [{ name: "Bad", lat: 99, lng: 0 }, { name: " Goa ", lat: 15.3, lng: 74.0 }] });
    assert.deepEqual(next.lastPlace, { name: "Goa", lat: 15.3, lng: 74.0 });
  });

  test("does not change what it is given", () => {
    const before = { lastMode: "explore", lastPlace: KEDARNATH };
    memory.nextMemory(before, { mode: "route", places: [{ name: "Goa", lat: 15, lng: 74 }] });
    assert.deepEqual(before, { lastMode: "explore", lastPlace: KEDARNATH });
  });

  test("a missing response gives back the earlier memory", () => {
    assert.deepEqual(memory.nextMemory({ lastMode: "email", lastPlace: KEDARNATH }, undefined), { lastMode: "email", lastPlace: KEDARNATH });
  });
});

describe("a chat from start to New chat", () => {
  test("memoryFor carries the history, the mode and the place; after a reset it carries nothing", () => {
    let messages = memory.initialMessages();
    let mem = memory.emptyMemory();
    // First question: nothing to remember yet.
    assert.deepEqual(memory.memoryFor(messages, mem), { history: [], lastMode: null, lastPlace: null });
    // The first reply arrives (the hook adds both messages, then remembers the mode and the place).
    messages = [...messages, msg(1, "user", "Tell me about Kedarnath"), msg(2, "bot", "A pilgrimage town.")];
    mem = memory.nextMemory(mem, { mode: "explore", places: [KEDARNATH] });
    assert.deepEqual(memory.memoryFor(messages, mem), {
      history: [{ role: "user", content: "Tell me about Kedarnath" }, { role: "assistant", content: "A pilgrimage town." }],
      lastMode: "explore",
      lastPlace: KEDARNATH,
    });
    // New chat: the same two calls the hook makes.
    messages = memory.initialMessages();
    mem = memory.emptyMemory();
    assert.deepEqual(memory.memoryFor(messages, mem), { history: [], lastMode: null, lastPlace: null });
  });

  test("emptyMemory is nothing remembered", () => {
    assert.deepEqual(memory.emptyMemory(), { lastMode: null, lastPlace: null });
  });

  test("the start state is new every time, so one chat can never change the next", () => {
    const a = memory.initialMessages();
    a[0].text = "changed";
    a.push(msg(1, "user", "x"));
    assert.equal(memory.initialMessages().length, 1);
    assert.equal(memory.initialMessages()[0].text, memory.GREETING);
    const m = memory.emptyMemory();
    m.lastPlace = KEDARNATH;
    assert.equal(memory.emptyMemory().lastPlace, null);
  });

  test("the welcome line is marked so it is never sent back as if the assistant had said it", () => {
    assert.equal(memory.initialMessages()[0].welcome, true);
    assert.equal(memory.initialMessages()[0].text, memory.GREETING);
  });
});

describe("sendMessage sends { message, history, last_mode, last_place }", () => {
  const realFetch = globalThis.fetch;
  let sent;
  const fake = (body, ok = true, status = 200) => {
    sent = null;
    globalThis.fetch = async (url, init) => {
      sent = { url, init, body: JSON.parse(init.body) };
      return { ok, status, json: async () => body };
    };
  };
  const restore = () => { globalThis.fetch = realFetch; };

  test("with memory", async () => {
    fake({ reply: "Cold at night.", images: [], mode: "explore", places: [KEDARNATH] });
    try {
      const history = [{ role: "user", content: "Tell me about Kedarnath" }, { role: "assistant", content: "A pilgrimage town." }];
      const result = await chatApi.sendMessage("And the weather?", { history, lastMode: "explore", lastPlace: KEDARNATH });
      assert.deepEqual(Object.keys(sent.body).sort(), ["history", "last_mode", "last_place", "message"]);
      assert.deepEqual(sent.body, { message: "And the weather?", history, last_mode: "explore", last_place: KEDARNATH });
      assert.deepEqual(result, { reply: "Cold at night.", images: [], mode: "explore", places: [KEDARNATH], route: [] });
    } finally {
      restore();
    }
  });

  test("without memory it sends an empty history and nulls", async () => {
    fake({ reply: "Hi." });
    try {
      await chatApi.sendMessage("hello");
      assert.deepEqual(sent.body, { message: "hello", history: [], last_mode: null, last_place: null });
      await chatApi.sendMessage("hello", { history: "not a list", lastMode: undefined, lastPlace: undefined });
      assert.deepEqual(sent.body, { message: "hello", history: [], last_mode: null, last_place: null });
    } finally {
      restore();
    }
  });

  test("the response's mode and places come back as a string and a list (or null and an empty list)", async () => {
    try {
      fake({ reply: "Hi.", mode: "route", places: [KEDARNATH] });
      assert.deepEqual((await chatApi.sendMessage("x")).places, [KEDARNATH]);
      for (const body of [{ reply: "Hi." }, { reply: "Hi.", mode: 5, places: "x" }, { reply: "Hi.", mode: null, places: null }]) {
        fake(body);
        const result = await chatApi.sendMessage("x");
        assert.equal(result.mode, null);
        assert.deepEqual(result.places, []);
      }
    } finally {
      restore();
    }
  });

  test("a 422 is still a friendly message", async () => {
    fake({}, false, 422);
    try {
      await assert.rejects(() => chatApi.sendMessage("x"), /not accepted/);
    } finally {
      restore();
    }
  });
});

describe("nothing is stored in the browser", () => {
  test("the chat files never touch localStorage, sessionStorage, IndexedDB or cookies", () => {
    for (const file of ["hooks/useChat.js", "hooks/chatMemory.js", "api/chat.js", "components/MessageList.jsx", "components/MessageBubble.jsx"]) {
      const source = readFileSync(path.join(root, "src", file), "utf-8")
        .replace(/\/\*[\s\S]*?\*\//g, "") // block comments
        .replace(/^\s*\/\/.*$/gm, "") // whole-line comments
        .replace(/\s\/\/ .*$/gm, ""); // end-of-line comments
      assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie|caches\./, file);
    }
  });

  test("useChat keeps the memory in React state and refs, and has a reset", () => {
    const source = readFileSync(path.join(root, "src", "hooks", "useChat.js"), "utf-8");
    assert.match(source, /useState\(initialMessages\)/);
    assert.match(source, /useRef\(emptyMemory\(\)\)/);
    assert.match(source, /const reset = useCallback/);
    assert.match(source, /return \{ messages, pending, send, reset \}/);
  });

  test("useChat ignores a reply that arrives after New chat", () => {
    const source = readFileSync(path.join(root, "src", "hooks", "useChat.js"), "utf-8");
    assert.equal((source.match(/mine !== generation\.current/g) || []).length, 2); // on success and on error
    assert.match(source, /generation\.current \+= 1/);
  });
});
