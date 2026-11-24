// ChatSplit.jsx: the two-column layout of a chat page: the chat on the left and, when a reply's map is open, the map panel on
// the right. It owns the map panel's state (which reply's map is open) so that the chat stays in the same place in the page
// when the panel opens or closes: it is never re-created, so its scroll position and the message box are kept, and only its
// width changes (styles.css animates it). A new reply with places opens the panel by itself, once per message; "Show map" on a
// reply opens it again (through MapContext). On close the panel shrinks away first, then it is removed.

import { createContext, useCallback, useEffect, useRef, useState } from "react";
import MapPanel from "./MapPanel";
import { PHONE_MAX_WIDTH, closeDelay, shouldAutoOpen, shouldFocusPanel } from "./mapData";

// The messages whose map panel has already opened by itself. Module-level and keyed by the message object, so a reply opens
// the panel once (not again when you come back to this page) and a new chat starts fresh.
const autoOpened = new WeakSet();

// Gives MessageList the function that opens a reply's map (null outside a ChatSplit: then there is no "Show map" button).
export const MapContext = createContext(null);

// True while the map panel is open (not closing): the "Show map" buttons hide themselves then.
export const MapOpenContext = createContext(false);

// The browser's own answer to a media query (false where it cannot be asked, e.g. on a server).
function matches(query) {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches;
}

// messages: the conversation (to find a new reply with places); children: the chat itself (the messages and the message box).
export default function ChatSplit({ messages, children }) {
  // The reply whose map is open (null = closed), whether the user asked for it, and whether it is shrinking away.
  const [open, setOpen] = useState({ message: null, byUser: false });
  const [closing, setClosing] = useState(false);
  const timer = useRef(null);

  const showMap = useCallback((message, byUser = true) => {
    clearTimeout(timer.current); // opening again while it was closing keeps it open
    timer.current = null; // (so a later close is not mistaken for "already closing")
    setClosing(false);
    setOpen({ message, byUser });
  }, []);

  const closeMap = useCallback(() => {
    if (timer.current) return; // already closing
    setClosing(true);
    timer.current = setTimeout(() => {
      timer.current = null;
      setOpen({ message: null, byUser: false });
      setClosing(false);
    }, closeDelay(matches("(prefers-reduced-motion: reduce)")));
  }, []);

  // A new reply with places opens the map by itself (once per message).
  useEffect(() => {
    const next = shouldAutoOpen(messages, autoOpened);
    if (next) {
      autoOpened.add(next);
      showMap(next, false);
    }
  }, [messages, showMap]);

  useEffect(() => () => clearTimeout(timer.current), []);

  // After "New chat" the message is gone from the list, and so is the panel.
  const openMessage = open.message && messages.includes(open.message) ? open.message : null;

  return (
    <MapContext.Provider value={showMap}>
     <MapOpenContext.Provider value={Boolean(openMessage) && !closing}>
      <div className={`chat-split${openMessage ? " has-map" : ""}`}>
        {/* The chat stays here whether the panel is open or not, so it keeps its scroll position and what was typed. */}
        <div className="chat-col">{children}</div>
        {openMessage && (
          <MapPanel
            places={openMessage.places}
            route={openMessage.route}
            legs={openMessage.legs}
            images={openMessage.images}
            closing={closing}
            focusOnOpen={shouldFocusPanel({ openedByUser: open.byUser, isPhone: matches(`(max-width: ${PHONE_MAX_WIDTH}px)`) })}
            onClose={closeMap}
          />
        )}
      </div>
     </MapOpenContext.Provider>
    </MapContext.Provider>
  );
}
