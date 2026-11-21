// chat-images.test.mjs: tests for the photo row under a chat reply (components/ImageRow.jsx, MessageBubble.jsx) and for how
// api/chat.js reads the response's "images". It uses only Node's built-in test runner, no extra library:
//     cd frontend
//     node --test
// The real source files are loaded through Vite's server-side loader (so JSX and import.meta.env work), the components are
// rendered to static HTML, and fetch is replaced by a fake, so no browser, no backend and no network are needed.

import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "package.json"));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { createServer } = await import(pathToFileURL(path.join(root, "node_modules", "vite", "dist", "node", "index.js")).href);

let server, ImageRow, usableImages, isPexelsUrl, MessageBubble, chatApi;
const h = React.createElement;

before(async () => {
  server = await createServer({ root, server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  ({ default: ImageRow, usableImages, isPexelsUrl } = await server.ssrLoadModule("/src/components/ImageRow.jsx"));
  ({ default: MessageBubble } = await server.ssrLoadModule("/src/components/MessageBubble.jsx"));
  chatApi = await server.ssrLoadModule("/src/api/chat.js");
});
after(async () => {
  await server.close();
});

// One photo as the backend sends it.
function photo(n = 1, over = {}) {
  return {
    url: `https://images.pexels.com/photos/${n}/large.jpeg`,
    thumb: `https://images.pexels.com/photos/${n}/medium.jpeg`,
    alt: `Stone temple ${n}`,
    photographer: `Photographer ${n}`,
    photographer_url: `https://www.pexels.com/@photographer-${n}`,
    pexels_url: `https://www.pexels.com/photo/hampi-${n}/`,
    ...over,
  };
}
const render = (images) => renderToStaticMarkup(h(ImageRow, { images }));

describe("isPexelsUrl", () => {
  const cases = [
    ["https://images.pexels.com/photos/1/large.jpeg", true],
    ["https://www.pexels.com/photo/x/", true],
    ["https://pexels.com/x", true],
    ["http://images.pexels.com/a.jpg", false],
    ["https://pexels.com.evil.example/a.jpg", false],
    ["https://evilpexels.com/a.jpg", false],
    ["https://images.pexels.com.evil.example/a.jpg", false],
    ["javascript:alert(1)", false],
    ["data:image/png;base64,AAAA", false],
    ["//images.pexels.com/a.jpg", false],
    ["", false],
    [null, false],
    [5, false],
    ["https://images.pexels.com/" + "a".repeat(600), false],
  ];
  for (const [value, expected] of cases) {
    test(`${String(value).slice(0, 50)} -> ${expected}`, () => assert.equal(isPexelsUrl(value), expected));
  }
});

describe("ImageRow", () => {
  test("shows each photo large, with a small corner credit that links to the photographer", () => {
    const html = render([photo(1), photo(2)]);
    assert.equal((html.match(/<figure class="image-card"/g) || []).length, 2);
    assert.match(html, /<img src="https:\/\/images\.pexels\.com\/photos\/1\/large\.jpeg"/); // the large picture, not the thumb
    assert.doesNotMatch(html, /medium\.jpeg/);
    assert.match(
      html,
      /<a class="image-credit" href="https:\/\/www\.pexels\.com\/@photographer-1" target="_blank" rel="noopener noreferrer">Photo by Photographer 1 on Pexels<\/a>/,
    );
    assert.match(html, /Photo by Photographer 2 on Pexels/);
  });

  test("has no captions", () => {
    const html = render([photo(1)]);
    assert.doesNotMatch(html, /figcaption/i);
    assert.doesNotMatch(html, /Stone temple 1</); // the alt text is only an attribute, never visible text
  });

  test("the credit links to the photo's own Pexels page when the photographer page is missing or off Pexels", () => {
    assert.match(render([photo(1, { photographer_url: "" })]), /href="https:\/\/www\.pexels\.com\/photo\/hampi-1\/"/);
    assert.match(render([photo(1, { photographer_url: "https://evil.example.com/me" })]), /href="https:\/\/www\.pexels\.com\/photo\/hampi-1\/"/);
  });

  test("with no usable link the credit is plain text, not a link", () => {
    const html = render([photo(1, { photographer_url: "", pexels_url: "" })]);
    assert.match(html, /<span class="image-credit">Photo by Photographer 1 on Pexels<\/span>/);
    assert.doesNotMatch(html, /<a /);
  });

  test("only pictures on Pexels hosts are shown", () => {
    const html = render([
      photo(1, { url: "https://evil.example.com/a.jpg" }),
      photo(2, { url: "http://images.pexels.com/a.jpg" }),
      photo(3, { url: "https://pexels.com.evil.example/a.jpg" }),
      photo(4, { url: "javascript:alert(1)" }),
      photo(5),
    ]);
    assert.equal((html.match(/<img /g) || []).length, 1);
    assert.match(html, /photos\/5\/large\.jpeg/);
    assert.doesNotMatch(html, /evil\.example|javascript:/);
  });

  test("a photo without a photographer name is not shown", () => {
    for (const bad of ["", "   ", null, undefined, 5]) {
      assert.equal(render([photo(1, { photographer: bad })]), "");
    }
  });

  test("duplicates are shown once and at most 6 photos are shown", () => {
    assert.equal(usableImages([photo(1), photo(1), photo(2)]).length, 2);
    assert.equal(usableImages(Array.from({ length: 20 }, (_, i) => photo(i + 1))).length, 6);
  });

  test("nothing is drawn when there are no images", () => {
    for (const value of [undefined, null, [], "text", 5, {}, [null, "x", 5]]) assert.equal(render(value), "");
  });

  test("names and alt text are plain text, never HTML", () => {
    const html = render([photo(1, { photographer: "<script>alert(1)</script>", alt: '"><img src=x onerror=alert(1)>' })]);
    assert.doesNotMatch(html, /<script>/);
    assert.doesNotMatch(html, /<img src=x/);
    assert.match(html, /&lt;script&gt;/);
  });

  test("the images load lazily and send no referrer", () => {
    const html = render([photo(1)]);
    assert.match(html, /loading="lazy"/);
    assert.match(html, /referrerPolicy="no-referrer"/);
  });
});

describe("MessageBubble", () => {
  test("a bot reply shows its text and then the photo row under it", () => {
    const html = renderToStaticMarkup(h(MessageBubble, { role: "bot", text: "Hampi is lovely.", images: [photo(1)] }));
    assert.match(html, /class="msg bot has-images"/);
    assert.ok(html.indexOf("Hampi is lovely.") < html.indexOf('class="image-row"'));
    assert.match(html, /Photo by Photographer 1 on Pexels/);
  });

  test("a bot reply without images has no row and no has-images class", () => {
    for (const images of [undefined, [], [photo(1, { url: "https://evil.example.com/a.jpg" })]]) {
      const html = renderToStaticMarkup(h(MessageBubble, { role: "bot", text: "Hello", images }));
      assert.doesNotMatch(html, /image-row|has-images/);
      assert.match(html, /<div class="msg bot">/);
    }
  });

  test("user and error messages never show images", () => {
    for (const role of ["user", "error"]) {
      const html = renderToStaticMarkup(h(MessageBubble, { role, text: "hi", images: [photo(1)] }));
      assert.doesNotMatch(html, /image-row|<img/);
    }
  });

  test("the Markdown text is still rendered (and raw HTML in it is still dropped)", () => {
    const html = renderToStaticMarkup(h(MessageBubble, { role: "bot", text: "**Bold** <b>raw</b>", images: [] }));
    assert.match(html, /<strong>Bold<\/strong>/);
    assert.doesNotMatch(html, /<b>/);
  });
});

describe("sendMessage", () => {
  const realFetch = globalThis.fetch;
  const fakeFetch = (body, ok = true, status = 200) => async () => ({ ok, status, json: async () => body });
  test("returns the reply and its images", async () => {
    globalThis.fetch = fakeFetch({ reply: "Hi.", images: [photo(1)] });
    try {
      assert.deepEqual(await chatApi.sendMessage("hello"), { reply: "Hi.", images: [photo(1)], mode: null, places: [], route: [] });
    } finally {
      globalThis.fetch = realFetch;
    }
  });
  test("images is an empty list when the response has none (or an older backend sends none)", async () => {
    for (const body of [{ reply: "Hi." }, { reply: "Hi.", images: null }, { reply: "Hi.", images: "x" }]) {
      globalThis.fetch = fakeFetch(body);
      try {
        assert.deepEqual(await chatApi.sendMessage("hello"), { reply: "Hi.", images: [], mode: null, places: [], route: [] });
      } finally {
        globalThis.fetch = realFetch;
      }
    }
  });
  test("errors still become friendly messages", async () => {
    globalThis.fetch = fakeFetch({}, false, 503);
    try {
      await assert.rejects(() => chatApi.sendMessage("hello"), /busy/);
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});
