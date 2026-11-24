// trips.js: the calls for saved trips: save a plan (POST /trips) and email a saved trip (POST /trips/{id}/email).
// Both need a signed-in user: the backend answers 401 to a guest. The owner and the recipient are never sent: the server takes
// them from the login token. Errors are ApiError (see client.js) with a message the user can read.

import { ApiError, postJson } from "./client";
import { tripBody } from "../components/replyActions.js";

// A trip id as the backend makes them (32 hex characters): checked before it goes into a URL.
const TRIP_ID = /^[0-9a-f]{32}$/;

// A short message for each status of POST /trips.
function describeSaveError(status) {
  if (status === 401) return "Please log in to save your trip.";
  if (status === 409) return "You have reached the limit of 100 saved trips. Delete one to save another.";
  if (status === 422) return "This plan could not be saved.";
  if (status === 503) return "Saving is not available right now. Please try again.";
  return "Could not save the trip. Please try again.";
}

// A short message for each status of POST /trips/{id}/email.
function describeEmailError(status) {
  if (status === 401) return "Please log in to email your plan.";
  if (status === 403) return "Your email address is not verified, so the plan cannot be emailed.";
  if (status === 404) return "That trip was not found. Save the plan again.";
  if (status === 429) return "You can email at most 5 plans per hour. Please try again later.";
  if (status === 502) return "The email could not be sent right now. Please try again.";
  if (status === 503) return "Emailing is not available right now. Please try again later.";
  return "Could not email the plan. Please try again.";
}

// Save the plan shown in this bot message. Resolves to the new trip's id.
export async function saveTrip(message) {
  const data = await postJson("/trips", tripBody(message), describeSaveError);
  if (!data || typeof data.id !== "string" || !TRIP_ID.test(data.id)) {
    throw new ApiError(0, "Could not save the trip. Please try again.");
  }
  return data.id;
}

// Email a saved trip to the address in the user's login token. Resolves only when the server says sent: true.
export async function emailTrip(id) {
  if (typeof id !== "string" || !TRIP_ID.test(id)) throw new ApiError(0, "Save the plan first.");
  const data = await postJson(`/trips/${id}/email`, {}, describeEmailError);
  if (!data || data.sent !== true) throw new ApiError(0, "The email could not be confirmed as sent.");
  return true;
}
