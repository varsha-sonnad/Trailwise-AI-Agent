// chatMemory.js: the pure helpers behind the chat's per-session memory. The memory lives in React state only (see useChat.js):
// nothing is written to localStorage, sessionStorage, cookies or the server, so "New chat" and a page refresh clear it.
// With every message the browser sends { message, history, last_mode, last_place }:
//   history     the earlier user messages and assistant reply texts, nothing else (no errors, no photos, no welcome line);
//   last_mode   the mode the previous reply was written in (explore, route, itinerary or email);
//   last_place  the last place geocoded for the user: { name, lat, lng }.
// The limits below match what the backend accepts (backend/app/models/chat.py); anything longer would be refused with 422.

// The first message shown when the chat opens. It is the app's own welcome line, not a model reply, so it is not sent back.
export const GREETING = "Hi! Tell me where you want to go and I will help you plan it.";

// At most this many earlier messages, each at most this long, and all of them together at most this long (characters).
export const MAX_HISTORY_MESSAGES = 10;
export const MAX_HISTORY_MESSAGE_CHARS = 2000;
export const MAX_HISTORY_TOTAL_CHARS = 12000;

// The fixed list of modes the backend knows.
export const MODES = ["explore", "route", "itinerary", "email"];

// The conversation when a chat starts or after "New chat": just the welcome line.
export function initialMessages() {
  return [{ id: 0, role: "bot", text: GREETING, images: [], welcome: true }];
}

// What is remembered besides the messages: the mode of the last reply and the last place (both null at the start).
export function emptyMemory() {
  return { lastMode: null, lastPlace: null };
}

// Control characters (other than newline, carriage return and tab) are not accepted by the backend: turn them into spaces.
function plain(text) {
  return text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, " ");
}

// The history to send: the user messages and the assistant's reply texts so far, in order, ready for the request.
// Error messages, the welcome line and blank texts are left out. Each text is cut to the limit; only the newest
// messages that fit the message-count and total-size limits are kept.
export function buildHistory(messages) {
  const items = [];
  for (const m of Array.isArray(messages) ? messages : []) {
    if (!m || m.welcome || (m.role !== "user" && m.role !== "bot") || typeof m.text !== "string") continue;
    const content = plain(m.text).slice(0, MAX_HISTORY_MESSAGE_CHARS);
    if (!content.trim()) continue;
    items.push({ role: m.role === "bot" ? "assistant" : "user", content });
  }
  const recent = items.slice(-MAX_HISTORY_MESSAGES);
  let total = recent.reduce((sum, m) => sum + m.content.length, 0);
  while (recent.length > 0 && total > MAX_HISTORY_TOTAL_CHARS) total -= recent.shift().content.length; // drop the oldest first
  return recent;
}

// What goes along with the next message: the history of the messages so far, the last mode and the last place.
// messages: the conversation before the new message; memory: { lastMode, lastPlace } (see emptyMemory).
export function memoryFor(messages, memory) {
  return { history: buildHistory(messages), lastMode: memory.lastMode, lastPlace: memory.lastPlace };
}

// True for a place the backend will accept: a plain name of up to 100 characters and real coordinates in range.
export function isValidPlace(place) {
  if (!place || typeof place !== "object") return false;
  const { name, lat, lng } = place;
  const real = (n) => typeof n === "number" && Number.isFinite(n);
  return (
    typeof name === "string" && name.trim().length >= 1 && name.length <= 100 && real(lat) && real(lng) &&
    lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180
  );
}

// The memory after a reply: the reply's mode (if it is a known one) and the first place it geocoded (if any). When the
// reply has no mode or no place, what was remembered before is kept, so a follow-up such as "and the weather?" still works.
export function nextMemory(memory, response) {
  const places = Array.isArray(response?.places) ? response.places : [];
  const first = places.find(isValidPlace);
  return {
    lastMode: MODES.includes(response?.mode) ? response.mode : memory.lastMode,
    lastPlace: first ? { name: first.name.trim(), lat: first.lat, lng: first.lng } : memory.lastPlace,
  };
}
