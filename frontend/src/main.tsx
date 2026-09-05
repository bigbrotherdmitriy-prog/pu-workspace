import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AppErrorBoundary } from "./AppErrorBoundary";
import "./styles.css";
import "./source.css";
import "./brand.css";
import "./metric-actions.css";
import "./designer.css";
import "./interface-v4.css";
import "./interface-v5-neon.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode><AppErrorBoundary><App /></AppErrorBoundary></React.StrictMode>,
);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/new/service-worker.js", { scope: "/new/", updateViaCache: "none" }).then((registration) => {
      void registration.update();
    }).catch(() => {
      // Offline support is optional; a failed registration must not block the app.
    });
  });
}
