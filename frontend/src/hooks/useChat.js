// useChat.js: React hook that holds the conversation state for the chat screen.
// It keeps the list of messages, whether a reply is pending, and sends messages to the backend.
// Per-session memory: the earlier messages, the mode of the last reply and the last place are kept in React state only
// (no localStorage, no cookies, nothing on the server) and are sent with every message, so the assistant remembers this chat.
// "New chat" (reset) and a page refresh clear all of it.

import { useCallback, useRef, useState } from "react";
import { MAX_MESSAGE_CHARS, sendMessage } from "../api/chat";
import { emptyMemory, initialMessages, memoryFor, nextMemory } from "./chatMemory";

// preferences: the saved preferences (reply language, budget style, travel mode are sent with each message).
export function useChat(preferences) {
  // Each message: { id, role: "user" | "bot" | "error", text, images, places, route } (images: the photos under a bot reply, usually
  // none; places and route: what the map panel draws for that reply, usually empty).
  const [messages, setMessages] = useState(initialMessages);
  // True while we wait for the backend, so the UI can show "Thinking..." and block double sends.
  const [pending, setPending] = useState(false);
  // Counter that gives every message its own id (React needs stable keys).
  const nextId = useRef(1);
  // The mode of the last reply and the last geocoded place (see chatMemory.js). A ref: it is not shown, only sent.
  const memory = useRef(emptyMemory());
  // The latest saved preferences, read when a message is sent (a ref, so a change never recreates send or restarts a chat).
  const prefsRef = useRef(preferences);
  prefsRef.current = preferences;
  // Counts "New chat" presses. A reply that arrives after one is dropped, so it cannot leak into the new chat.
  const generation = useRef(0);

  // Add one message to the end of the conversation.
  const addMessage = useCallback((role, text, images = [], places = [], route = []) => {
    setMessages((current) => [...current, { id: nextId.current++, role, text, images, places, route }]);
  }, []);

  // Send a user message: show it, call the backend with the memory, then show the reply or an error.
  const send = useCallback(
    async (text) => {
      const message = text.trim();
      if (!message || pending) return;
      if (message.length > MAX_MESSAGE_CHARS) {
        addMessage("error", `Please keep your message under ${MAX_MESSAGE_CHARS} characters.`);
        return;
      }
      // The memory that goes along: the earlier messages (before this one), the last mode and the last place.
      const sent = memoryFor(messages, memory.current);
      const mine = generation.current;
      addMessage("user", message);
      setPending(true);
      try {
        const { reply, images, mode, places, route } = await sendMessage(message, { ...sent, preferences: prefsRef.current });
        if (mine !== generation.current) return; // "New chat" was pressed while waiting
        memory.current = nextMemory(memory.current, { mode, places });
        addMessage("bot", reply, images, places, route);
      } catch (err) {
        if (mine !== generation.current) return;
        // ApiError messages are already friendly; anything else gets a generic line.
        addMessage("error", err.message || "Something went wrong. Please try again.");
      } finally {
        if (mine === generation.current) setPending(false);
      }
    },
    [pending, messages, addMessage],
  );

  // New chat: forget the messages, the mode and the place, and ignore any reply still on its way.
  const reset = useCallback(() => {
    generation.current += 1;
    memory.current = emptyMemory();
    nextId.current = 1;
    setMessages(initialMessages());
    setPending(false);
  }, []);

  return { messages, pending, send, reset };
}
