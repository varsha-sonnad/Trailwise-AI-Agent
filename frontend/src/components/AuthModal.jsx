// AuthModal.jsx: the "Log in" pop-up. It asks for an email, checks it looks valid, and reports it through onLogin
// (App.jsx then sends the user to Keycloak's login page, where the password is typed). "Continue as guest" closes it.
// "New to Trailwise? Sign up" calls onSignUp straight away: sign-up happens only on Keycloak's register page, so
// there is no email step and no form for it here. Nothing is stored and the modal makes no backend call.
// It closes with the X button, the Escape key, or a click on the dark area outside the box.

import { useEffect, useRef, useState } from "react";

// A simple "looks like an email" check (text, an @, text, a dot, text). Keycloak does the real check.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Elements the Tab key may move between inside the box (used for the focus trap).
const FOCUSABLE = "button, [href], input";

// onClose(): close the pop-up. onLogin(email): a valid email was entered. onSignUp(): go to Keycloak's register page.
// onGuest(): "Continue as guest".
export default function AuthModal({ onClose, onLogin, onSignUp, onGuest }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const boxRef = useRef(null);
  const emailRef = useRef(null);

  // On open: remember what had focus and move it to the email field; on close: give focus back.
  useEffect(() => {
    const previous = document.activeElement;
    emailRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  // Escape closes the pop-up; Tab is kept inside the box so keyboard users cannot tab behind it.
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = boxRef.current.querySelectorAll(FOCUSABLE);
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

  // Continue: show an error for a bad email, otherwise pass it on.
  function submit(event) {
    event.preventDefault(); // stop the page from reloading
    const value = email.trim();
    if (!EMAIL_PATTERN.test(value)) {
      setError("Enter a valid email address.");
      emailRef.current?.focus();
      return;
    }
    onLogin(value);
  }

  return (
    // The dark backdrop. Only a click that starts on the backdrop itself closes the box
    // (a text selection that drags out of the box must not close it).
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="auth-title" ref={boxRef}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <div className="modal-mark" aria-hidden="true">
          ☀
        </div>
        <h2 id="auth-title">Log in</h2>
        <form onSubmit={submit} noValidate>
          <input
            ref={emailRef}
            type="email"
            className="modal-input"
            placeholder="Email"
            aria-label="Email"
            autoComplete="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setError(""); // clear the error as soon as the user edits
            }}
            aria-invalid={error ? "true" : undefined}
            aria-describedby={error ? "auth-error-email" : undefined}
          />
          {error && (
            <p id="auth-error-email" className="modal-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="modal-continue">
            Continue
          </button>
        </form>
        <p className="modal-note">You will enter your password on the next page.</p>
        <button type="button" className="link-button" onClick={onGuest}>
          Continue as guest
        </button>
        <p className="modal-switch">
          New to Trailwise?{" "}
          <button type="button" className="link-button inline" onClick={onSignUp}>
            Sign up
          </button>
        </p>
      </div>
    </div>
  );
}
