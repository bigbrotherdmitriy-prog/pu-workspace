import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import {
  HEALTH_PATH, OFFLINE_RECHECK_MS, PROBE_TIMEOUT_MS, isOnline, markOfflineAndRecheck, recheckConnectivity,
  reportServerReachable,
  resetConnectivityForTests, startConnectivityMonitor, subscribeConnectivity,
} from "./connectivity";
import type { ServerConflict, SyncSummary } from "./types";

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

  it("shows a real offline at once on the offline event, before the probe returns", async () => {
    const stop = startConnectivityMonitor();
    reportServerReachable();
    const states: boolean[] = [];
    subscribeConnectivity((online) => states.push(online));
    let answer: (response: Response) => void = () => {};
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => { answer = resolve; }));
    setNavigatorOnline(false);
    window.dispatchEvent(new Event("offline"));
    // Offline is reported synchronously, while the probe is still pending.
    expect(isOnline()).toBe(false);
    expect(states).toEqual([false]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // The server still answers (VPN case): the probe clears the offline state.
    answer(new Response("{}", { status: 200 }));
    await vi.waitFor(() => expect(isOnline()).toBe(true));
    expect(states).toEqual([false, true]);
    stop();
  });

  it("keeps a confirmed offline when the probe fails after the offline event", async () => {
    reportServerReachable();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    setNavigatorOnline(false);
    await expect(markOfflineAndRecheck()).resolves.toBe(false);
    expect(isOnline()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// useOfflineSync lifetime regression coverage (PR459).
//
// These tests exercise the real hook against a mocked ./syncEngine module.
// ./connectivity is intentionally NOT mocked so the connectivity suite above
// keeps exercising the real module. IndexedDB availability is isolated per
// test and every mounted hook, timer and restored global is cleaned up.
// ---------------------------------------------------------------------------

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function summary(total: number): SyncSummary {
  return { queued: total, syncing: 0, attention: 0, authRequired: 0, conflicts: 0, total };
}

function conflict(id: number): ServerConflict {
  return {
    id,
    command_id: id,
    entity_type: "task",
    entity_id: id,
    base_values: {},
    local_values: {},
    server_values: {},
    conflicting_fields: [],
    server_record_version: 1,
    status: "unresolved",
  };
}

const syncEngineMock = vi.hoisted(() => ({
  CHANGE_EVENT: "offline-change",
  offlineSummary: vi.fn(),
  serverConflicts: vi.fn(),
  synchronize: vi.fn(),
  resolveConflict: vi.fn(),
}));

vi.mock("./syncEngine", () => syncEngineMock);

// Imported after the mock is registered.
import { useOfflineSync } from "./useOfflineSync";

const originalIndexedDB = Object.getOwnPropertyDescriptor(globalThis, "indexedDB");

function enableIndexedDB() {
  Object.defineProperty(globalThis, "indexedDB", {
    configurable: true,
    writable: true,
    value: {},
  });
}

function restoreIndexedDB() {
  if (originalIndexedDB) {
    Object.defineProperty(globalThis, "indexedDB", originalIndexedDB);
  } else {
    delete (globalThis as { indexedDB?: unknown }).indexedDB;
  }
}

// Flush pending microtasks without relying on real timers or polling.
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useOfflineSync lifetime", () => {
  beforeEach(() => {
    enableIndexedDB();
    reportServerReachable();
    syncEngineMock.offlineSummary.mockReset();
    syncEngineMock.serverConflicts.mockReset();
    syncEngineMock.synchronize.mockReset();
    syncEngineMock.resolveConflict.mockReset();
    syncEngineMock.offlineSummary.mockResolvedValue(summary(0));
    syncEngineMock.serverConflicts.mockResolvedValue([]);
    syncEngineMock.synchronize.mockResolvedValue(undefined);
    syncEngineMock.resolveConflict.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    restoreIndexedDB();
    vi.clearAllMocks();
  });

  it("does not let an old-scope summary overwrite state after a user switch", async () => {
    const oldSummary = deferred<SyncSummary>();
    syncEngineMock.offlineSummary.mockImplementation((userId: number) => {
      if (userId === 1) return oldSummary.promise;
      return Promise.resolve(summary(2));
    });

    const onApplied = vi.fn();
    const { result, rerender } = renderHook(
      ({ userId, projectId }: { userId: number; projectId: number }) =>
        useOfflineSync(userId, projectId, onApplied),
      { initialProps: { userId: 1, projectId: 1 } },
    );
    await flush();

    rerender({ userId: 2, projectId: 1 });
    await flush();
    expect(result.current.summary.total).toBe(2);

    await act(async () => {
      oldSummary.resolve(summary(9));
      await oldSummary.promise;
    });
    await flush();

    expect(result.current.summary.total).toBe(2);
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("does not revive the first A scope after an A -> B -> A switch", async () => {
    const firstA = deferred<SyncSummary>();
    let aCalls = 0;
    syncEngineMock.offlineSummary.mockImplementation((userId: number) => {
      if (userId === 1) {
        aCalls += 1;
        if (aCalls === 1) return firstA.promise;
        return Promise.resolve(summary(3));
      }
      return Promise.resolve(summary(5));
    });

    const onApplied = vi.fn();
    const { result, rerender } = renderHook(
      ({ userId }: { userId: number }) => useOfflineSync(userId, 1, onApplied),
      { initialProps: { userId: 1 } },
    );
    await flush();

    rerender({ userId: 2 });
    await flush();
    expect(result.current.summary.total).toBe(5);

    rerender({ userId: 1 });
    await flush();
    expect(result.current.summary.total).toBe(3);

    await act(async () => {
      firstA.resolve(summary(7));
      await firstA.promise;
    });
    await flush();

    expect(result.current.summary.total).toBe(3);
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("does not let an older same-scope refresh overwrite a newer one", async () => {
    const older = deferred<SyncSummary>();
    const newer = deferred<SyncSummary>();
    let calls = 0;
    syncEngineMock.offlineSummary.mockImplementation(() => {
      calls += 1;
      if (calls === 1) return older.promise;
      if (calls === 2) return newer.promise;
      return Promise.resolve(summary(4));
    });

    const onApplied = vi.fn();
    const { result } = renderHook(() => useOfflineSync(1, 1, onApplied));
    await flush();

    // Trigger a second refresh through the public event path.
    await act(async () => {
      window.dispatchEvent(new Event(syncEngineMock.CHANGE_EVENT));
    });
    await flush();

    await act(async () => {
      newer.resolve(summary(4));
      await newer.promise;
    });
    await flush();
    expect(result.current.summary.total).toBe(4);

    await act(async () => {
      older.resolve(summary(8));
      await older.promise;
    });
    await flush();

    expect(result.current.summary.total).toBe(4);
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("does not let a stale conflict fetch overwrite a newer conflict list", async () => {
    const olderConflicts = deferred<ServerConflict[]>();
    const newerConflicts = deferred<ServerConflict[]>();
    let calls = 0;
    syncEngineMock.serverConflicts.mockImplementation(() => {
      calls += 1;
      if (calls === 1) return olderConflicts.promise;
      if (calls === 2) return newerConflicts.promise;
      return Promise.resolve([]);
    });

    const onApplied = vi.fn();
    const { result } = renderHook(() => useOfflineSync(1, 1, onApplied));
    await flush();

    await act(async () => {
      window.dispatchEvent(new Event(syncEngineMock.CHANGE_EVENT));
    });
    await flush();

    await act(async () => {
      newerConflicts.resolve([conflict(2)]);
      await newerConflicts.promise;
    });
    await flush();
    expect(result.current.conflicts.map((c) => c.id)).toEqual([2]);

    await act(async () => {
      olderConflicts.resolve([conflict(1)]);
      await olderConflicts.promise;
    });
    await flush();

    expect(result.current.conflicts.map((c) => c.id)).toEqual([2]);
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("does not invoke onApplied or follow-on work for a pending syncNow after a scope switch", async () => {
    const pendingBefore = deferred<SyncSummary>();
    let user1SummaryCalls = 0;
    syncEngineMock.offlineSummary.mockImplementation((userId: number) => {
      if (userId === 1) {
        user1SummaryCalls += 1;
        if (user1SummaryCalls === 1) return pendingBefore.promise;
        return Promise.resolve(summary(0));
      }
      return Promise.resolve(summary(0));
    });

    const onApplied = vi.fn();
    const { rerender } = renderHook(
      ({ userId }: { userId: number }) => useOfflineSync(userId, 1, onApplied),
      { initialProps: { userId: 1 } },
    );
    await flush();

    rerender({ userId: 2 });
    await flush();

    await act(async () => {
      pendingBefore.resolve(summary(5));
      await pendingBefore.promise;
    });
    await flush();

    expect(onApplied).not.toHaveBeenCalled();
    // The old scope must not start a synchronize follow-on.
    expect(syncEngineMock.synchronize).not.toHaveBeenCalledWith(1, 1, expect.anything());
  });

  it("does not invoke onApplied for a pending resolve after a scope switch", async () => {
    const pendingResolve = deferred<void>();
    syncEngineMock.resolveConflict.mockImplementation(() => pendingResolve.promise);

    const onApplied = vi.fn();
    const { result, rerender } = renderHook(
      ({ userId }: { userId: number }) => useOfflineSync(userId, 1, onApplied),
      { initialProps: { userId: 1 } },
    );
    await flush();

    let resolvePromise: Promise<void> = Promise.resolve();
    await act(async () => {
      resolvePromise = result.current.resolve(1, "keep_server");
    });

    rerender({ userId: 2 });
    await flush();

    await act(async () => {
      pendingResolve.resolve();
      await resolvePromise;
    });
    await flush();

    expect(onApplied).not.toHaveBeenCalled();
  });

  it("suppresses follow-on work after unmount", async () => {
    const pendingBefore = deferred<SyncSummary>();
    let calls = 0;
    syncEngineMock.offlineSummary.mockImplementation(() => {
      calls += 1;
      if (calls === 1) return pendingBefore.promise;
      return Promise.resolve(summary(0));
    });

    const onApplied = vi.fn();
    const { unmount } = renderHook(() => useOfflineSync(1, 1, onApplied));
    await flush();

    unmount();

    await act(async () => {
      pendingBefore.resolve(summary(5));
      await pendingBefore.promise;
    });
    await flush();

    expect(onApplied).not.toHaveBeenCalled();
    expect(syncEngineMock.synchronize).not.toHaveBeenCalled();
  });

  it("invokes onApplied for a current-scope sync that reduces the total", async () => {
    let total = 5;
    syncEngineMock.offlineSummary.mockImplementation(() => Promise.resolve(summary(total)));
    syncEngineMock.synchronize.mockImplementation(async () => { total = 0; });

    const onApplied = vi.fn();
    const { result } = renderHook(() => useOfflineSync(1, 1, onApplied));
    await flush();

    await act(async () => {
      await result.current.syncNow(true);
    });
    await flush();

    expect(syncEngineMock.synchronize).toHaveBeenCalledWith(1, 1, true);
    expect(onApplied).toHaveBeenCalledTimes(1);
  });
});
