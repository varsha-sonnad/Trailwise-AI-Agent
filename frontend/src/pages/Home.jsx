// Home.jsx: the public landing page: a sunset hero (serif headline, chat box, amber button) and a
// "Popular destinations" section of image cards. "Plan it" opens the app page (/app) as a guest with the typed text
// pre-filled; clicking a card opens the guest chat with a first message (App.jsx does the hand-over through onPlan
// and onStart). The cards' pictures are small illustrations drawn here in SVG,
// so there are no photo files, licences or outside requests.

import { useState } from "react";
import { MAX_MESSAGE_CHARS } from "../api/chat";

// The illustrations: each is the inside of a 300 x 180 picture (sky, shapes). Ids are unique per picture.
// Colors are only decoration; the cards contain no prices, times or other travel data.
const SCENES = {
  goa: (
    <>
      <defs>
        <linearGradient id="sky-goa" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ff9a6e" />
          <stop offset="0.6" stopColor="#ffd37a" />
          <stop offset="1" stopColor="#ffe8c8" />
        </linearGradient>
      </defs>
      <rect width="300" height="180" fill="url(#sky-goa)" />
      <circle cx="210" cy="100" r="30" fill="#fff3c4" />
      <path d="M0 110 H300 V180 H0Z" fill="#2f7fa8" />
      <path d="M0 128 Q25 120 50 128 T100 128 T150 128 T200 128 T250 128 T300 128 V180 H0Z" fill="#256c92" />
      <path d="M0 150 Q25 142 50 150 T100 150 T150 150 T200 150 T250 150 T300 150 V180 H0Z" fill="#1c5a7d" />
      <path d="M60 180 Q64 130 70 95" stroke="#3a2a1a" strokeWidth="5" fill="none" strokeLinecap="round" />
      <path d="M70 95 Q50 80 35 92 M70 95 Q85 74 104 84 M70 95 Q66 72 52 66 M70 95 Q90 92 100 108" stroke="#2c6b3a" strokeWidth="5" fill="none" strokeLinecap="round" />
    </>
  ),
  jaipur: (
    <>
      <defs>
        <linearGradient id="sky-jaipur" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#f6a6a0" />
          <stop offset="1" stopColor="#ffe1cf" />
        </linearGradient>
      </defs>
      <rect width="300" height="180" fill="url(#sky-jaipur)" />
      <path d="M0 150 H300 V180 H0Z" fill="#c9766a" />
      <path d="M40 150 V92 H260 V150Z" fill="#d9776b" />
      <path d="M40 92 V80 H60 V92 M90 92 V74 H110 V92 M140 92 V62 H160 V92 M190 92 V74 H210 V92 M240 92 V80 H260 V92" fill="#c3604f" />
      <path d="M130 62 Q150 30 170 62Z M80 74 Q100 52 120 74Z M180 74 Q200 52 220 74Z" fill="#e9967f" />
      <path d="M135 150 V118 Q150 100 165 118 V150Z" fill="#7a2f2a" />
      <path d="M62 150 V120 Q72 108 82 120 V150Z M218 150 V120 Q228 108 238 120 V150Z" fill="#9a423a" />
    </>
  ),
  kerala: (
    <>
      <defs>
        <linearGradient id="sky-kerala" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#bfe6d8" />
          <stop offset="1" stopColor="#f3f8e4" />
        </linearGradient>
      </defs>
      <rect width="300" height="180" fill="url(#sky-kerala)" />
      <path d="M0 105 H300 V180 H0Z" fill="#3f9a8a" />
      <path d="M0 118 Q40 110 80 118 T160 118 T240 118 T300 118 V180 H0Z" fill="#33887a" />
      <path d="M0 100 Q40 80 90 100 T190 100 T300 96 V108 H0Z" fill="#4c9a4a" />
      <path d="M95 150 Q150 170 205 150 L195 142 H105Z" fill="#7a4a2a" />
      <path d="M120 142 V116 H180 V142Z" fill="#e8c27a" />
      <path d="M114 116 H186 L150 100Z" fill="#b8782f" />
      <path d="M255 110 Q258 70 262 50 M262 50 Q244 40 232 52 M262 50 Q280 40 292 54 M262 50 Q258 34 246 30" stroke="#2c6b3a" strokeWidth="4" fill="none" strokeLinecap="round" />
    </>
  ),
  varanasi: (
    <>
      <defs>
        <linearGradient id="sky-varanasi" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#5a4a9a" />
          <stop offset="0.6" stopColor="#e98a6c" />
          <stop offset="1" stopColor="#ffd08a" />
        </linearGradient>
      </defs>
      <rect width="300" height="180" fill="url(#sky-varanasi)" />
      <circle cx="80" cy="82" r="22" fill="#ffe6a8" />
      <path d="M0 150 H300 V180 H0Z" fill="#3b4f8a" />
      <path d="M0 120 H300 V150 H0Z" fill="#8a5a4a" />
      <path d="M0 130 H300 M0 140 H300" stroke="#6e4538" strokeWidth="3" />
      <path d="M150 120 V84 H210 V120Z" fill="#a8644f" />
      <path d="M156 84 Q180 40 204 84Z" fill="#c47a5c" />
      <path d="M180 40 V28" stroke="#c47a5c" strokeWidth="3" />
      <path d="M215 120 V96 H255 V120Z" fill="#935544" />
      <path d="M220 96 Q235 66 250 96Z" fill="#b66e56" />
    </>
  ),
  ladakh: (
    <>
      <defs>
        <linearGradient id="sky-ladakh" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#4f8fd6" />
          <stop offset="1" stopColor="#cfe6f7" />
        </linearGradient>
      </defs>
      <rect width="300" height="180" fill="url(#sky-ladakh)" />
      <path d="M0 140 L70 60 L110 100 L170 30 L240 110 L300 70 V180 H0Z" fill="#7d8aa0" />
      <path d="M170 30 L150 58 L165 52 L172 64 L184 50 L200 64Z M70 60 L55 80 L68 74 L76 84 L90 76Z" fill="#ffffff" />
      <path d="M0 150 Q80 120 160 148 T300 140 V180 H0Z" fill="#b08a62" />
      <path d="M0 168 Q90 148 170 166 T300 160 V180 H0Z" fill="#8f6c48" />
      <path d="M205 150 V128 H235 V150Z" fill="#f3ead8" />
      <path d="M205 128 H235 L220 116Z" fill="#c0463a" />
    </>
  ),
  hampi: (
    <>
      <defs>
        <linearGradient id="sky-hampi" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#f2b56a" />
          <stop offset="1" stopColor="#fbe3b8" />
        </linearGradient>
      </defs>
      <rect width="300" height="180" fill="url(#sky-hampi)" />
      <circle cx="235" cy="58" r="22" fill="#fff3c4" />
      <path d="M0 150 H300 V180 H0Z" fill="#9a7048" />
      <ellipse cx="60" cy="140" rx="46" ry="30" fill="#b98a5a" />
      <ellipse cx="120" cy="146" rx="38" ry="24" fill="#a77a4e" />
      <ellipse cx="45" cy="120" rx="26" ry="20" fill="#c79a68" />
      <path d="M170 150 V96 H250 V150Z" fill="#b8814f" />
      <path d="M178 96 V80 H242 V96 M188 80 V66 H232 V80 M198 66 V54 H222 V66 M208 54 V44 H212 V54" fill="#c9915c" stroke="#8f6238" strokeWidth="2" />
      <path d="M200 150 V118 H220 V150Z" fill="#5d3b22" />
    </>
  ),
};

// The six cards: name, one-line description, and which picture to draw.
const DESTINATIONS = [
  { id: "goa", name: "Goa", blurb: "Beaches, old churches and relaxed seaside evenings." },
  { id: "jaipur", name: "Jaipur", blurb: "The Pink City: forts, palaces and busy bazaars." },
  { id: "kerala", name: "Kerala backwaters", blurb: "Quiet canals, houseboats and green rice fields." },
  { id: "varanasi", name: "Varanasi", blurb: "Ghats on the Ganges, temples and evening aarti." },
  { id: "ladakh", name: "Ladakh", blurb: "High mountain passes, monasteries and wide open skies." },
  { id: "hampi", name: "Hampi", blurb: "Ancient ruins and boulder-strewn hills by the river." },
];

// onStart(text?): open the chat; when text is given it is sent as the first message (used by the destination cards).
// onPlan(text): "Plan it" in the hero: go to /app with the typed text pre-filled in the chat input.
// user / onOpenApp(): when someone is signed in, a link into the app page (/app) is shown.
export default function Home({ onStart, onPlan, user, onOpenApp }) {
  const [text, setText] = useState("");

  // Take the typed text to the app page (it is pre-filled there, not sent) and clear the box.
  function submit(event) {
    event.preventDefault(); // stop the page from reloading
    if (!text.trim()) return;
    onPlan(text);
    setText("");
  }

  return (
    <>
      <section className="hero">
        <div className="hero-inner">
          {/* Two fixed lines. On a narrow phone a line may wrap again, and the last two words stay together. */}
          <h2>
            <span className="hero-line">Tell us the dream.</span>
            <span className="hero-line">We’ll plan the{"\u00a0"}trail.</span>
          </h2>
          <p className="hero-sub">Chat about where you want to go, and get weather, routes and a day-by-day plan.</p>
          <form className="hero-form" onSubmit={submit}>
            <input
              type="text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={MAX_MESSAGE_CHARS}
              placeholder="Ask Trailwise"
              aria-label="Where do you want to go?"
            />
            <button type="submit" disabled={!text.trim()}>
              Plan it
            </button>
          </form>
          {user && (
            <button type="button" className="hero-app-link" onClick={onOpenApp}>
              Go to your app, {user.name.split(" ")[0]} →
            </button>
          )}
        </div>
      </section>

      {/* The nav bar's "Destinations" scrolls here (App.jsx looks the section up by this id). */}
      <section id="destinations" className="dest" aria-labelledby="dest-title">
        <h2 id="dest-title">Popular destinations</h2>
        <p className="dest-sub">Pick a place and Trailwise will help you plan it.</p>
        <div className="dest-grid">
          {DESTINATIONS.map((d) => (
            <button key={d.id} type="button" className="dest-card" onClick={() => onStart(`I'd like to plan a trip to ${d.name}`)}>
              <svg className="dest-art" viewBox="0 0 300 180" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
                {SCENES[d.id]}
              </svg>
              <span className="dest-body">
                <span className="dest-name">{d.name}</span>
                <span className="dest-blurb">{d.blurb}</span>
              </span>
            </button>
          ))}
        </div>
      </section>
    </>
  );
}
