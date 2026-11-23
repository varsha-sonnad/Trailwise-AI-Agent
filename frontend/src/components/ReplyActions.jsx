// ReplyActions.jsx: the buttons under a bot reply. replyActions() (replyActions.js) decides from the response fields which ones:
// "Save trip" and "Email plan" under a plan reply, or one "Plan a trip to <place>" under an explore reply about a single place.
//   Save trip   saves the plan (POST /trips); afterwards it reads "Saved ✓" and is disabled.
//   Email plan  saves first when the plan is not saved yet, then asks the server to email it to the signed-in user's own
//               verified address; afterwards it reads "Sent to your email" and is disabled. A failure shows the reason.
//   Guests      get the login popup (LoginPrompt) instead; so does a 401 from the server (an expired session).
// Who is signed in, how to log in and how to send a message come from ReplyActionsContext (App.jsx provides it).

import { createContext, useContext, useState } from "react";
import { ApiError } from "../api/client";
import { emailTrip, saveTrip } from "../api/trips";
import LoginPrompt from "./LoginPrompt";
import { emailPlan, planMessage, replyActions } from "./replyActions.js";

// user: the signed-in user or null (a guest); onLogin(): go to the login page; onSend(text): send a new chat message;
// pending: a reply is being waited for (the explore button is disabled then).
export const ReplyActionsContext = createContext({ user: null, onLogin() {}, onSend() {}, pending: false });

const API = { saveTrip, emailTrip };

// message: { role, text, mode, places, legs } of the bot reply.
export default function ReplyActions({ message }) {
  const { user, onLogin, onSend, pending } = useContext(ReplyActionsContext);
  const [tripId, setTripId] = useState(null); // the saved trip of this reply, once it is saved
  const [busy, setBusy] = useState(false); // a save or an email is in progress
  const [sent, setSent] = useState(false); // the server confirmed the email was sent
  const [error, setError] = useState("");
  const [showLogin, setShowLogin] = useState(false);

  const action = replyActions(message);
  if (action.kind === "none") return null;

  if (action.kind === "explore") {
    return (
      <div className="reply-actions">
        <button type="button" className="reply-action" disabled={pending} onClick={() => onSend(planMessage(action.place))}>
          Plan a trip to {action.place}
        </button>
      </div>
    );
  }

  // Run one action with the shared busy and error handling. A guest (or a 401) gets the login popup instead.
  async function run(task) {
    if (!user) {
      setShowLogin(true);
      return;
    }
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setShowLogin(true);
      else setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const save = () => run(async () => setTripId(await saveTrip(message)));
  const email = () =>
    run(async () => {
      await emailPlan(message, tripId, API, setTripId);
      setSent(true);
    });

  return (
    <div className="reply-actions">
      <button type="button" className="reply-action" disabled={Boolean(tripId) || busy} onClick={save}>
        {tripId ? "Saved ✓" : busy && !sent ? "Working..." : "Save trip"}
      </button>
      <button type="button" className="reply-action" disabled={sent || busy} onClick={email}>
        {sent ? "Sent to your email" : "Email plan"}
      </button>
      {error && (
        <p className="reply-actions-error" role="alert">
          {error}
        </p>
      )}
      {showLogin && <LoginPrompt onLogin={onLogin} onClose={() => setShowLogin(false)} />}
    </div>
  );
}
