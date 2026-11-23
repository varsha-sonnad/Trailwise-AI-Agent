// LoginPrompt.jsx: the small login popup a guest gets when they press "Save trip" or "Email plan".
// It only offers to go to the Keycloak login page (onLogin) or to close (onClose: the Cancel button, Escape, or a press outside).
// Note: logging in reloads the page, so the current chat is not kept.

import { useEffect, useRef } from "react";

export default function LoginPrompt({ onLogin, onClose }) {
  const loginButton = useRef(null);

  // Focus the main button when the popup opens, and close on Escape.
  useEffect(() => {
    loginButton.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    // A press on the dark backdrop itself (not on the box) closes the popup.
    <div className="login-prompt-backdrop" onPointerDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="login-prompt" role="dialog" aria-modal="true" aria-labelledby="login-prompt-title">
        <h2 id="login-prompt-title">Log in to continue</h2>
        <p>Log in to save your trip or email it to yourself.</p>
        <div className="login-prompt-actions">
          <button type="button" className="login-prompt-primary" ref={loginButton} onClick={() => onLogin()}>
            Log in
          </button>
          <button type="button" className="login-prompt-cancel" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
