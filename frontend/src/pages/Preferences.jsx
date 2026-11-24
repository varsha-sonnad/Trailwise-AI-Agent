// Preferences.jsx: the Preferences page on /app: four white cards (Travel mode, Budget style, Interests, Language)
// of pill buttons, and a save bar at the bottom. Signed-in users' choices are saved to their account; a guest's stay in
// this browser (see hooks/usePreferences.js). The page only draws; the hook holds the state and does the saving.

import { BUDGETS, INTERESTS, LANGUAGES, TRAVEL_MODES } from "../api/preferences";

// One group of pill buttons. options: [{ value, label, lang? }]; isOn(value): is it selected; onPick(value): clicked.
// Each pill is a button with aria-pressed, so screen readers announce it as a toggle.
function Pills({ options, isOn, onPick, labelledBy }) {
  return (
    <div className="pref-pills" role="group" aria-labelledby={labelledBy}>
      {options.map((o) => (
        <button key={o.value} type="button" className="pref-pill" aria-pressed={isOn(o.value)} lang={o.lang} onClick={() => onPick(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// One white card with a heading, an optional hint line and its pills.
function Card({ id, title, hint, children }) {
  return (
    <section className="pref-card" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      {hint && <p className="pref-hint">{hint}</p>}
      {children}
    </section>
  );
}

// user: the signed-in user or null (a guest); prefs: what usePreferences returned.
export default function Preferences({ user, prefs }) {
  const { draft, dirty, loading, loadFailed, saving, error, toggleMode, toggleInterest, pickBudget, pickLanguage, reset, save } = prefs;

  if (loading) return <div className="placeholder">Loading...</div>;

  return (
    <div className="prefs">
      <div className="prefs-scroll">
        <h2 className="prefs-title">Your travel preferences</h2>
        <p className="prefs-intro">Tell us how you like to travel. Pick as many or as few as you like, then save.</p>

        {!user && (
          <p className="pref-note">You're browsing as a guest. Your choices are kept in this browser. Log in to save them to your account.</p>
        )}

        <Card id="pref-mode" title="Travel mode" hint="Pick any.">
          <Pills labelledBy="pref-mode" options={TRAVEL_MODES} isOn={(v) => draft.travel_mode.includes(v)} onPick={toggleMode} />
        </Card>

        <Card id="pref-budget" title="Budget style" hint="Pick one. This is a style, not a price.">
          <Pills labelledBy="pref-budget" options={BUDGETS} isOn={(v) => draft.budget === v} onPick={pickBudget} />
        </Card>

        <Card id="pref-interests" title="Interests" hint="Pick any.">
          <Pills labelledBy="pref-interests" options={INTERESTS} isOn={(v) => draft.interests.includes(v)} onPick={toggleInterest} />
        </Card>

        <Card id="pref-language" title="Language" hint="Pick one.">
          <Pills labelledBy="pref-language" options={LANGUAGES} isOn={(v) => draft.reply_language === v} onPick={pickLanguage} />
        </Card>
      </div>

      {/* The save bar: always at the bottom; the message shows only while there is something unsaved. */}
      <div className="pref-bar">
        <p className="pref-bar-text" role="status">
          {error ? <span className="pref-error">{error}</span> : dirty ? "You have unsaved changes" : ""}
        </p>
        <button type="button" className="pref-btn ghost" onClick={reset} disabled={!dirty || saving}>
          Reset
        </button>
        <button type="button" className="pref-btn" onClick={save} disabled={!dirty || saving || loadFailed}>
          {saving ? "Saving..." : "Save preferences"}
        </button>
      </div>
    </div>
  );
}
