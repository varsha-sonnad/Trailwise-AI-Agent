// mockReturningUser.js: MOCK data for the "returning user" view of the Home dashboard on /app.
//
// TODO(backend): this is NOT real data. When the backend has endpoints for a user's trips and recently explored
// places (see the chat history and saved trips work), replace getReturningUserData() with a real fetch and delete
// this mock. Until then the mock is OFF, so nobody is ever shown a made-up trip as if it were theirs.
// To try the returning view while building the UI, change USE_MOCK_RETURNING_USER to true (logged-in users only).

// Switch for the mock. Keep it false in anything you commit.
export const USE_MOCK_RETURNING_USER = false;

// What the backend will eventually return for a returning user: their last trip, and the place ids they explored.
export const MOCK_RETURNING_USER = {
  lastTrip: { placeId: "goa", name: "Goa" },
  recentlyExplored: ["hampi", "jaipur", "ladakh", "varanasi"],
};

// The returning-user data for this visitor, or null when they are a guest or new (so the dashboard shows
// "Start your first trip"). user: the signed-in user ({ name }) or null.
export function getReturningUserData(user) {
  if (!USE_MOCK_RETURNING_USER || !user) return null;
  return MOCK_RETURNING_USER;
}
