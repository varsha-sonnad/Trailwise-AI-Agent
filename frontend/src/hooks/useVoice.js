// useVoice.js: React hook for voice input using the browser's built-in speech recognition.
// Languages from the plan: English, Hindi and Kannada. No audio leaves the app code: the browser does
// the recognition. Support is uneven (best in Chrome, weakest for Kannada), so callers must offer
// typing as a fallback; `supported` says whether voice can be used at all.

import { useCallback, useEffect, useRef, useState } from "react";

// The languages the user can speak, as BCP 47 codes the browser understands.
export const VOICE_LANGUAGES = [
  { code: "en-IN", label: "English" },
  { code: "hi-IN", label: "हिन्दी" },
  { code: "kn-IN", label: "ಕನ್ನಡ" },
];

// The browser's recognition class, under its standard or its prefixed name (undefined if unsupported).
const Recognition = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : undefined;

// Map the browser's error codes to short messages for the user.
function describeVoiceError(code) {
  if (code === "not-allowed" || code === "service-not-allowed") return "Microphone access was blocked. Allow it in the browser, or type instead.";
  if (code === "no-speech") return "I did not hear anything. Try again, or type instead.";
  if (code === "language-not-supported") return "This browser cannot recognise that language. Please type instead.";
  if (code === "network") return "Voice input needs an internet connection. Please type instead.";
  return "Voice input failed. Please type instead.";
}

// lang: the BCP 47 code to listen for. onResult(text): called with the recognised text.
export function useVoice({ lang, onResult }) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  // The running recognition object, so we can stop it.
  const recognitionRef = useRef(null);
  // Always call the latest onResult without restarting the recognition.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  // Start listening for one phrase.
  const start = useCallback(() => {
    if (!Recognition || recognitionRef.current) return;
    setError("");
    const recognition = new Recognition();
    recognition.lang = lang;
    recognition.interimResults = false; // only final text
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const text = event.results[0]?.[0]?.transcript;
      if (text) onResultRef.current(text);
    };
    recognition.onerror = (event) => setError(describeVoiceError(event.error));
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
    };
    recognitionRef.current = recognition;
    setListening(true);
    recognition.start();
  }, [lang]);

  // Stop listening (the browser then delivers any text it already heard).
  const stop = useCallback(() => recognitionRef.current?.stop(), []);

  // Make sure the microphone is released when the component goes away.
  useEffect(() => () => recognitionRef.current?.abort(), []);

  return { supported: Boolean(Recognition), listening, error, start, stop };
}
