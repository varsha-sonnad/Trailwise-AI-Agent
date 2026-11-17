// ChatInput.jsx: the message box, the Send button and the voice controls.
// Enter sends, Shift+Enter inserts a new line. Voice text is added to the box for the user to check.

import { useState } from "react";
import { MAX_MESSAGE_CHARS } from "../api/chat";
import VoiceButton from "./VoiceButton";

// onSend(text): called with the message. disabled: true while a reply is pending.
export default function ChatInput({ onSend, disabled }) {
  const [text, setText] = useState("");

  // Send the message and clear the box.
  function submit(event) {
    event.preventDefault(); // stop the page from reloading
    if (!text.trim() || disabled) return;
    onSend(text);
    setText("");
  }

  // Enter sends; Shift+Enter inserts a new line.
  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit(event);
    }
  }

  // Add recognised speech to what is already typed.
  function addVoiceText(spoken) {
    setText((current) => (current ? `${current} ${spoken}` : spoken));
  }

  return (
    <form className="composer" onSubmit={submit}>
      <div className="composer-row">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          maxLength={MAX_MESSAGE_CHARS}
          placeholder="e.g. Plan a 3-day trip to Goa from Bangalore"
          aria-label="Your message"
          autoFocus
        />
        <button type="submit" disabled={disabled || !text.trim()}>
          Send
        </button>
      </div>
      <VoiceButton onText={addVoiceText} disabled={disabled} />
    </form>
  );
}
