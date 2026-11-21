// App.jsx: the app shell: the nav bar (public site only), the current page and the disclaimer.
// There is no router library. Two small pieces of state decide what to show: the URL path ("/" is the public
// site, "/app" is the app page, open to guests and to signed-in users, handled with the browser's History API) and,
// under "/", which page (the home page or the chat).
// The conversation (useChat) is held here so it survives switching pages.

import { useCallback, useEffect, useState } from "react";
import Disclaimer from "./components/Disclaimer";
import NavBar from "./components/NavBar";
import { useAuth } from "./hooks/useAuth";
import { useChat } from "./hooks/useChat";
import AppHome from "./pages/AppHome";
import Chat from "./pages/Chat";
import Home from "./pages/Home";

export default function App() {
  // The current URL path. It changes through navigate() below and the browser's Back and Forward buttons.
  const [path, setPath] = useState(window.location.pathname);
  // Which page is showing under "/": "home" or "chat" (the guest chat).
  const [page, setPage] = useState("home");
  // The conversation, shared by the Home hero input, the Chat page and the logged-in page.
  const { messages, pending, send, reset } = useChat();
  // Who is signed in (a Keycloak user, or null for a guest); ready = the session check has finished.
  const { user, ready, login, logout } = useAuth();
  // Text typed in the hero search box, handed to the chat input on /app (it is only pre-filled, never sent).
  // It lives in React state only: nothing is stored in the browser.
  const [draft, setDraft] = useState("");
  // True on the app page (/app).
  const isApp = path === "/app" || path === "/app/";

  // Move to another path without reloading the page (replace = overwrite the history entry instead of adding one).
  const navigate = useCallback((to, replace = false) => {
    window.history[replace ? "replaceState" : "pushState"]({}, "", to);
    setPath(to);
    if (to === "/") setPage("home");
  }, []);

  // Keep the path in step with the browser's Back and Forward buttons.
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Set when "Destinations" is clicked: the page scrolls to that section once the home page is on screen.
  const [scrollToDestinations, setScrollToDestinations] = useState(false);
  useEffect(() => {
    if (!scrollToDestinations || isApp || page !== "home") return;
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches; // no animation if the user asked for none
    document.getElementById("destinations")?.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
    setScrollToDestinations(false);
  }, [scrollToDestinations, isApp, page]);

  // Open the chat, and send the first message when the hero input or a destination card gave one.
  function startPlanning(text) {
    setPage("chat");
    if (typeof text === "string" && text.trim()) send(text);
  }

  // The logo: back to the top of the home page.
  function goHome() {
    setPage("home");
    document.querySelector(".page")?.scrollTo({ top: 0 });
  }

  // A hero chip or "Start planning" / "Continue as guest": go straight to /app, as a guest if nobody is logged in.
  // A chip's text is pre-filled in the chat input there.
  function planTrip(text) {
    setDraft(typeof text === "string" ? text : "");
    navigate("/app");
  }

  // "Log in" (the header, and the guest's button inside /app): go straight to Keycloak's login page, which sends
  // the user back to /app afterwards (keycloak.login with redirectUri, see auth/session.js). There is no pop-up and
  // no sign-up link here: Keycloak's login page has its own Register link. The password is typed only on Keycloak.
  function goToLogin() {
    login();
  }

  // What fills the middle of the screen.
  let content;
  if (isApp) {
    // Wait for the session check, so a logged-in user does not flash as a guest. user is null for a guest.
    content = ready ? (
      <AppHome
        user={user}
        messages={messages}
        pending={pending}
        onSend={send}
        onNewChat={reset}
        onLogin={goToLogin}
        onLogout={logout}
        draft={draft}
        onDraftUsed={() => setDraft("")}
      />
    ) : (
      <div className="placeholder">Loading...</div>
    );
  } else if (page === "home") {
    content = (
      <Home
        onStart={startPlanning}
        onPlan={planTrip}
        onGuest={() => planTrip()}
        user={user}
        onOpenApp={() => navigate("/app")}
      />
    );
  } else {
    content = <Chat messages={messages} pending={pending} onSend={send} />;
  }

  return (
    <div className="app">
      {/* The app page has its own sidebar, so the top bar is only for the public site. */}
      {!isApp && (
        <NavBar
          onHome={goHome}
          onDestinations={() => {
            setPage("home");
            setScrollToDestinations(true);
          }}
          onLogin={goToLogin}
          onLogout={logout}
          user={user}
          overHero={page === "home"}
        />
      )}

      <main className="page">{content}</main>

      <Disclaimer />
    </div>
  );
}
