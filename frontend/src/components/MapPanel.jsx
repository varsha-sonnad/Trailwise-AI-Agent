// MapPanel.jsx: the map panel. On a desktop it is the right-hand column of the chat page (the chat shrinks to make room, see
// ChatSplit.jsx and styles.css), so the messages and the message box stay visible and usable; on a phone it is a full-screen
// overlay. It closes with the close button or the Escape key, and on a phone also with a press outside the panel. Keyboard focus
// goes to the close button when it was opened on purpose (or on a phone) and comes back to where it was when the panel closes.
// The map itself is PlaceMap.jsx.

import { useEffect, useRef } from "react";
import PlaceMap from "./PlaceMap";
import { closeOnOutsidePress, isCloseKey } from "./mapData";

// places, route, images: the lists of the reply the panel was opened for; onClose: called for every way of closing;
// closing: true while it shrinks away; focusOnOpen: whether it takes keyboard focus when it opens (see shouldFocusPanel).
export default function MapPanel({ places, route, legs, images, onClose, closing = false, focusOnOpen = true }) {
  const closeButton = useRef(null);
  // The latest onClose, so the keyboard listener below is set up once and not again on every render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    // Only when focus is moved into the panel is it given back afterwards (on desktop it stays in the message box).
    const before = focusOnOpen ? document.activeElement : null;
    if (focusOnOpen) closeButton.current?.focus();
    const onKey = (event) => {
      if (isCloseKey(event)) closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (before && typeof before.focus === "function" && document.contains(before)) before.focus();
    };
  }, [focusOnOpen]);

  return (
    // On a desktop the backdrop takes no space (display: contents), so a press outside the panel does nothing and the chat
    // stays usable. On a phone it covers the screen: a pointer press on it itself (not on the panel) is a click outside.
    <div className="map-backdrop" onPointerDown={(event) => closeOnOutsidePress(event, onClose)}>
      <aside className={`map-panel${closing ? " closing" : ""}`} role="dialog" aria-label="Map">
        <header className="map-panel-header">
          <h2>Map</h2>
          <button type="button" className="map-close" ref={closeButton} onClick={onClose} aria-label="Close map">
            &times;
          </button>
        </header>
        <PlaceMap places={places} route={route} legs={legs} images={images} />
      </aside>
    </div>
  );
}
