// client.js: the one place that makes HTTP calls to the Trailwise backend.
// Every call goes through postJson, so errors look the same everywhere and no key is ever involved
// (the frontend holds no API keys; the backend does all the external calls).

// Where the backend runs. Set VITE_API_URL in frontend/.env.local to change it (see .env.example).
export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

// Supplies the auth headers for every call (a Keycloak Bearer token, or a guest session id).
// auth/session.js registers the real one; until then no extra headers are sent.
let authHeaders = async () => ({});
export function setAuthHeaderProvider(provider) {
  authHeaders = provider;
}

// An error from the backend or the network. status is the HTTP status, or 0 when the server was unreachable.
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Send a JSON POST to the backend and return the parsed JSON reply.
// describeError turns an HTTP status into a short message the user can understand.
export async function postJson(path, body, describeError) {
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify(body),
    });
  } catch {
    // fetch throws a TypeError when the server cannot be reached at all.
    throw new ApiError(0, `Cannot reach the server. Is the backend running on ${API_URL}?`);
  }
  if (!response.ok) {
    throw new ApiError(response.status, describeError(response.status));
  }
  return response.json();
}
