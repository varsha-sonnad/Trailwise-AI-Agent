// replyActions.js: the pure rules (no React, no DOM, no network) for the buttons under a bot reply, so they can be tested with Node alone.
//
// replyActions(message) decides from the RESPONSE FIELDS only (role, mode, places, legs), never from the model's wording:
//   "plan"     a plan reply: "Save trip" and "Email plan" are shown. That is mode itinerary with at least one valid place, or
//              mode route with at least one valid leg (the map lines).
//   "explore"  an explore reply about exactly one place: one "Plan a trip to <place>" button.
//   "none"     everything else: greetings, clarifying questions, short explore answers, error replies (they have no places or
//              legs, or are not bot messages at all).
// Known edge: a fixed failure reply that still carries places from an earlier lookup looks like a plan to these rules. The
// backend will get a field (kind: "plan") for that later; until then this is accepted.
//
// tripTitle / tripBody build what POST /trips needs from the same fields (the server's limits are repeated here so a long reply
// is cut instead of refused).

import { validLegs, validPlaces } from "./mapData";

// Limits of POST /trips (the server enforces them too).
export const MAX_TRIP_TITLE_CHARS = 100;
export const MAX_TRIP_REPLY_CHARS = 8000;
export const MAX_TRIP_PLACES = 12;

// What to show under this message: { kind: "plan" } | { kind: "explore", place } | { kind: "none" }.
export function replyActions(message) {
  if (!message || message.role !== "bot") return { kind: "none" };
  const places = validPlaces(message.places);
  if (message.mode === "itinerary" && places.length > 0) return { kind: "plan" };
  if (message.mode === "route" && validLegs(message.legs).length > 0) return { kind: "plan" };
  if (message.mode === "explore" && places.length === 1) return { kind: "explore", place: places[0].name };
  return { kind: "none" };
}

// The message the explore button sends as a new message.
export function planMessage(place) {
  return `Plan a trip to ${place}`;
}

// The saved trip's title, from the reply's places: "Trip to <first place>" for an itinerary, "<first> to <second>" for a route
// (the first two places are the start and the destination). Never longer than the server allows.
export function tripTitle(message) {
  const places = validPlaces(message?.places);
  let title = "Trip plan";
  if (message?.mode === "route" && places.length >= 2) title = `${places[0].name} to ${places[1].name}`;
  else if (places.length >= 1) title = `Trip to ${places[0].name}`;
  return title.slice(0, MAX_TRIP_TITLE_CHARS);
}

// The body of POST /trips for this reply. The owner is never part of it (the server takes it from the login token).
export function tripBody(message) {
  const text = typeof message?.text === "string" ? message.text : "";
  return {
    title: tripTitle(message),
    places: validPlaces(message?.places).slice(0, MAX_TRIP_PLACES),
    summary: "",
    reply: text.slice(0, MAX_TRIP_REPLY_CHARS),
  };
}

// "Email plan": a trip must be saved before it can be emailed, so save first when this reply has no saved trip yet, then email.
// api = { saveTrip(message) -> id, emailTrip(id) }; onSaved(id) is called as soon as the save worked, so the caller keeps the id
// (and shows "Saved") even when the email step then fails. Returns the trip id.
export async function emailPlan(message, tripId, api, onSaved) {
  let id = tripId;
  if (!id) {
    id = await api.saveTrip(message);
    onSaved(id);
  }
  await api.emailTrip(id);
  return id;
}
