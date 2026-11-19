// useAuth.js: React hook that tells the UI who is signed in (a Keycloak user or nobody/guest)
// and gives it login and logout actions. The real work is in auth/session.js.

import { useEffect, useState } from "react";
import { initAuth, login, logout, register } from "../auth/session";

export function useAuth() {
  // The signed-in user ({ name, email }) or null for a guest.
  const [user, setUser] = useState(null);
  // True once the quiet "is there already a session?" check has finished.
  const [ready, setReady] = useState(false);

  // Run the session check once when the app opens.
  useEffect(() => {
    let active = true; // ignore the result if the component was removed meanwhile
    initAuth().then((u) => {
      if (!active) return;
      setUser(u);
      setReady(true);
    });
    return () => {
      active = false;
    };
  }, []);

  return { user, ready, login, logout, register };
}
