// MessageList.jsx: the scrolling conversation. It shows every message, a "Thinking..." bubble while a
// reply is pending, and keeps the newest message in view.

import { useEffect, useRef } from "react";
import MessageBubble from "./MessageBubble";

// messages: [{ id, role, text }]; pending: true while waiting for the backend.
export default function MessageList({ messages, pending }) {
  // An invisible element at the end of the list that we scroll into view.
  const endRef = useRef(null);

  // Scroll down whenever a message is added or the pending state changes.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  return (
    // aria-live makes screen readers announce new messages.
    <div className="messages" aria-live="polite">
      {messages.map((m) => (
        <MessageBubble key={m.id} role={m.role} text={m.text} />
      ))}
      {pending && <div className="msg pending">Thinking...</div>}
      <div ref={endRef} />
    </div>
  );
}
