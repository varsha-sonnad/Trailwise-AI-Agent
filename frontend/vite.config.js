// vite.config.js: settings for the Vite dev server and build.
// The dev server must run on port 5173: that is the one address the backend allows (CORS in backend/app/config.py).

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Lets Vite understand .jsx files and React's fast refresh.
  plugins: [react()],
  server: {
    host: "localhost",
    port: 5173,
    // Fail instead of silently picking another port, which the backend's CORS would reject.
    strictPort: true,
  },
});
