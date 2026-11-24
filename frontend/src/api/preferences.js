// preferences.js: everything about saved travel preferences on the browser side: the allowed values (they match
// backend/app/models/preferences.py exactly), small pure helpers, the guest copy kept in the browser (localStorage),
// and the calls to GET and PUT /me/preferences for signed-in users. The backend checks every value again.

import { getJson, putJson } from "./client";

// The choices shown on the page, in order: { value (what is stored and sent), label (what the user reads) }.
export const TRAVEL_MODES = [
  { value: "train", label: "Train" },
  { value: "bus", label: "Bus" },
];
export const BUDGETS = [
  { value: "budget", label: "Budget" },
  { value: "comfort", label: "Comfort" },
  { value: "premium", label: "Premium" },
];
export const INTERESTS = ["beach", "mountains", "heritage", "spiritual", "adventure", "nature", "food", "culture", "relaxing", "wildlife"].map(
  (value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }),
);
// lang is the language of the label itself, so screen readers pronounce it correctly.
export const LANGUAGES = [
  { value: "en", label: "English", lang: "en" },
  { value: "hi", label: "हिन्दी", lang: "hi" },
  { value: "kn", label: "ಕನ್ನಡ", lang: "kn" },
];

// What "nothing chosen" looks like (the same as the backend's empty defaults).
export const EMPTY_PREFERENCES = Object.freeze({ travel_mode: [], budget: null, interests: [], reply_language: null });

// The key of the guest copy in localStorage.
export const STORAGE_KEY = "trailwise_preferences";

// The values of a list of options, as a set.
const valuesOf = (options) => options.map((o) => o.value);

// Keep only known values, once each, in the order of `allowed`. Anything that is not a list gives [].
function cleanList(list, allowed) {
  return Array.isArray(list) ? allowed.filter((v) => list.includes(v)) : [];
}

// Turn anything (a stored string's parsed JSON, an API reply) into a valid preferences object:
// unknown values and fields are dropped, so a damaged or edited copy can never be sent on.
export function sanitize(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  return {
    travel_mode: cleanList(r.travel_mode, valuesOf(TRAVEL_MODES)),
    budget: valuesOf(BUDGETS).includes(r.budget) ? r.budget : null,
    interests: cleanList(r.interests, valuesOf(INTERESTS)),
    reply_language: valuesOf(LANGUAGES).includes(r.reply_language) ? r.reply_language : null,
  };
}

// True when nothing is chosen.
export function isEmpty(prefs) {
  const p = sanitize(prefs);
  return p.travel_mode.length === 0 && p.budget === null && p.interests.length === 0 && p.reply_language === null;
}

// True when two preferences mean the same thing (list order does not matter).
export function samePreferences(a, b) {
  return JSON.stringify(sanitize(a)) === JSON.stringify(sanitize(b));
}

// After login: what to do with the browser copy. "copy" = the account has nothing saved and the browser has choices,
// so send the browser's to the account; "keep-account" = the account already has some, so it wins; "none" = nothing to copy.
export function decideAfterLogin(account, browser) {
  if (!isEmpty(account)) return "keep-account";
  return isEmpty(browser) ? "none" : "copy";
}

// The guest copy from the browser, or the empty preferences when there is none or it cannot be read.
export function loadLocal() {
  try {
    const text = localStorage.getItem(STORAGE_KEY);
    return text ? sanitize(JSON.parse(text)) : { ...EMPTY_PREFERENCES };
  } catch {
    return { ...EMPTY_PREFERENCES }; // storage blocked or the text is damaged
  }
}

// Keep the guest copy in the browser. Returns false when the browser would not store it.
export function saveLocal(prefs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sanitize(prefs)));
    return true;
  } catch {
    return false;
  }
}

// Remove the guest copy from the browser.
export function clearLocal() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do: storage is blocked.
  }
}

// The part of the saved preferences that goes along with a chat message: only the reply language, the budget style and the
// travel mode, and only what is set (interests are not sent). Returns null when none is set, so the request is unchanged then.
// The backend validates these again and builds one short fixed line from them (it never receives free text).
export function chatPreferences(prefs) {
  const p = sanitize(prefs);
  const out = {};
  if (p.reply_language) out.reply_language = p.reply_language;
  if (p.budget) out.budget = p.budget;
  if (p.travel_mode.length) out.travel_mode = p.travel_mode;
  return Object.keys(out).length ? out : null;
}

// Turn a failed HTTP status into a short message the user can understand.
function describePreferencesError(status) {
  if (status === 401) return "Your sign-in has expired. Please log in again.";
  if (status === 422) return "Those choices were not accepted. Please try again.";
  if (status === 503) return "Preferences are not available right now. Please try again in a minute.";
  return `Something went wrong (error ${status}). Please try again.`;
}

// The signed-in user's saved preferences (empty ones when nothing is saved). The user is taken from the token by the backend.
export async function fetchPreferences() {
  return sanitize(await getJson("/me/preferences", describePreferencesError));
}

// Save the signed-in user's preferences (the whole set) and return what the backend stored. No user id is sent.
export async function savePreferences(prefs) {
  return sanitize(await putJson("/me/preferences", sanitize(prefs), describePreferencesError));
}
