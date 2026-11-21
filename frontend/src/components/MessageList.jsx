// MessageList.jsx: the scrolling conversation. It shows every message, a "Thinking..." bubble while a
// reply is pending, and keeps the newest message in view. The map panel is not here: ChatSplit owns it and gives each reply's
// "Show map" button its function through MapContext. When the chat column changes width (the map panel opening or closing) the
// text re-wraps; the list then puts the view back at the same distance from the bottom, so the scroll position is kept.

import { useContext, useEffect, useRef } from "react";
import { MapContext, MapOpenContext } from "./ChatSplit";
import MessageBubble from "./MessageBubble";
import { anchoredScrollTop, scrollGap } from "./mapData";

// messages: [{ id, role, text, images, places, route }]; pending: true while waiting for the backend.
export default function MessageList({ messages, pending }) {
  // An invisible element at the end of the list that we scroll into view.
  const endRef = useRef(null);
  const listRef = useRef(null);
  // How far the view is from the bottom (0 = at the bottom), updated as the user scrolls.
  const gap = useRef(0);
  const showMap = useContext(MapContext);
  const mapOpen = useContext(MapOpenContext); // true while the map panel is open: then "Show map" is hidden

  // Scroll down whenever a message is added or the pending state changes.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  // Keep the scroll position when the list's width changes: restore the same distance from the bottom.
  useEffect(() => {
    const box = listRef.current;
    if (!box || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => {
      box.scrollTop = anchoredScrollTop(box, gap.current);
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  return (
    // aria-live makes screen readers announce new messages.
    <div className="messages" aria-live="polite" ref={listRef} onScroll={(event) => (gap.current = scrollGap(event.currentTarget))}>
      {messages.map((m) => (
        <MessageBubble
          key={m.id}
          role={m.role}
          text={m.text}
          images={m.images}
          places={m.places}
          onShowMap={showMap && !mapOpen ? () => showMap(m, true) : undefined}
        />
      ))}
      {pending && <div className="msg pending">Thinking...</div>}
      <div ref={endRef} />
    </div>
  );
}
