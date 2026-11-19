// NavBar.jsx: the public site's top bar. Three items: "Destinations" (scrolls to that section), "Plan a Trip" and
// "Log in", with the Trailwise logo on the left. "Plan a Trip" and "Log in" both open the login pop-up (a signed-in
// visitor sees "Hi, name" and "Log out", and "Plan a Trip" opens the app). On narrow screens the items fold into
// a menu button. It only reports clicks; App.jsx decides what happens.

import { useState } from "react";

// onHome(): the logo. onDestinations(): scroll to the destinations section. onPlanTrip(): "Plan a Trip".
// onLogin() / onLogout(): the account buttons. user: the signed-in user ({ name }) or null.
// overHero: draw the bar transparent, with dark text, on top of the sunset home hero.
export default function NavBar({ onHome, onDestinations, onPlanTrip, onLogin, onLogout, overHero, user }) {
  // Whether the folded menu is open (only matters on narrow screens).
  const [menuOpen, setMenuOpen] = useState(false);

  // Run an action and fold the menu again.
  function pick(action) {
    setMenuOpen(false);
    action();
  }

  return (
    <header className={`navbar${overHero ? " over-hero" : ""}`}>
      <button type="button" className="brand" onClick={() => pick(onHome)}>
        <span aria-hidden="true">☀</span> Trailwise
      </button>

      <nav id="nav-links" className={`nav-links${menuOpen ? " open" : ""}`} aria-label="Main">
        <button type="button" className="nav-link" onClick={() => pick(onDestinations)}>
          Destinations
        </button>
        <button type="button" className="nav-link cta" onClick={() => pick(onPlanTrip)}>
          Plan a Trip
        </button>
        {user ? (
          <>
            <span className="nav-user">Hi, {user.name.split(" ")[0]}</span>
            <button type="button" className="nav-link" onClick={() => pick(onLogout)}>
              Log out
            </button>
          </>
        ) : (
          <button type="button" className="nav-link" onClick={() => pick(onLogin)}>
            Log in
          </button>
        )}
      </nav>

      <button
        type="button"
        className="menu-toggle"
        aria-label="Menu"
        aria-expanded={menuOpen}
        aria-controls="nav-links"
        onClick={() => setMenuOpen((open) => !open)}
      >
        ☰
      </button>
    </header>
  );
}
