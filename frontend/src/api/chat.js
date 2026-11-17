// chat.js: the call to the backend's POST /chat endpoint.
// The backend runs the Gemini agent and returns {reply}. Failures become an ApiError with a friendly message.

import { postJson } from "./client";

// Longest message the backend accepts (matches max_message_chars in backend/app/config.py).
export const MAX_MESSAGE_CHARS = 1000;

// Turn a failed HTTP status into a short message the user can understand.
function describeChatError(status) {
  if (status === 422) return `That message was not accepted. Keep it between 1 and ${MAX_MESSAGE_CHARS} characters.`;
  // 503 = the AI service is overloaded (temporary); 502 = it failed for another reason.
  if (status === 503) return "The assistant is busy right now (high demand). Please try again in a minute.";
  if (status === 502) return "The assistant is unavailable right now. Please try again.";
  return `Something went wrong (error ${status}). Please try again.`;
}

// Send one user message and return the assistant's reply text.
export async function sendMessage(message) {
  const data = await postJson("/chat", { message }, describeChatError);
  return data.reply;
}
