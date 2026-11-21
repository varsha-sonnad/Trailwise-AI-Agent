// mapData.js: the pure (no React, no Leaflet, no DOM) helpers behind the map panel, so they can be tested with Node alone.
// It decides which places, route points and photo may be drawn, how the map is framed, which tile address is used, and when
// the panel opens by itself. Nothing is invented: places and the route come from the response's "places" and "route", and the
// photo only from the response's "images" (a Pexels picture with a photographer's name, checked by ImageRow's usableImages).

import { usableImages } from "./ImageRow";

// The tile address used when VITE_MAP_TILE_URL is not set (or is not a usable address): OpenStreetMap's public tile server,
// meant for light use. The required credit is always shown on the map (see OSM_ATTRIBUTION).
export const DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
// The map credit OpenStreetMap asks for. A constant of ours (never data from a reply), so it is safe as the layer's attribution.
export const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors';
// One place is shown at city level; several are fitted into view (but never zoomed in beyond MAX_FIT_ZOOM).
export const SINGLE_PLACE_ZOOM = 11;
export const MAX_FIT_ZOOM = 13;
// Limits, in line with what the backend sends (it sends at most 12 places).
export const MAX_PLACES = 12;
export const MAX_ROUTE_POINTS = 2000;

// The tile address to use: the configured one if it is an https address (or http on localhost, for a local tile server) that
// has the {z}, {x} and {y} placeholders; otherwise the default. Whatever is configured, the OpenStreetMap credit stays.
export function tileUrl(raw) {
  if (typeof raw !== "string") return DEFAULT_TILE_URL;
  const value = raw.trim();
  const allowed = value.startsWith("https://") || /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(value);
  const hasPlaceholders = ["{z}", "{x}", "{y}"].every((p) => value.includes(p));
  return allowed && hasPlaceholders && value.length <= 500 ? value : DEFAULT_TILE_URL;
}

// A real coordinate pair: finite numbers inside the valid ranges (strings and NaN do not count).
function isCoordinate(lat, lng) {
  return typeof lat === "number" && typeof lng === "number" && Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
}

// The places that may get a pin: a name (1 to 100 characters) and real coordinates, no repeats, at most MAX_PLACES.
export function validPlaces(places) {
  if (!Array.isArray(places)) return [];
  const seen = new Set();
  const out = [];
  for (const place of places) {
    if (!place || typeof place !== "object") continue;
    const name = typeof place.name === "string" ? place.name.trim() : "";
    if (!name || name.length > 100 || !isCoordinate(place.lat, place.lng)) continue;
    const key = `${name.toLowerCase()}|${place.lat}|${place.lng}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ name, lat: place.lat, lng: place.lng });
    if (out.length >= MAX_PLACES) break;
  }
  return out;
}

// The route line: a list of [lat, lng] pairs (or {lat, lng} objects). Invalid points are dropped; fewer than two good points
// means no line at all. Returned as [[lat, lng], ...], at most MAX_ROUTE_POINTS.
export function validRoute(route) {
  if (!Array.isArray(route)) return [];
  const points = [];
  for (const point of route) {
    const [lat, lng] = Array.isArray(point) ? point : point && typeof point === "object" ? [point.lat, point.lng] : [];
    if (isCoordinate(lat, lng)) points.push([lat, lng]);
    if (points.length >= MAX_ROUTE_POINTS) break;
  }
  return points.length >= 2 ? points : [];
}

// True when a reply has at least one place that can be pinned: only then the "Show map" button and the panel exist.
export function hasMap(places) {
  return validPlaces(places).length > 0;
}

// How to frame the map. One place and no route: centre on it at zoom 11. Anything else: fit the bounds of every pin and route
// point. Several pins that sit on exactly the same spot count as one place. null when there is nothing to show.
export function mapView(places, route) {
  const pins = validPlaces(places);
  const line = validRoute(route);
  const all = [...pins.map((p) => [p.lat, p.lng]), ...line];
  if (all.length === 0) return null;
  const lats = all.map((p) => p[0]);
  const lngs = all.map((p) => p[1]);
  const south = Math.min(...lats), north = Math.max(...lats), west = Math.min(...lngs), east = Math.max(...lngs);
  if ((pins.length === 1 && line.length === 0) || (south === north && west === east)) {
    return { kind: "single", center: [south, west], zoom: SINGLE_PLACE_ZOOM };
  }
  return { kind: "bounds", bounds: [[south, west], [north, east]], maxZoom: MAX_FIT_ZOOM };
}

// What one pin's card shows: the place name and, at most, one photo. The reply's photos belong to the main place the reply is
// about (the first pin), so only the first pin may carry one, taken from the response's "images" and nowhere else. A photo
// needs a Pexels address and a photographer (usableImages checks both). With no usable photo there is none: nothing is invented.
export function cardModel(place, index, images) {
  const photo = index === 0 ? usableImages(images)[0] : undefined;
  return {
    name: place.name,
    photo: photo ? { url: photo.url, alt: photo.alt, link: photo.link, credit: `Photo by ${photo.photographer} on Pexels` } : null,
  };
}

// Which message the map panel should open for by itself: the last message, when it is a bot reply with pinnable places and has
// not been handled before. `handled` is a WeakSet of message objects, so a message opens the panel once (not again after a
// page switch) and a new chat starts fresh. Returns the message or null.
export function shouldAutoOpen(messages, handled) {
  const last = Array.isArray(messages) ? messages[messages.length - 1] : undefined;
  if (!last || last.role !== "bot" || handled.has(last) || !hasMap(last.places)) return null;
  return last;
}

// How long the panel takes to close (its width shrinks back, then it is removed): matches map-close in styles.css.
export const CLOSE_MS = 260;
// The phone layout (the map is a full-screen overlay) applies up to this width; wider screens get two columns.
export const PHONE_MAX_WIDTH = 760;

// The wait before the panel is removed after it was asked to close: none when the user prefers reduced motion.
export function closeDelay(reducedMotion) {
  return reducedMotion ? 0 : CLOSE_MS;
}

// Whether the panel takes keyboard focus when it opens. Opened by the "Show map" button: yes. Opened by itself for a new
// reply: only on a phone (it covers the screen there). On desktop it must not steal focus from the message box.
export function shouldFocusPanel({ openedByUser, isPhone }) {
  return Boolean(openedByUser || isPhone);
}

// Keeping the chat's scroll position while its width changes (the text re-wraps and gets taller or shorter): remember how far
// the view is from the bottom, and put it back at the same distance. At the bottom the gap is 0, so it stays at the bottom.
export function scrollGap(box) {
  return Math.max(0, box.scrollHeight - box.clientHeight - box.scrollTop);
}
export function anchoredScrollTop(box, gap) {
  return Math.max(0, box.scrollHeight - box.clientHeight - gap);
}

// A press on the backdrop itself (not on the panel on top of it) is a click outside the panel: it closes it.
export function closeOnOutsidePress(event, onClose) {
  if (event && event.target === event.currentTarget) onClose();
}

// True for the key that closes the panel.
export function isCloseKey(event) {
  return event?.key === "Escape" || event?.key === "Esc";
}
