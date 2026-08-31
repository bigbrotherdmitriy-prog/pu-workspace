import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import "./source.css";
import "./brand.css";
import "./metric-actions.css";
import "./designer.css";

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/new/service-worker.js", { scope: "/new/" }).catch(() => {
      // Offline support is optional; a failed registration must not block the app.
    });
  });
}
