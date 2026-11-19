// session.js: everything about "who is using the app" on the browser side.
// Registered users sign in through Keycloak (keycloak-js, Authorization Code flow with PKCE). The access token
// lives only in memory inside the keycloak object: it is never written to localStorage or sessionStorage.
// Guests get a random session id kept in sessionStorage (it is not a credential, just a label for the tab).
// This file also tells the API client which headers to add to every backend call.

import Keycloak from "keycloak-js";
import { setAuthHeaderProvider } from "../api/client";

// Where Keycloak is, which realm and which client (all non-secret; set in frontend/.env.local, see .env.example).
const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL || "http://localhost:8080",
  realm: import.meta.env.VITE_KEYCLOAK_REALM || "trailwise",
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID || "trailwise-web",
});

// keycloak.init() may only run once; React's dev mode runs effects twice, so the promise is shared.
let initPromise = null;

// Start Keycloak and quietly check whether the user already has a session. Resolves to the signed-in
// user ({ name, email }) or null. If Keycloak is not reachable the site still works, as a guest.
export function initAuth() {
  if (!initPromise) {
    initPromise = keycloak
      .init({ onLoad: "check-sso", pkceMethod: "S256", checkLoginIframe: false })
      .then(() => currentUser())
      .catch(() => null);
  }
  return initPromise;
}

// The signed-in user read from the token, or null for a guest.
function currentUser() {
  if (!keycloak.authenticated) return null;
  const t = keycloak.tokenParsed || {};
  return { name: t.name || t.preferred_username || t.email || "there", email: t.email || "" };
}

// Where Keycloak sends the user back to after logging in or signing up: the logged-in page (/app).
// It is covered by the client's allowed redirect "http://localhost:5173/*".
const APP_URL = `${window.location.origin}/app`;

// Go to the Keycloak login page, then return to /app. The typed email is only a hint that pre-fills the form there.
export function login(emailHint) {
  return keycloak.login({ loginHint: emailHint || undefined, redirectUri: APP_URL });
}

// Go straight to Keycloak's registration page (it asks for first name, last name, email and password, and
// confirms the email by mail). redirectUri is where Keycloak sends the user back to afterwards: the logged-in page.
export function register() {
  return keycloak.register({ redirectUri: APP_URL });
}

// Sign out of Keycloak and come back to the home page.
export function logout() {
  return keycloak.logout({ redirectUri: window.location.origin });
}

// A random id for this browser tab (32 random bytes, URL-safe base64), created once and reused.
function guestSessionId() {
  const KEY = "trailwise_session_id";
  try {
    const saved = sessionStorage.getItem(KEY);
    if (saved) return saved;
  } catch {
    // sessionStorage can be blocked; fall through and make a fresh id.
  }
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const id = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  try {
    sessionStorage.setItem(KEY, id);
  } catch {
    // Not saved: the next call makes another id, which is fine for a guest.
  }
  return id;
}

// The headers the backend needs: a fresh Keycloak token for a signed-in user, the session id for a guest.
export async function getAuthHeaders() {
  if (keycloak.authenticated) {
    try {
      await keycloak.updateToken(30); // refresh when it expires within 30 seconds
      return { Authorization: `Bearer ${keycloak.token}` };
    } catch {
      // The Keycloak session ended: carry on as a guest.
    }
  }
  return { "X-Session-Id": guestSessionId() };
}

// Every backend call asks this provider for its headers (see api/client.js).
setAuthHeaderProvider(getAuthHeaders);
