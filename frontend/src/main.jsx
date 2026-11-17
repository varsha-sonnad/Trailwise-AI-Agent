// main.jsx: the starting point of the React app. It mounts <App /> into the #root element of index.html.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  // StrictMode runs extra checks during development only.
  <StrictMode>
    <App />
  </StrictMode>,
);
