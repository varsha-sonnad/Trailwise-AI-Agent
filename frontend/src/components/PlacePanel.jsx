// PlacePanel.jsx: the slide-in side panel on the Explore page. It shows one place (photo with its credit, name, region,
// short description, "Learn More" and "Plan a Trip"), a "Searching..." state while the mock search runs, or a friendly
// "not found" message. It closes with the back arrow, the Escape key, or a click on the dimmed area outside it.
// Keyboard: focus moves into the panel when it opens, Tab stays inside it, and focus returns to what opened it on close.
// UI only: it holds no data and calls nothing; the page decides what each button does (onLearnMore, onPlan).

import { useEffect, useRef } from "react";

// Everything inside the panel that can take focus (for the Tab loop).
const FOCUSABLE = 'button:not([disabled]), a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

// view: { status: "found", place } | { status: "searching", query } | { status: "notfound", query }.
// onClose(): close the panel. onLearnMore(name) and onPlan(name): the buttons, given the place name (or the typed text).
export default function PlacePanel({ view, onClose, onLearnMore, onPlan }) {
  const panelRef = useRef(null);
  const backRef = useRef(null);

  // On open: remember what had focus and move focus to the back arrow. On close: give focus back.
  useEffect(() => {
    const opener = document.activeElement;
    backRef.current?.focus();
    return () => {
      if (opener && typeof opener.focus === "function") opener.focus();
    };
  }, []);

  // Escape closes the panel; Tab and Shift+Tab loop inside it.
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const items = [...panelRef.current.querySelectorAll(FOCUSABLE)];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const place = view.status === "found" ? view.place : null;
  // The heading: the place's name, or the text the user typed.
  const title = place ? place.name : view.query;

  return (
    <>
      {/* The dimmed area: a click anywhere on it closes the panel. It is not a tab stop (the back arrow does the same). */}
      <div className="panel-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="place-panel" role="dialog" aria-modal="true" aria-labelledby="place-panel-title" ref={panelRef}>
        <div className="panel-top">
          <button type="button" className="panel-back" onClick={onClose} aria-label="Back to places" ref={backRef}>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
          </button>
        </div>

        {place && (
          <>
            <img className="panel-photo" src={place.image} alt={`${place.name}`} width="460" height="280" />
            {/* The credit is always shown under a photo: the photographer, linked when there is an address. */}
            <p className="panel-credit">
              {place.creditUrl ? (
                <a href={place.creditUrl} target="_blank" rel="noopener noreferrer">
                  {place.credit}
                </a>
              ) : (
                place.credit
              )}
            </p>
          </>
        )}
        {view.status === "searching" && <div className="panel-photo panel-photo-empty" aria-hidden="true" />}

        <div className="panel-body">
          <h2 className="panel-name" id="place-panel-title">
            {title}
          </h2>

          {place && (
            <>
              <p className="panel-region">{place.region}</p>
              <p className="panel-text">{place.description}</p>
              <div className="panel-actions">
                <button type="button" className="panel-secondary" onClick={() => onLearnMore(place.name)}>
                  Learn More
                </button>
                <button type="button" onClick={() => onPlan(place.name)}>
                  Plan a Trip
                </button>
              </div>
            </>
          )}

          {view.status === "searching" && (
            <p className="panel-text panel-searching" role="status">
              Searching...
            </p>
          )}

          {view.status === "notfound" && (
            <>
              <p className="panel-text" role="status">
                We could not find that place yet. Try one of the suggested places, or ask Trailwise in the chat and it will
                help you plan it.
              </p>
              <div className="panel-actions">
                <button type="button" onClick={() => onPlan(view.query)}>
                  Plan a Trip
                </button>
              </div>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
