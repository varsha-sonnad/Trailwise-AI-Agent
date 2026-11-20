// map-panel.test.mjs: tests for the map panel (components/mapData.js, PlaceMap.jsx, MapPanel.jsx, MessageBubble.jsx,
// MessageList.jsx, api/chat.js). Only Node's built-in test runner is used, no extra library:
//     cd frontend
//     node --test
// The real source files are loaded through Vite's server-side loader, components are rendered to static HTML, the card is
// built with a tiny fake document, and fetch is faked, so no browser, no Leaflet, no backend and no network are needed.
// (Leaflet itself is loaded only inside a browser effect, so nothing here touches it.)

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

let server, data, MapPanel, PlaceMap, buildCard, MessageBubble, chatApi;
const h = React.createElement;

before(async () => {
  server = await createServer({ root, server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  data = await server.ssrLoadModule("/src/components/mapData.js");
  ({ default: MapPanel } = await server.ssrLoadModule("/src/components/MapPanel.jsx"));
  ({ default: PlaceMap, buildCard } = await server.ssrLoadModule("/src/components/PlaceMap.jsx"));
  ({ default: MessageBubble } = await server.ssrLoadModule("/src/components/MessageBubble.jsx"));
  chatApi = await server.ssrLoadModule("/src/api/chat.js");
});
after(async () => {
  await server.close();
});

const source = (...parts) => readFileSync(path.join(root, ...parts), "utf-8").replace(/\r\n/g, "\n");
// A source file without its comments, so a scan only sees code.
const code = (...parts) => source(...parts).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

const VARANASI = { name: "Varanasi", lat: 25.3176, lng: 82.9739 };
const LUCKNOW = { name: "Lucknow", lat: 26.8467, lng: 80.9462 };
function photo(n = 1, over = {}) {
  return {
    url: `https://images.pexels.com/photos/${n}/large.jpeg`, thumb: "", alt: `Ghats ${n}`, photographer: `Photographer ${n}`,
    photographer_url: `https://www.pexels.com/@p-${n}`, pexels_url: `https://www.pexels.com/photo/${n}/`, ...over,
  };
}

// ---------------------------------------------------------------- the tile address
describe("tileUrl (VITE_MAP_TILE_URL)", () => {
  test("a configured https address with {z}/{x}/{y} is used", () => {
    assert.equal(data.tileUrl("https://tiles.example.org/{z}/{x}/{y}.png"), "https://tiles.example.org/{z}/{x}/{y}.png");
    assert.equal(data.tileUrl("  https://{s}.tiles.example.org/{z}/{x}/{y}.png "), "https://{s}.tiles.example.org/{z}/{x}/{y}.png");
    assert.equal(data.tileUrl("http://localhost:8081/{z}/{x}/{y}.png"), "http://localhost:8081/{z}/{x}/{y}.png");
  });
  test("anything else falls back to the OpenStreetMap default", () => {
    for (const bad of [undefined, null, "", "   ", 42, "https://tiles.example.org/tile.png", "https://t.example.org/{z}/{x}.png",
      "http://tiles.example.org/{z}/{x}/{y}.png", "javascript:alert(1)//{z}{x}{y}", "//tiles.example.org/{z}/{x}/{y}.png"]) {
      assert.equal(data.tileUrl(bad), data.DEFAULT_TILE_URL);
    }
    assert.equal(data.DEFAULT_TILE_URL, "https://tile.openstreetmap.org/{z}/{x}/{y}.png");
  });
  test("the OpenStreetMap credit is a fixed text with the copyright link", () => {
    assert.match(data.OSM_ATTRIBUTION, /OpenStreetMap/);
    assert.match(data.OSM_ATTRIBUTION, /openstreetmap\.org\/copyright/);
  });
});

// ---------------------------------------------------------------- places and the route
describe("validPlaces and validRoute", () => {
  test("only named places with real coordinates are kept, without repeats", () => {
    const list = data.validPlaces([
      VARANASI, { ...VARANASI }, { name: "  Lucknow ", lat: 26.8, lng: 80.9 }, { name: "", lat: 1, lng: 1 }, { name: "x", lat: "1", lng: 1 },
      { name: "y", lat: NaN, lng: 1 }, { name: "z", lat: 91, lng: 1 }, { name: "w", lat: 1, lng: 181 }, { name: 5, lat: 1, lng: 1 },
      null, "Goa", { name: "n".repeat(101), lat: 1, lng: 1 },
    ]);
    assert.deepEqual(list.map((p) => p.name), ["Varanasi", "Lucknow"]);
    assert.deepEqual(data.validPlaces(undefined), []);
    assert.deepEqual(data.validPlaces({}), []);
  });
  test("at most 12 places", () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ name: `Place ${i}`, lat: 10 + i / 10, lng: 70 + i / 10 }));
    assert.equal(data.validPlaces(many).length, 12);
  });
  test("a route is a list of [lat, lng] pairs or {lat, lng} objects, bad points are dropped, under two points is no route", () => {
    assert.deepEqual(data.validRoute([[25, 82], [26, 81]]), [[25, 82], [26, 81]]);
    assert.deepEqual(data.validRoute([{ lat: 25, lng: 82 }, { lat: 26, lng: 81 }]), [[25, 82], [26, 81]]);
    assert.deepEqual(data.validRoute([[25, 82], ["x", 1], [200, 1], [26, 81], null, [1]]), [[25, 82], [26, 81]]);
    assert.deepEqual(data.validRoute([[25, 82]]), []);
    assert.deepEqual(data.validRoute([[25, 82], [999, 1]]), []);
    for (const none of [undefined, null, [], "abc", {}]) assert.deepEqual(data.validRoute(none), []);
  });
  test("hasMap is true only with at least one pinnable place", () => {
    assert.equal(data.hasMap([VARANASI]), true);
    for (const none of [undefined, [], [{ name: "x", lat: 999, lng: 0 }], "x"]) assert.equal(data.hasMap(none), false);
  });
});

// ---------------------------------------------------------------- how the map is framed
describe("mapView", () => {
  test("one place and no route: centred on it at zoom 11", () => {
    assert.deepEqual(data.mapView([VARANASI], []), { kind: "single", center: [25.3176, 82.9739], zoom: 11 });
    assert.equal(data.SINGLE_PLACE_ZOOM, 11);
  });
  test("several places: fitted to all pins", () => {
    const view = data.mapView([VARANASI, LUCKNOW], []);
    assert.equal(view.kind, "bounds");
    assert.deepEqual(view.bounds, [[25.3176, 80.9462], [26.8467, 82.9739]]);
    assert.equal(view.maxZoom, data.MAX_FIT_ZOOM);
  });
  test("a route widens the view (even with one place)", () => {
    const view = data.mapView([VARANASI], [[25.3, 82.9], [27.5, 80.0]]);
    assert.equal(view.kind, "bounds");
    assert.deepEqual(view.bounds, [[25.3, 80.0], [27.5, 82.9739]]);
  });
  test("pins on the same spot count as one place, and nothing gives no view", () => {
    assert.equal(data.mapView([{ name: "A", lat: 1, lng: 2 }, { name: "B", lat: 1, lng: 2 }], []).kind, "single");
    assert.equal(data.mapView([], []), null);
    assert.equal(data.mapView([{ name: "bad", lat: 500, lng: 0 }], []), null);
  });
});

// ---------------------------------------------------------------- the card on a pin
describe("cardModel: a photo only from the response's images, never invented", () => {
  test("the first pin gets the first usable photo with its credit", () => {
    const card = data.cardModel(VARANASI, 0, [photo(1), photo(2)]);
    assert.equal(card.name, "Varanasi");
    assert.deepEqual(card.photo, { url: photo(1).url, alt: "Ghats 1", link: "https://www.pexels.com/@p-1", credit: "Photo by Photographer 1 on Pexels" });
  });
  test("other pins have no photo (the photos are of the main place)", () => {
    assert.equal(data.cardModel(LUCKNOW, 1, [photo(1)]).photo, null);
  });
  test("no images, or none usable, means no photo", () => {
    for (const images of [undefined, [], null, [photo(1, { url: "https://evil.example/a.jpg" })], [photo(1, { photographer: " " })], [photo(1, { url: "http://images.pexels.com/a.jpg" })]]) {
      assert.equal(data.cardModel(VARANASI, 0, images).photo, null);
    }
  });
  test("a photo without a photographer page falls back to the photo's Pexels page, else no link", () => {
    assert.equal(data.cardModel(VARANASI, 0, [photo(1, { photographer_url: "" })]).photo.link, "https://www.pexels.com/photo/1/");
    assert.equal(data.cardModel(VARANASI, 0, [photo(1, { photographer_url: "", pexels_url: "" })]).photo.link, "");
  });
});

// A tiny stand-in for the DOM: elements record their tag, text and attributes; innerHTML is forbidden.
function fakeDoc() {
  const make = (tag) => ({
    tag, className: "", textContent: "", attrs: {}, children: [],
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(c) { this.children.push(c); return c; },
    set innerHTML(_) { throw new Error("innerHTML must never be used"); },
  });
  return { createElement: make };
}
const flat = (n) => [n, ...n.children.flatMap(flat)];

describe("buildCard (DOM text only)", () => {
  test("with a photo: image, name and a credit link that opens safely", () => {
    const card = buildCard(data.cardModel(VARANASI, 0, [photo(1)]), fakeDoc());
    const nodes = flat(card);
    const img = nodes.find((n) => n.tag === "img");
    assert.equal(img.attrs.src, photo(1).url);
    assert.equal(img.attrs.referrerpolicy, "no-referrer");
    assert.equal(nodes.find((n) => n.tag === "strong").textContent, "Varanasi");
    const credit = nodes.find((n) => n.className === "pin-card-credit");
    assert.equal(credit.tag, "a");
    assert.equal(credit.textContent, "Photo by Photographer 1 on Pexels");
    assert.equal(credit.attrs.href, "https://www.pexels.com/@p-1");
    assert.equal(credit.attrs.rel, "noopener noreferrer");
    assert.equal(credit.attrs.target, "_blank");
  });
  test("without a photo: just the name, no image and no credit", () => {
    const nodes = flat(buildCard(data.cardModel(VARANASI, 0, []), fakeDoc()));
    assert.equal(nodes.some((n) => n.tag === "img"), false);
    assert.equal(nodes.some((n) => n.className === "pin-card-credit"), false);
    assert.equal(nodes.find((n) => n.tag === "strong").textContent, "Varanasi");
  });
  test("a credit without a link is plain text", () => {
    const credit = flat(buildCard(data.cardModel(VARANASI, 0, [photo(1, { photographer_url: "", pexels_url: "" })]), fakeDoc())).find((n) => n.className === "pin-card-credit");
    assert.equal(credit.tag, "span");
    assert.equal(credit.attrs.href, undefined);
  });
  test("a name that looks like markup stays text (the fake would throw on innerHTML)", () => {
    const card = buildCard({ name: "<img src=x onerror=alert(1)>", photo: null }, fakeDoc());
    assert.equal(flat(card).find((n) => n.tag === "strong").textContent, "<img src=x onerror=alert(1)>");
  });
});

// ---------------------------------------------------------------- opening and closing
describe("when the panel opens and how it closes", () => {
  const bot = (over = {}) => ({ id: 1, role: "bot", text: "Varanasi...", images: [], places: [VARANASI], route: [], ...over });
  test("a new bot reply with places opens the panel, once", () => {
    const handled = new WeakSet();
    const last = bot();
    assert.equal(data.shouldAutoOpen([{ id: 0, role: "bot", places: [] }, { id: 1, role: "user" }, last], handled), last);
    handled.add(last);
    assert.equal(data.shouldAutoOpen([last], handled), null); // already handled: not again (for example after a page switch)
    assert.equal(data.shouldAutoOpen([bot()], handled) !== null, true); // a different message object is new
  });
  test("no auto-open without places, for user and error messages, or for an empty list", () => {
    const handled = new WeakSet();
    assert.equal(data.shouldAutoOpen([bot({ places: [] })], handled), null);
    assert.equal(data.shouldAutoOpen([bot({ places: [{ name: "x", lat: 999, lng: 0 }] })], handled), null);
    assert.equal(data.shouldAutoOpen([bot({ role: "user" })], handled), null);
    assert.equal(data.shouldAutoOpen([bot({ role: "error" })], handled), null);
    assert.equal(data.shouldAutoOpen([], handled), null);
    assert.equal(data.shouldAutoOpen(undefined, handled), null);
  });
  test("Escape closes, other keys do not", () => {
    assert.equal(data.isCloseKey({ key: "Escape" }), true);
    assert.equal(data.isCloseKey({ key: "Esc" }), true);
    for (const key of ["Enter", "a", " ", "Tab"]) assert.equal(data.isCloseKey({ key }), false);
    assert.equal(data.isCloseKey(undefined), false);
  });
  test("a press on the backdrop closes it, a press inside the panel does not", () => {
    let closed = 0;
    const backdrop = {};
    data.closeOnOutsidePress({ target: backdrop, currentTarget: backdrop }, () => closed++);
    assert.equal(closed, 1);
    data.closeOnOutsidePress({ target: {}, currentTarget: backdrop }, () => closed++);
    assert.equal(closed, 1);
  });
});

// ---------------------------------------------------------------- layout helpers
describe("closing, focus and keeping the scroll position", () => {
  test("the panel is removed after the close animation, at once for reduced motion", () => {
    assert.equal(data.closeDelay(false), data.CLOSE_MS);
    assert.equal(data.closeDelay(true), 0);
    assert.ok(data.CLOSE_MS >= 250 && data.CLOSE_MS <= 400);
    assert.match(source("src", "styles.css"), /map-close 0\.26s/); // the same time as CLOSE_MS
    assert.equal(data.CLOSE_MS, 260);
  });
  test("focus moves into the panel when the user opened it, or on a phone; never stolen on a desktop auto-open", () => {
    assert.equal(data.shouldFocusPanel({ openedByUser: true, isPhone: false }), true);
    assert.equal(data.shouldFocusPanel({ openedByUser: false, isPhone: true }), true);
    assert.equal(data.shouldFocusPanel({ openedByUser: false, isPhone: false }), false);
    assert.equal(data.shouldFocusPanel({ openedByUser: true, isPhone: true }), true);
  });
  test("the scroll position is restored at the same distance from the bottom", () => {
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 900 };
    const gap = data.scrollGap(box); // 600 from the bottom
    assert.equal(gap, 600);
    // The column gets narrower, the text wraps and is taller: the same distance from the bottom is kept.
    assert.equal(data.anchoredScrollTop({ scrollHeight: 2600, clientHeight: 500 }, gap), 1500);
    // At the bottom the gap is 0, so the view stays at the bottom.
    assert.equal(data.scrollGap({ scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 }), 0);
    assert.equal(data.anchoredScrollTop({ scrollHeight: 2600, clientHeight: 500 }, 0), 2100);
    // Never negative.
    assert.equal(data.anchoredScrollTop({ scrollHeight: 400, clientHeight: 500 }, 50), 0);
    assert.equal(data.scrollGap({ scrollHeight: 400, clientHeight: 500, scrollTop: 0 }), 0);
  });
  test("MessageList remembers the gap on scroll and restores it when its width changes", () => {
    const src = code("src", "components", "MessageList.jsx");
    assert.match(src, /new ResizeObserver\(\(\) => \{\s*box\.scrollTop = anchoredScrollTop\(box, gap\.current\);/);
    assert.match(src, /onScroll=\{\(event\) => \(gap\.current = scrollGap\(event\.currentTarget\)\)\}/);
    assert.match(src, /observer\.disconnect\(\)/);
  });
  test("MapPanel gives focus back only when it took it, and PlaceMap re-measures when its box changes", () => {
    const panel = code("src", "components", "MapPanel.jsx");
    assert.match(panel, /const before = focusOnOpen \? document\.activeElement : null;/);
    assert.match(panel, /if \(focusOnOpen\) closeButton\.current\?\.focus\(\);/);
    assert.match(panel, /before\.focus\(\)/);
    const map = code("src", "components", "PlaceMap.jsx");
    assert.match(map, /new ResizeObserver\(\(\) => map && map\.invalidateSize\(\)\)/);
    assert.match(map, /observer\.disconnect\(\)/);
  });
});

// ---------------------------------------------------------------- the components
describe("MapPanel and PlaceMap markup", () => {
  test("a dialog with a labelled close button, over a backdrop, holding the map box", () => {
    // Rendered inside the test: the components are only loaded once before() has run.
    const panel = renderToStaticMarkup(h(MapPanel, { places: [VARANASI], route: [], images: [photo(1)], onClose() {} }));
    assert.match(panel, /class="map-backdrop"/);
    assert.match(panel, /<aside class="map-panel" role="dialog" aria-label="Map"/);
    const closing = renderToStaticMarkup(h(MapPanel, { places: [VARANASI], route: [], images: [], closing: true, onClose() {} }));
    assert.match(closing, /<aside class="map-panel closing"/);
    assert.match(panel, /<button[^>]*class="map-close"[^>]*aria-label="Close map"/);
    assert.match(panel, /class="map-canvas"[^>]*role="region"[^>]*aria-label="Map of the places in this reply"/);
  });
  test("PlaceMap renders only the sized box (the map itself is drawn in the browser)", () => {
    assert.match(renderToStaticMarkup(h(PlaceMap, { places: [VARANASI], route: [], images: [] })), /^<div class="map-canvas"/);
  });
});

describe('the "Show map" button on a reply', () => {
  const render = (props) => renderToStaticMarkup(h(MessageBubble, { role: "bot", text: "Hello", images: [], ...props }));
  test("shown when the reply has a place and a way to open the map", () => {
    assert.match(render({ places: [VARANASI], onShowMap() {} }), /<button type="button" class="show-map">Show map<\/button>/);
  });
  test("not shown without places, with only unusable places, without a handler, or on user and error messages", () => {
    assert.doesNotMatch(render({ places: [], onShowMap() {} }), /show-map/);
    assert.doesNotMatch(render({ places: [{ name: "x", lat: 999, lng: 0 }], onShowMap() {} }), /show-map/);
    assert.doesNotMatch(render({ places: [VARANASI] }), /show-map/);
    assert.doesNotMatch(renderToStaticMarkup(h(MessageBubble, { role: "user", text: "hi", places: [VARANASI], onShowMap() {} })), /show-map/);
    assert.doesNotMatch(renderToStaticMarkup(h(MessageBubble, { role: "error", text: "oops", places: [VARANASI], onShowMap() {} })), /show-map/);
  });
});

// ---------------------------------------------------------------- the response and the message
describe("api/chat.js passes places and route on", () => {
  const realFetch = globalThis.fetch;
  const fakeFetch = (body) => async () => ({ ok: true, status: 200, json: async () => body, headers: new Headers() });
  const ask = async (body) => {
    globalThis.fetch = fakeFetch(body);
    try {
      return await chatApi.sendMessage("varanasi", { history: [], lastMode: null, lastPlace: null });
    } finally {
      globalThis.fetch = realFetch;
    }
  };
  test("places and route come through, and missing fields become empty lists", async () => {
    const full = await ask({ reply: "Hi", images: [photo(1)], mode: "explore", places: [VARANASI], route: [[25, 82], [26, 81]] });
    assert.deepEqual(full.places, [VARANASI]);
    assert.deepEqual(full.route, [[25, 82], [26, 81]]);
    const bare = await ask({ reply: "Hi" });
    assert.deepEqual([bare.places, bare.route], [[], []]);
    const odd = await ask({ reply: "Hi", places: "x", route: { a: 1 } });
    assert.deepEqual([odd.places, odd.route], [[], []]);
  });
});

describe("the message keeps places and route", () => {
  test("useChat stores them on the bot message", () => {
    const src = code("src", "hooks", "useChat.js");
    assert.match(src, /const \{ reply, images, mode, places, route \} = await sendMessage/);
    assert.match(src, /addMessage\("bot", reply, images, places, route\)/);
    assert.match(src, /\{ id: nextId\.current\+\+, role, text, images, places, route \}/);
  });
  test("ChatSplit owns the panel: auto-open once per message, the panel as a sibling of the chat, shrinking away on close", () => {
    const src = code("src", "components", "ChatSplit.jsx");
    assert.match(src, /shouldAutoOpen\(messages, autoOpened\)/);
    assert.match(src, /new WeakSet\(\)/);
    assert.match(src, /<div className="chat-col">\{children\}<\/div>/); // the chat is always rendered in the same place
    assert.match(src, /<MapPanel\s+places=\{openMessage\.places\}\s+route=\{openMessage\.route\}\s+images=\{openMessage\.images\}/);
    assert.match(src, /messages\.includes\(open\.message\)/); // after "New chat" the panel is gone
    assert.match(src, /closeDelay\(matches\("\(prefers-reduced-motion: reduce\)"\)\)/);
    assert.match(src, /if \(timer\.current\) return; \/\/ already closing/);
    assert.match(src, /clearTimeout\(timer\.current\);[^\n]*\n\s*timer\.current = null;/); // reopening while closing resets the timer
    assert.match(src, /<MapContext\.Provider value=\{showMap\}>/);
  });
  test("MessageList no longer draws the panel; its Show map button uses the context", () => {
    const src = code("src", "components", "MessageList.jsx");
    assert.doesNotMatch(src, /MapPanel/);
    assert.match(src, /useContext\(MapContext\)/);
    assert.match(src, /onShowMap=\{showMap && !mapOpen \? \(\) => showMap\(m, true\) : undefined\}/);
    assert.match(src, /useContext\(MapOpenContext\)/); // the button hides while the panel is open
  });
  test("both chat pages put the chat in a ChatSplit", () => {
    const home = code("src", "pages", "AppHome.jsx");
    assert.match(home, /<ChatSplit messages=\{messages\}>[\s\S]*<MessageList messages=\{messages\} pending=\{pending\} \/>[\s\S]*<ChatBar[\s\S]*<\/ChatSplit>/);
    const chat = code("src", "pages", "Chat.jsx");
    assert.match(chat, /<ChatSplit messages=\{messages\}>\s*<div className="chat-page">/);
  });
});

// ---------------------------------------------------------------- how the map is built (source checks)
describe("PlaceMap source rules", () => {
  const src = code("src", "components", "PlaceMap.jsx");
  test("plain Leaflet, loaded only inside the effect", () => {
    assert.match(src, /await import\("leaflet"\)/);
    assert.doesNotMatch(src, /^import .* from "leaflet"/m);
    assert.doesNotMatch(src, /react-leaflet/);
  });
  test("leaflet.css and the three marker images are imported explicitly", () => {
    for (const file of ["leaflet/dist/leaflet.css", "leaflet/dist/images/marker-icon.png", "leaflet/dist/images/marker-icon-2x.png", "leaflet/dist/images/marker-shadow.png"]) {
      assert.ok(src.includes(`"${file}"`), file);
    }
    assert.match(src, /L\.icon\(\{\s*iconUrl: markerIcon, iconRetinaUrl: markerIcon2x, shadowUrl: markerShadow/);
  });
  test("the tile address comes from VITE_MAP_TILE_URL and the OpenStreetMap credit is set", () => {
    assert.match(src, /tileUrl\(import\.meta\.env\.VITE_MAP_TILE_URL\)/);
    assert.match(src, /attribution: OSM_ATTRIBUTION/);
    assert.doesNotMatch(src, /https?:\/\//); // no address written in this file: tiles come from the setting or the default in mapData.js
  });
  test("a pin for every place, the route line, fitted view, hover and tap cards", () => {
    assert.match(src, /pins\.forEach\(/);
    assert.match(src, /L\.marker\(\[place\.lat, place\.lng\]/);
    assert.match(src, /L\.polyline\(line/);
    assert.match(src, /map\.setView\(view\.center, view\.zoom\)/);
    assert.match(src, /map\.fitBounds\(view\.bounds/);
    assert.match(src, /bindPopup\(buildCard\(cardModel\(place, index, images\)\)/);
    assert.match(src, /marker\.on\("mouseover", \(\) => marker\.openPopup\(\)\)/);
  });
  test("no markup is ever built from reply text", () => {
    for (const name of ["PlaceMap.jsx", "MapPanel.jsx", "MessageList.jsx", "MessageBubble.jsx"]) {
      const text = code("src", "components", name);
      assert.doesNotMatch(text, /innerHTML|dangerouslySetInnerHTML|insertAdjacentHTML|document\.write/, name);
    }
  });
  test("the map is cleaned up when the panel closes", () => {
    assert.match(src, /map\.remove\(\)/);
    assert.match(src, /cancelled = true/);
  });
  test("data.js never writes a tile or photo address other than the OpenStreetMap ones", () => {
    const urls = code("src", "components", "mapData.js").match(/https?:\/\/[^\s"'`<>)]+/g) || [];
    assert.ok(urls.every((u) => u.includes("openstreetmap.org") || u.startsWith("http://(") ), urls.join(", "));
  });
});

describe("the styles and settings", () => {
  const css = source("src", "styles.css");
  test("desktop: two columns, the chat shrinks to make room (about 58%) and the map takes about 42%", () => {
    assert.match(css, /\.chat-split \{[^}]*display: flex/);
    assert.match(css, /\.chat-col \{[^}]*flex: 1 1 0[^}]*min-width: 0/); // the flexible column; min-width 0 lets it shrink
    assert.match(css, /\.map-panel \{[^}]*position: relative[^}]*flex: 0 0 42%/);
    const share = Number(css.match(/\.map-panel \{[^}]*flex: 0 0 (\d+)%/)[1]);
    assert.ok(share >= 40 && share <= 45, `map share ${share}%`);
    assert.ok(100 - share >= 55 && 100 - share <= 60);
    assert.match(css, /\.map-backdrop \{ display: contents; \}/); // no overlay on a desktop: the chat stays usable
  });
  test("a smooth width change both ways", () => {
    assert.match(css, /@keyframes map-open \{ from \{ flex-basis: 0; opacity: 0; \} to \{ flex-basis: 42%; opacity: 1; \} \}/);
    assert.match(css, /@keyframes map-close \{ from \{ flex-basis: 42%; opacity: 1; \} to \{ flex-basis: 0; opacity: 0; \} \}/);
    assert.match(css, /\.map-panel \{[^}]*animation: map-open/);
    assert.match(css, /\.map-panel\.closing \{ animation: map-close [^}]*forwards/);
    // Reduced motion switches both off, and it comes after the phone rules (which also set an animation).
    const reduced = css.lastIndexOf("@media (prefers-reduced-motion: reduce) { .map-panel, .map-panel.closing { animation: none; } }");
    assert.ok(reduced > css.indexOf("@keyframes map-slide-out"));
    assert.ok(css.indexOf("@keyframes map-close") < css.indexOf("@media (max-width: 760px) {\n  .map-backdrop"));
  });
  test("the map box keeps a height (fixed 520px, never below 320px, filling the column) and Leaflet gets it", () => {
    assert.match(css, /\.map-canvas \{ width: 100%; height: 520px; flex: 1 1 auto; min-height: 320px; \}/);
  });
  test("phones (760px and narrower): a full-screen overlay with its backdrop, slide in and out", () => {
    const phone = css.slice(css.indexOf("@media (max-width: 760px) {\n  .map-backdrop"));
    assert.match(phone, /\.map-backdrop \{ display: block; position: fixed; inset: 0; z-index: 1100/);
    assert.match(phone, /\.map-panel \{[^}]*position: absolute[^}]*flex: none; width: 100vw[^}]*animation: map-slide-in/);
    assert.match(phone, /\.map-panel\.closing \{ animation: map-slide-out/);
    assert.match(phone, /\.map-canvas \{ height: calc\(100dvh - 58px\); flex: none; \}/);
    assert.match(css, /@keyframes map-slide-in \{ from \{ transform: translateX\(100%\)/);
    assert.equal(data.PHONE_MAX_WIDTH, 760); // the same width the JavaScript uses to decide on focus
  });
  test("the panel component has the three ways to close", () => {
    const src = code("src", "components", "MapPanel.jsx");
    assert.match(src, /document\.addEventListener\("keydown"/);
    assert.match(src, /document\.removeEventListener\("keydown"/);
    assert.match(src, /onPointerDown=\{\(event\) => closeOnOutsidePress\(event, onClose\)\}/);
    assert.match(src, /onClick=\{onClose\}/);
  });
  test("VITE_MAP_TILE_URL is in the frontend .env.example, the Dockerfile and docker-compose.yml", () => {
    assert.match(source(".env.example"), /^VITE_MAP_TILE_URL=https:\/\/tile\.openstreetmap\.org\/\{z\}\/\{x\}\/\{y\}\.png$/m);
    assert.match(source("Dockerfile"), /ARG VITE_MAP_TILE_URL=/);
    assert.match(source("Dockerfile"), /ENV VITE_MAP_TILE_URL=\$VITE_MAP_TILE_URL/);
    assert.match(readFileSync(path.join(root, "..", "docker-compose.yml"), "utf-8"), /VITE_MAP_TILE_URL:/);
  });
  test("leaflet is the only new dependency", () => {
    const pkg = JSON.parse(source("package.json"));
    assert.equal(pkg.dependencies.leaflet, "1.9.4");
    assert.deepEqual(Object.keys(pkg.dependencies).sort(), ["keycloak-js", "leaflet", "react", "react-dom", "react-markdown"]);
  });
});
