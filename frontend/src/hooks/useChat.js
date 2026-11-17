// useChat.js: React hook that holds the conversation state for the chat screen.
// It keeps the list of messages, whether a reply is pending, and sends messages to the backend.
// The backend does not keep chat history yet, so this list lives only in the browser tab.

import { useCallback, useRef, useState } from "react";
import { MAX_MESSAGE_CHARS, sendMessage } from "../api/chat";

// The first message shown when the page opens.
const GREETING = "Hi! Tell me where you want to go and I will help you plan it.";

export function useChat() {
  // Each message: { id, role: "user" | "bot" | "error", text }.
  const [messages, setMessages] = useState([{ id: 0, role: "bot", text: GREETING }]);
  // True while we wait for the backend, so the UI can show "Thinking..." and block double sends.
  const [pending, setPending] = useState(false);
  // Counter that gives every message its own id (React needs stable keys).
  const nextId = useRef(1);

  // Add one message to the end of the conversation.
  const addMessage = useCallback((role, text) => {
    setMessages((current) => [...current, { id: nextId.current++, role, text }]);
  }, []);

  // Send a user message: show it, call the backend, then show the reply or an error.
  const send = useCallback(
    async (text) => {
      const message = text.trim();
      if (!message || pending) return;
      if (message.length > MAX_MESSAGE_CHARS) {
        addMessage("error", `Please keep your message under ${MAX_MESSAGE_CHARS} characters.`);
        return;
      }
      addMessage("user", message);
      setPending(true);
      try {
        addMessage("bot", await sendMessage(message));
      } catch (err) {
        // ApiError messages are already friendly; anything else gets a generic line.
        addMessage("error", err.message || "Something went wrong. Please try again.");
      } finally {
        setPending(false);
      }
    },
    [pending, addMessage],
  );

  return { messages, pending, send };
}
