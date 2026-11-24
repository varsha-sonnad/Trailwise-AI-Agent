// VoiceButton.jsx: voice input controls: a language picker and a microphone button.
// The recognised text is handed to onText; the user reviews it and presses Send themselves.
// If the browser has no speech recognition, a short note asks the user to type instead.

import { VOICE_LANGUAGES, useVoice, useVoiceLanguage } from "../hooks/useVoice";

// onText(text): called with the recognised text. disabled: true while a reply is pending.
// replyLanguage: the saved reply language (en, hi, kn or null); the picker starts on it.
export default function VoiceButton({ onText, disabled, replyLanguage }) {
  const [lang, setLang] = useVoiceLanguage(replyLanguage);
  const { supported, listening, error, start, stop } = useVoice({ lang, onResult: onText });

  // Text fallback (plan: speech support is uneven, especially for Kannada).
  if (!supported) {
    return <div className="voice">Voice input is not supported in this browser. Please type your message.</div>;
  }

  return (
    <div className="voice">
      <select value={lang} onChange={(e) => setLang(e.target.value)} disabled={listening} aria-label="Voice language">
        {VOICE_LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        className={listening ? "listening" : ""}
        onClick={listening ? stop : start}
        disabled={disabled}
        aria-pressed={listening}
      >
        {listening ? "Stop" : "Speak"}
      </button>
      {error && <span className="voice-error">{error}</span>}
    </div>
  );
}
