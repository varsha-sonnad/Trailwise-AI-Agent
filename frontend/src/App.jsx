// App.jsx: the app shell: the header with the page switcher, the current page, and the disclaimer.
// There is no router library yet: a small piece of state decides which page to show.

import { useState } from "react";
import Disclaimer from "./components/Disclaimer";
import Home from "./pages/Home";
import Login from "./pages/Login";
import SavedTrips from "./pages/SavedTrips";

// The pages, in the order of the buttons. Login and SavedTrips are placeholders for now.
const PAGES = [
  { id: "home", label: "Chat", Component: Home },
  { id: "trips", label: "Saved trips", Component: SavedTrips },
  { id: "login", label: "Log in", Component: Login },
];

export default function App() {
  const [pageId, setPageId] = useState("home");
  const { Component } = PAGES.find((p) => p.id === pageId);

  return (
    <main className="app">
      <header className="app-header">
        <h1>Trailwise</h1>
        <p>Your AI travel planner. Planning and information only: no booking or payments.</p>
        <nav className="nav" aria-label="Pages">
          {PAGES.map((p) => (
            <button key={p.id} type="button" onClick={() => setPageId(p.id)} aria-current={p.id === pageId ? "page" : undefined}>
              {p.label}
            </button>
          ))}
        </nav>
      </header>

      {/* The chat is kept mounted so switching pages does not lose the conversation. */}
      <div className="page" hidden={pageId !== "home"}>
        <Home />
      </div>
      {pageId !== "home" && (
        <div className="page">
          <Component />
        </div>
      )}

      <Disclaimer />
    </main>
  );
}
