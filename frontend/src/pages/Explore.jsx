// Explore.jsx: the Explore section of the /app page. A search box and one grid of suggested place cards (photo, name,
// one-line description). Clicking a card, or searching for a place, opens the slide-in side panel (PlacePanel) for that place.
// "Learn More" and "Plan a Trip" in the panel open Chats with a message pre-filled (it is never sent for the user), through
// onPrompt, the same hand-off the Home cards use. UI only: the places are the hardcoded list in data/places.js and the
// search is a mock (see mockSearchPlace there). No keys, no network, no timings or prices.

import { useCallback, useEffect, useRef, useState } from "react";
import PlacePanel from "../components/PlacePanel";
import { PLACES, learnPrompt, mockSearchPlace, planPrompt, rankPlaces } from "../data/places";

// The longest search text accepted, in characters (the same limit as other short text boxes in the app).
const MAX_QUERY_CHARS = 100;

// onPrompt(text): open Chats with this message pre-filled. prefs: the saved preferences, used only to reorder the cards
// (matching interests first, nothing hidden) and to add the budget style and travel mode to the Plan a Trip message.
export default function Explore({ onPrompt, prefs }) {
  // What the search box holds.
  const [query, setQuery] = useState("");
  // A short hint under the search box (for example when the text is too short), or "".
  const [hint, setHint] = useState("");
  // What the side panel shows: null (closed), or { status: "found" | "searching" | "notfound", place?, query? }.
  const [view, setView] = useState(null);
  // Counts searches. A search result is used only if no newer search or close has happened since, so a late answer
  // never reopens a panel the user already closed.
  const searchId = useRef(0);
  useEffect(() => () => void (searchId.current += 1), []); // leaving the page cancels a running search

  // Close the panel (back arrow, Escape or a click outside) and forget any search still running.
  const closePanel = useCallback(() => {
    searchId.current += 1;
    setView(null);
  }, []);

  // Search for the typed place with the mock search; the panel opens at once in its "Searching..." state.
  async function onSearch(event) {
    event.preventDefault(); // stop the page from reloading
    const text = query.trim();
    if (text.length < 2) {
      setHint("Type at least 2 letters to search.");
      return;
    }
    setHint("");
    const mine = (searchId.current += 1);
    setView({ status: "searching", query: text });
    const place = await mockSearchPlace(text);
    if (mine !== searchId.current) return; // closed or replaced while searching
    setView(place ? { status: "found", place } : { status: "notfound", query: text });
  }

  return (
    <div className="explore">
      <h2 className="sr-only">Explore places</h2>

      <form className="explore-search" onSubmit={onSearch} role="search">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={MAX_QUERY_CHARS}
          placeholder="Search places..."
          aria-label="Search places"
        />
        <button type="submit">Search</button>
      </form>
      {hint && (
        <p className="explore-hint" role="status">
          {hint}
        </p>
      )}

      <div className="place-grid">
        {rankPlaces(PLACES, prefs).map((place) => (
          <button key={place.id} type="button" className="place-card" onClick={() => setView({ status: "found", place })}>
            <img className="place-img" src={place.image} alt="" width="300" height="180" loading="lazy" />
            <span className="place-body">
              <span className="place-name">{place.name}</span>
              <span className="place-tagline">{place.tagline}</span>
            </span>
          </button>
        ))}
      </div>

      {view && (
        <PlacePanel
          view={view}
          onClose={closePanel}
          onLearnMore={(name) => onPrompt(learnPrompt(name))}
          onPlan={(name) => onPrompt(planPrompt(name, prefs))}
        />
      )}
    </div>
  );
}
