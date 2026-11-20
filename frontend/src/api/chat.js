// chat.js: the call to the backend's POST /chat endpoint.
// The backend runs the OpenAI agent and returns {reply, images, mode, places}. Failures become an ApiError with a friendly message.

import { postJson } from "./client";

// Longest message the backend accepts (matches max_message_chars in backend/app/config.py).
export const MAX_MESSAGE_CHARS = 1000;

// Turn a failed HTTP status into a short message the user can understand.
function describeChatError(status) {
  // 401 = the sign-in is no longer valid (for example it expired).
  if (status === 401) return "Your sign-in has expired. Please log in again.";
  if (status === 422) return `That message was not accepted. Keep it between 1 and ${MAX_MESSAGE_CHARS} characters.`;
  // 503 = the AI service is overloaded (temporary); 502 = it failed for another reason.
  if (status === 503) return "The assistant is busy right now (high demand). Please try again in a minute.";
  if (status === 502) return "The assistant is unavailable right now. Please try again.";
  return `Something went wrong (error ${status}). Please try again.`;
}

// Send one user message with the chat's memory and return the assistant's reply:
// { reply: the text, images: the photos to show under it, mode: the mode it was written in, places: the places it geocoded,
// route: the route line for the map when there is one }.
// memory = { history, lastMode, lastPlace } (see hooks/chatMemory.js); the backend stores none of it, so it is sent every time.
// images and places are always lists (empty when the reply has none, or when an older backend does not send the field).
export async function sendMessage(message, memory = {}) {
  const body = {
    message,
    history: Array.isArray(memory.history) ? memory.history : [],
    last_mode: memory.lastMode ?? null,
    last_place: memory.lastPlace ?? null,
  };
  const data = await postJson("/chat", body, describeChatError);
  return {
    reply: data.reply,
    images: Array.isArray(data.images) ? data.images : [],
    mode: typeof data.mode === "string" ? data.mode : null,
    places: Array.isArray(data.places) ? data.places : [],
    // The route line for the map, when a response has one ([[lat, lng], ...]). The backend does not send one yet; the map draws
    // it only when it is there (and valid), and never makes one up.
    route: Array.isArray(data.route) ? data.route : [],
  };
}
