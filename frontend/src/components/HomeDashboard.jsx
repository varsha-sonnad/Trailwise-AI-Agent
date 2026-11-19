// HomeDashboard.jsx: the Home section of the /app page. It is a dashboard, not a chat: a greeting, quick-start chips,
// and then either "Start your first trip" with six place cards (guest or new user) or "Continue your trip to <place>"
// with a "Recently explored" row (returning user). Every click only hands a message to onPrompt, which opens Chat with
// that message pre-filled (it is never sent for the user). No timings or prices appear on any card.

import { PLACES, QUICK_STARTS, placeById, tripPrompt } from "../data/places";

// "Good morning", "Good afternoon" or "Good evening" from the local time.
function greetingFor(date) {
  const hour = date.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

// One place card: picture, title and tagline. onPick(): the card was clicked. compact: the small version used in the
// "Recently explored" row (no tagline).
function PlaceCard({ place, onPick, compact }) {
  return (
    <button type="button" className="place-card" onClick={onPick}>
      <img className="place-img" src={place.image} alt="" width="300" height="180" loading="lazy" />
      <span className="place-body">
        <span className="place-name">{place.name}</span>
        {!compact && <span className="place-tagline">{place.tagline}</span>}
      </span>
    </button>
  );
}

// user: the signed-in user ({ name }) or null for a guest. returning: { lastTrip: { placeId, name }, recentlyExplored:
// [place ids] } for a returning user, or null for a guest or new user. onPrompt(text): open Chat with this message.
export default function HomeDashboard({ user, returning, onPrompt }) {
  const firstName = user ? user.name.split(" ")[0] : "";

  return (
    <div className="dash">
      <header>
        <h2 className="greeting">
          {greetingFor(new Date())}
          {firstName && `, ${firstName}`}
        </h2>
        <p className="greeting-sub">Where would you like to go next?</p>
      </header>

      <div className="chips" role="group" aria-label="Quick start">
        {QUICK_STARTS.map((q) => (
          <button key={q.label} type="button" className="chip" onClick={() => onPrompt(q.prompt)}>
            {q.label}
          </button>
        ))}
      </div>

      {returning ? (
        <>
          <div className="continue-card">
            {placeById(returning.lastTrip.placeId) && (
              <img className="continue-img" src={placeById(returning.lastTrip.placeId).image} alt="" width="300" height="180" />
            )}
            <div className="continue-body">
              <h3>Continue your trip to {returning.lastTrip.name}</h3>
              <p>Pick up where you left off.</p>
            </div>
            <button type="button" onClick={() => onPrompt(`Let's continue planning my trip to ${returning.lastTrip.name}`)}>
              Continue
            </button>
          </div>

          <h3>Recently explored</h3>
          <div className="recent-row">
            {returning.recentlyExplored.map(placeById).filter(Boolean).map((place) => (
              <PlaceCard key={place.id} place={place} compact onPick={() => onPrompt(tripPrompt(place))} />
            ))}
          </div>
        </>
      ) : (
        <>
          <h3>Start your first trip</h3>
          <div className="place-grid">
            {PLACES.map((place) => (
              <PlaceCard key={place.id} place={place} onPick={() => onPrompt(tripPrompt(place))} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
