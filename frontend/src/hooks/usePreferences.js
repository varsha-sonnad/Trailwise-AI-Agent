// usePreferences.js: React hook behind the Preferences page. It keeps the saved copy and the copy being edited
// (the draft), and says whether they differ. A signed-in user's preferences are loaded from and saved to the API
// (GET and PUT /me/preferences); a guest's are kept in the browser (localStorage). Right after login, if the account
// has nothing saved, the browser's choices are copied to the account; if it already has some, the account's win.
// Either way the browser copy is then removed, so it cannot carry over to another account on a shared browser.

import { useCallback, useEffect, useState } from "react";
import {
  EMPTY_PREFERENCES,
  clearLocal,
  decideAfterLogin,
  fetchPreferences,
  loadLocal,
  samePreferences,
  savePreferences,
  saveLocal,
} from "../api/preferences";

// Add the value to the list if it is missing, remove it if it is there.
const toggle = (list, value) => (list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

// signedIn: true for a Keycloak user, false for a guest.
export function usePreferences(signedIn) {
  // The last saved choices, and the choices on screen.
  const [saved, setSaved] = useState(EMPTY_PREFERENCES);
  const [draft, setDraft] = useState(EMPTY_PREFERENCES);
  // loading: the first load has not finished. loadFailed: the account's preferences could not be read (saving is
  // blocked then, so empty choices cannot overwrite what is saved). saving: a save is running. error: a message to show.
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Load once when the page opens (and again if the user signs in or out).
  useEffect(() => {
    let active = true; // ignore the result if the component went away meanwhile
    async function load() {
      setLoading(true);
      setError("");
      setLoadFailed(false);
      if (!signedIn) {
        const local = loadLocal();
        setSaved(local);
        setDraft(local);
        setLoading(false);
        return;
      }
      try {
        const account = await fetchPreferences();
        const browser = loadLocal();
        const action = decideAfterLogin(account, browser);
        let current = account;
        if (action === "copy") {
          try {
            current = await savePreferences(browser);
          } catch {
            // The copy failed: show the browser's choices as unsaved changes so the user can press Save, and keep them here.
            if (!active) return;
            setSaved(account);
            setDraft(browser);
            setError("We could not copy your choices to your account yet. Press Save preferences to try again.");
            setLoading(false);
            return;
          }
        }
        if (action !== "none") clearLocal();
        if (!active) return;
        setSaved(current);
        setDraft(current);
      } catch (e) {
        if (!active) return;
        setLoadFailed(true);
        setError(e.message || "Could not load your preferences.");
      }
      if (active) setLoading(false);
    }
    load();
    return () => {
      active = false;
    };
  }, [signedIn]);

  // Change one field of the draft.
  const toggleMode = useCallback((v) => setDraft((d) => ({ ...d, travel_mode: toggle(d.travel_mode, v) })), []);
  const toggleInterest = useCallback((v) => setDraft((d) => ({ ...d, interests: toggle(d.interests, v) })), []);
  const pickBudget = useCallback((v) => setDraft((d) => ({ ...d, budget: d.budget === v ? null : v })), []);
  const pickLanguage = useCallback((v) => setDraft((d) => ({ ...d, reply_language: d.reply_language === v ? null : v })), []);

  // Go back to the last saved choices.
  const reset = useCallback(() => {
    setDraft(saved);
    setError("");
  }, [saved]);

  // Save the draft: to the account (API) or, for a guest, to the browser.
  const save = useCallback(async () => {
    setSaving(true);
    setError("");
    try {
      if (signedIn) {
        const stored = await savePreferences(draft);
        setSaved(stored);
        setDraft(stored);
      } else if (saveLocal(draft)) {
        setSaved(draft);
      } else {
        setError("Your browser would not keep these choices (storage is blocked).");
      }
    } catch (e) {
      setError(e.message || "Could not save your preferences.");
    } finally {
      setSaving(false);
    }
  }, [signedIn, draft]);

  return {
    saved,
    draft,
    dirty: !samePreferences(saved, draft),
    loading,
    loadFailed,
    saving,
    error,
    toggleMode,
    toggleInterest,
    pickBudget,
    pickLanguage,
    reset,
    save,
  };
}
