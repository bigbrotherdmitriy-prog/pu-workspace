import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectSiteLocationCard, distanceMeters } from "./ProjectSiteLocationCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

type SiteLocation = {
  configured: boolean;
  latitude: number | null;
  longitude: number | null;
  label: string | null;
  radius_m: number | null;
  accuracy_m: number | null;
};

const configuredSite: SiteLocation = {
  configured: true,
  latitude: 55.7558,
  longitude: 37.6173,
  label: "Стройплощадка",
  radius_m: 150,
  accuracy_m: 12,
};

function response(body: SiteLocation) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => body,
  } as Response);
}

function position(latitude: number, longitude: number, accuracy = 8): GeolocationPosition {
  return {
    coords: {
      latitude,
      longitude,
      accuracy,
      altitude: null,
      altitudeAccuracy: null,
      heading: null,
      speed: null,
      toJSON: () => ({}),
    },
    timestamp: Date.now(),
    toJSON: () => ({}),
  };
}

function installGeolocation(
  implementation: (success: PositionCallback, error?: PositionErrorCallback | null) => void,
) {
  const getCurrentPosition = vi.fn(implementation);
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: { getCurrentPosition },
  });
  return getCurrentPosition;
}

describe("project site GPS", () => {
  it("calculates haversine distance in metres", () => {
    expect(distanceMeters(55.7558, 37.6173, 55.7567, 37.6173)).toBeCloseTo(100.1, 0);
    expect(distanceMeters(55.7558, 37.6173, 55.7558, 37.6173)).toBe(0);
  });

  it("checks whether the user is on site without persisting their live position", async () => {
    const fetchMock = vi.fn((_: string | URL | Request, _options?: RequestInit) => response(configuredSite));
    vi.stubGlobal("fetch", fetchMock);
    const geolocation = installGeolocation((success) => success(position(55.7562, 37.6173)));

    render(<ProjectSiteLocationCard projectId={17} />);
    expect(await screen.findByText("Стройплощадка")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /проверить.*местоположение/i }));

    await waitFor(() => expect(geolocation).toHaveBeenCalledOnce());
    expect(await screen.findByText(/на объекте/i)).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "PUT")).toHaveLength(0);
  });

  it("persists a captured position only after the explicit set-site action", async () => {
    const unconfigured: SiteLocation = {
      configured: false,
      latitude: null,
      longitude: null,
      label: null,
      radius_m: null,
      accuracy_m: null,
    };
    const fetchMock = vi.fn((_: string | URL | Request, options?: RequestInit) => {
      if (options?.method === "PUT") return response(configuredSite);
      return response(unconfigured);
    });
    vi.stubGlobal("fetch", fetchMock);
    installGeolocation((success) => success(position(55.7558, 37.6173, 12)));

    render(<ProjectSiteLocationCard projectId={17} />);
    const setButton = await screen.findByRole("button", { name: /установить.*текущ/i });
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "PUT")).toHaveLength(0);
    fireEvent.click(setButton);

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, options]) => options?.method === "PUT");
      expect(put).toBeDefined();
      expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({
        latitude: 55.7558,
        longitude: 37.6173,
        accuracy_m: 12,
      });
    });
  });

  it("shows a recoverable permission-denied state", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(configuredSite)));
    installGeolocation((_, error) => error?.({
      code: 1,
      message: "denied",
      PERMISSION_DENIED: 1,
      POSITION_UNAVAILABLE: 2,
      TIMEOUT: 3,
    }));

    render(<ProjectSiteLocationCard projectId={17} />);
    fireEvent.click(await screen.findByRole("button", { name: /проверить.*местоположение/i }));

    expect(await screen.findByText(/доступ.*геопозици.*запрещён/i)).toBeInTheDocument();
  });

  it("explains when browser geolocation is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(configuredSite)));
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: undefined });

    render(<ProjectSiteLocationCard projectId={17} />);

    expect(await screen.findByText(/геопозици.*не поддерживается/i)).toBeInTheDocument();
  });
});
