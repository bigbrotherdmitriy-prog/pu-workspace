import React from "react";
import { createRoot } from "react-dom/client";
import { ProjectSiteLocationCard } from "../../src/modules/projects/ProjectSiteLocationCard";
import "../../src/styles.css";
import "../../src/brand.css";

const site = {
  configured: true,
  latitude: 55.7558,
  longitude: 37.6173,
  label: "Стройплощадка · корпус 2",
  radius_m: 250,
  accuracy_m: 9,
};

globalThis.fetch = async () => new Response(JSON.stringify(site), {
  status: 200,
  headers: { "Content-Type": "application/json", "X-Request-ID": "gps-layout" },
});

Object.defineProperty(navigator, "geolocation", {
  configurable: true,
  value: {
    getCurrentPosition(success: PositionCallback) {
      success({
        coords: {
          latitude: 55.7562,
          longitude: 37.6173,
          accuracy: 8,
          altitude: null,
          altitudeAccuracy: null,
          heading: null,
          speed: null,
          toJSON: () => ({}),
        },
        timestamp: Date.now(),
        toJSON: () => ({}),
      });
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <div className="today-page" style={{ maxWidth: 1180, margin: "32px auto", padding: "0 16px" }}>
    <ProjectSiteLocationCard projectId={17} />
  </div>,
);
