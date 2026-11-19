// AppHome.jsx: the app page (route /app), open to guests and to signed-in users. A sidebar (Home, Chats, Saved,
// Explore) and a main area. Home is a dashboard (HomeDashboard: place cards or "continue your trip"), and Chats is the
// chat itself: the conversation and a chat bar with a mic and a send button. Clicking a card or chip on Home opens
// Chats with a message pre-filled (not sent). UI only: the chat uses the shared conversation held in App.jsx (the
// same POST /chat as the rest of the app). A guest sees a "Log in" button where a signed-in user sees "Log out", and
// the Saved panel asks a guest to log in; Saved and Explore are otherwise "coming soon".
// The layout idea (sidebar on the left, chat bar pinned to the bottom) is generic; the look is the Trailwise sunset
// style from styles.css. On phones the sidebar becomes a bar of tabs at the bottom.

import { useEffect, useState } from "react";
import { MAX_MESSAGE_CHARS } from "../api/chat";
import HomeDashboard from "../components/HomeDashboard";
import MessageList from "../components/MessageList";
import { getReturningUserData } from "../data/mockReturningUser";
import { VOICE_LANGUAGES, useVoice } from "../hooks/useVoice";

// The only sidebar items, in order. "id" picks the panel shown in the main area.
const SECTIONS = [
  { id: "home", label: "Home" },
  { id: "chats", label: "Chats" },
  { id: "saved", label: "Saved" },
  { id: "explore", label: "Explore" },
];

// Small line icons drawn inline (no icon library). Each entry is the SVG shapes inside a 24x24 box.
const ICONS = {
  home: <path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  chats: <path d="M21 12a8 8 0 0 1-11.5 7.2L4 20l1.1-4.2A8 8 0 1 1 21 12z" />,
  saved: <path d="M6 3h12v18l-6-4-6 4z" />,
  explore: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M15.5 8.5l-2 5-5 2 2-5z" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </>
  ),
  send: <path d="M12 19V5M5 12l7-7 7 7" />,
};

// One icon by name; it takes the surrounding text color.
function Icon({ name }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

// The message box with the mic and the send button.
// onSend(text): send a message. disabled: true while a reply is pending.
// initialText: text to start with (from the public home page's search box, or a Home card or chip); the cursor is put in the box when set.
// onTextTaken(): called once the box has taken initialText, so the page can forget it.
function ChatBar({ onSend, disabled, initialText, onTextTaken }) {
  const [text, setText] = useState(initialText);
  useEffect(() => {
    if (initialText) onTextTaken();
  }, [initialText, onTextTaken]);
  const [lang, setLang] = useState(VOICE_LANGUAGES[0].code);
  // Speech recognition (the browser does it). Spoken text is added to the box for the user to check.
  const voice = useVoice({
    lang,
    onResult: (spoken) => setText((current) => (current ? `${current} ${spoken}` : spoken)),
  });

  // Send the message and clear the box.
  function submit(event) {
    event.preventDefault(); // stop the page from reloading
    if (!text.trim() || disabled) return;
    onSend(text);
    setText("");
  }

  // Enter sends; Shift+Enter inserts a new line.
  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) submit(event);
  }

  return (
    <form className="chatbar" onSubmit={submit}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        rows={2}
        maxLength={MAX_MESSAGE_CHARS}
        placeholder="Ask Trailwise."
        aria-label="Your message"
        autoFocus={Boolean(initialText)}
      />
      <div className="chatbar-actions">
        {voice.error && <span className="voice-error">{voice.error}</span>}
        {voice.supported && (
          <select value={lang} onChange={(e) => setLang(e.target.value)} disabled={voice.listening} aria-label="Voice language">
            {VOICE_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          className={`round ghost${voice.listening ? " listening" : ""}`}
          onClick={voice.listening ? voice.stop : voice.start}
          disabled={disabled || !voice.supported}
          aria-label={voice.listening ? "Stop listening" : "Speak your message"}
          aria-pressed={voice.listening}
          title={voice.supported ? "Speak" : "Voice input is not supported in this browser"}
        >
          <Icon name="mic" />
        </button>
        <button type="submit" className="round" disabled={disabled || !text.trim()} aria-label="Send">
          <Icon name="send" />
        </button>
      </div>
    </form>
  );
}

// user: the signed-in user ({ name }), or null for a guest; messages / pending / onSend: the shared conversation from
// App.jsx; onLogin(): open the login pop-up; onLogout(): sign out; draft: text from the public home page's search box,
// which pre-fills the chat input once (onDraftUsed() then clears it). Nothing here talks to the backend or Keycloak.
export default function AppHome({ user, messages, pending, onSend, onLogin, onLogout, draft, onDraftUsed }) {
  // Which panel the sidebar has selected. Text from the public home page's search box goes straight to Chats.
  const [section, setSection] = useState(draft ? "chats" : "home");
  // The text to pre-fill in the chat bar, taken once when Chats opens (the chat bar clears it after using it, so
  // switching to another panel and back does not fill the box again).
  const [prefill, setPrefill] = useState(draft);
  useEffect(() => {
    if (draft) onDraftUsed();
  }, [draft, onDraftUsed]);
  // A card, chip or Continue button on Home: open Chats with this message pre-filled (it is not sent).
  function openChatWith(text) {
    setPrefill(text);
    setSection("chats");
  }
  // The first message in the list is Trailwise's own welcome line, so "started" means the user has sent something.
  const started = messages.length > 1 || pending;
  // TODO(backend): real returning-user data. For now this is null (guest or new) unless the mock is switched on.
  const returning = getReturningUserData(user);

  return (
    <div className="appshell">
      <aside className="side">
        <button type="button" className="side-brand" onClick={() => setSection("home")}>
          <span aria-hidden="true">☀</span> Trailwise
        </button>
        <nav className="side-nav" aria-label="App sections">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className="side-link"
              aria-current={s.id === section ? "page" : undefined}
              onClick={() => setSection(s.id)}
            >
              <Icon name={s.id} />
              <span>{s.label}</span>
            </button>
          ))}
        </nav>
      </aside>

      <section className="app-main">
        <header className="app-top">
          {user ? (
            <>
              <span className="app-user">Hi, {user.name.split(" ")[0]}</span>
              <button type="button" className="link-button" onClick={onLogout}>
                Log out
              </button>
            </>
          ) : (
            // A guest: Log in instead of Log out (it opens the same login pop-up as the home page).
            <>
              <span className="app-user">Browsing as a guest</span>
              <button type="button" className="link-button" onClick={onLogin}>
                Log in
              </button>
            </>
          )}
        </header>

        {section === "home" && <HomeDashboard user={user} returning={returning} onPrompt={openChatWith} />}

        {section === "chats" && (
          <>
            {started ? (
              <MessageList messages={messages} pending={pending} />
            ) : (
              <div className="app-content">
                <div className="app-mark" aria-hidden="true">
                  ☀
                </div>
                <h2 className="greeting">Ask Trailwise</h2>
                <p className="greeting-sub">Tell me where you want to go and I will help you plan it.</p>
              </div>
            )}
            <ChatBar onSend={onSend} disabled={pending} initialText={prefill} onTextTaken={() => setPrefill("")} />
          </>
        )}

        {/* Saved and Explore have no data yet. A guest who opens Saved is asked to log in. */}
        {(section === "saved" || section === "explore") && (
          <div className="app-content">
            <h2 className="greeting">{SECTIONS.find((s) => s.id === section).label}</h2>
            <p className="greeting-sub">
              {section === "saved" && !user ? "Log in to save your trips" : "Coming soon. For now, plan a trip from Home."}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
