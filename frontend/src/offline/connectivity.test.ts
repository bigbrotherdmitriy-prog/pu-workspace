import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  HEALTH_PATH, OFFLINE_RECHECK_MS, PROBE_TIMEOUT_MS, isOnline, recheckConnectivity, reportServerReachable,
  resetConnectivityForTests, startConnectivityMonitor, subscribeConnectivity,
} from "./connectivity";

function setNavigatorOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, value });
}

function setVisibility(value: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value });
}

const fetchMock = vi.fn();

beforeEach(() => {
  resetConnectivityForTests();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setNavigatorOnline(true);
  setVisibility("visible");
});

afterEach(() => {
  resetConnectivityForTests();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setNavigatorOnline(true);
});

describe("connectivity", () => {
  it("stays online without probing when the browser reports online", async () => {
    await expect(recheckConnectivity()).resolves.toBe(true);
    expect(isOnline()).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats navigator.onLine=false as online when the health probe succeeds", async () => {
    setNavigatorOnline(false);
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await expect(recheckConnectivity()).resolves.toBe(true);
    expect(isOnline()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url).startsWith(`${HEALTH_PATH}?`)).toBe(true);
    expect(init).toMatchObject({ cache: "no-store", method: "GET" });
  });

  it("goes offline only when navigator.onLine=false and the probe fails", async () => {
    setNavigatorOnline(false);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const listener = vi.fn();
    subscribeConnectivity(listener);
    await expect(recheckConnectivity()).resolves.toBe(false);
    expect(isOnline()).toBe(false);
    expect(listener).toHaveBeenLastCalledWith(false);
  });

  it("treats a hanging probe as offline after the timeout", async () => {
    vi.useFakeTimers();
    setNavigatorOnline(false);
    fetchMock.mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const result = recheckConnectivity();
    await vi.advanceTimersByTimeAsync(PROBE_TIMEOUT_MS);
    await expect(result).resolves.toBe(false);
    expect(isOnline()).toBe(false);
  });

  it("clears the offline state as soon as any API request succeeds", async () => {
    setNavigatorOnline(false);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await recheckConnectivity();
    const listener = vi.fn();
    subscribeConnectivity(listener);
    reportServerReachable();
    expect(isOnline()).toBe(true);
    expect(listener).toHaveBeenCalledWith(true);
  });

  it("recovers when the page becomes visible again", async () => {
    setNavigatorOnline(false);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const listener = vi.fn();
    subscribeConnectivity(listener);
    const stop = startConnectivityMonitor();
    await vi.waitFor(() => expect(listener).toHaveBeenLastCalledWith(false));

    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.waitFor(() => expect(isOnline()).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    stop();
  });

  it("re-checks on the offline event and periodically only while offline", async () => {
    vi.useFakeTimers();
    const stop = startConnectivityMonitor();
    await vi.advanceTimersByTimeAsync(OFFLINE_RECHECK_MS * 3);
    expect(fetchMock).not.toHaveBeenCalled();

    setNavigatorOnline(false);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    window.dispatchEvent(new Event("offline"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(isOnline()).toBe(false);

    await vi.advanceTimersByTimeAsync(OFFLINE_RECHECK_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await vi.advanceTimersByTimeAsync(OFFLINE_RECHECK_MS);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(isOnline()).toBe(true);

    await vi.advanceTimersByTimeAsync(OFFLINE_RECHECK_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    stop();
  });

  it("returns to online on the online event without probing", async () => {
    setNavigatorOnline(false);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const stop = startConnectivityMonitor();
    await vi.waitFor(() => expect(isOnline()).toBe(false));
    setNavigatorOnline(true);
    window.dispatchEvent(new Event("online"));
    expect(isOnline()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    stop();
  });
});
