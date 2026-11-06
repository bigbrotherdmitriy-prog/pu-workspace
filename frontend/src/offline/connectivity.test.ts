import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement, StrictMode } from "react";
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
// useOfflineSync lifetime regression coverage (PR459 / PR470 / PR471).
//
// These tests exercise the real hook against a mocked ./syncEngine module.
// ./connectivity is intentionally NOT mocked so the connectivity suite above
// keeps exercising the real module. IndexedDB availability is isolated per
// test and every mounted hook, timer and restored global is cleaned up.
//
// Sequencing note: the hook fires an initial refresh() AND an initial
// syncNow(false) on mount. Both consume offlineSummary/serverConflicts. Tests
// therefore let the mount effects fully settle with default responses BEFORE
// arming deferred mocks or recording baselines, so that only explicitly
// triggered work is measured.
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

// Dispatch a CHANGE_EVENT refresh and let the resulting microtasks settle.
async function changeEvent() {
  await act(async () => {
    window.dispatchEvent(new Event(syncEngineMock.CHANGE_EVENT));
  });
  await flush();
}

// Resolve a deferred inside act and await its settlement.
async function settle<T>(d: Deferred<T>, value: T) {
  await act(async () => {
    d.resolve(value);
    await d.promise;
  });
  await flush();
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

    await settle(oldSummary, summary(9));

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

    await settle(firstA, summary(7));

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
    await changeEvent();

    await settle(newer, summary(4));
    expect(result.current.summary.total).toBe(4);

    await settle(older, summary(8));

    expect(result.current.summary.total).toBe(4);
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("does not let a stale conflict fetch overwrite a newer conflict list", async () => {
    const onApplied = vi.fn();
    const { result } = renderHook(() => useOfflineSync(1, 1, onApplied));
    // Let the initial refresh + initial autosync fully settle with defaults.
    await flush();

    // Arm deferred serverConflicts only for the two explicitly triggered
    // CHANGE_EVENT refreshes. The first armed call is the OLDER request, the
    // second armed call is the NEWER request.
    const olderConflicts = deferred<ServerConflict[]>();
    const newerConflicts = deferred<ServerConflict[]>();
    let armed = 0;
    syncEngineMock.serverConflicts.mockImplementation(() => {
      armed += 1;
      if (armed === 1) return olderConflicts.promise;
      if (armed === 2) return newerConflicts.promise;
      return Promise.resolve([]);
    });

    await changeEvent();
    await changeEvent();
    expect(armed).toBe(2);

    // Resolve the NEWER request first, then the OLDER one.
    await settle(newerConflicts, [conflict(2)]);
    expect(result.current.conflicts.map((c) => c.id)).toEqual([2]);

    await settle(olderConflicts, [conflict(1)]);

    expect(result.current.conflicts.map((c) => c.id)).toEqual([2]);
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("does not invoke onApplied or follow-on work for a pending syncNow after a scope switch", async () => {
    const onApplied = vi.fn();
    const { result, rerender } = renderHook(
      ({ userId }: { userId: number }) => useOfflineSync(userId, 1, onApplied),
      { initialProps: { userId: 1 } },
    );
    // Let the initial refresh + initial autosync fully settle.
    await flush();

    // Arm a controlled pending summary for the explicit syncNow below.
    const pendingBefore = deferred<SyncSummary>();
    syncEngineMock.offlineSummary.mockImplementation(() => pendingBefore.promise);

    // Record the baseline only after initial effects have settled.
    const syncBaseline = syncEngineMock.synchronize.mock.calls.length;
    const appliedBaseline = onApplied.mock.calls.length;

    let syncPromise: Promise<void> = Promise.resolve();
    await act(async () => {
      syncPromise = result.current.syncNow(true);
    });

    // Switch scope while the old-scope summary is still pending.
    rerender({ userId: 2 });
    await flush();

    await settle(pendingBefore, summary(5));
    await act(async () => { await syncPromise; });
    await flush();

    expect(onApplied.mock.calls.length).toBe(appliedBaseline);
    // No NEW old-scope synchronize follow-on since the baseline.
    const newSyncCalls = syncEngineMock.synchronize.mock.calls.slice(syncBaseline);
    expect(newSyncCalls.some((call) => call[0] === 1 && call[1] === 1)).toBe(false);
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
    const onApplied = vi.fn();
    const { result, unmount } = renderHook(() => useOfflineSync(1, 1, onApplied));
    // Let the initial refresh + initial autosync fully settle.
    await flush();

    // Arm a gated summary for an explicitly started syncNow.
    const pendingBefore = deferred<SyncSummary>();
    syncEngineMock.offlineSummary.mockImplementation(() => pendingBefore.promise);

    const syncBaseline = syncEngineMock.synchronize.mock.calls.length;
    const appliedBaseline = onApplied.mock.calls.length;

    let syncPromise: Promise<void> = Promise.resolve();
    await act(async () => {
      syncPromise = result.current.syncNow(true);
    });

    unmount();

    await settle(pendingBefore, summary(5));
    await act(async () => { await syncPromise; });
    await flush();

    expect(onApplied.mock.calls.length).toBe(appliedBaseline);
    // No NEW follow-on synchronize relative to the baseline.
    expect(syncEngineMock.synchronize.mock.calls.length).toBe(syncBaseline);
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

  // -------------------------------------------------------------------------
  // Retained-method coverage (PR471). A method captured from an earlier render
  // must not start backend work or invoke callbacks once its scope is no longer
  // current. Note: a pending first-A completion is NOT the same as a NEW
  // invocation of the retained first-A function.
  // -------------------------------------------------------------------------

  it("does not run backend work or callbacks for a retained method after A -> B", async () => {
    const onApplied = vi.fn();
    const { result, rerender } = renderHook(
      ({ userId }: { userId: number }) => useOfflineSync(userId, 1, onApplied),
      { initialProps: { userId: 1 } },
    );
    await flush();

    const retainedSyncNow = result.current.syncNow;
    const retainedResolve = result.current.resolve;

    rerender({ userId: 2 });
    await flush();

    const summaryBaseline = syncEngineMock.offlineSummary.mock.calls.length;
    const syncBaseline = syncEngineMock.synchronize.mock.calls.length;
    const resolveBaseline = syncEngineMock.resolveConflict.mock.calls.length;
    const appliedBaseline = onApplied.mock.calls.length;

    await act(async () => { await retainedSyncNow(true); });
    await act(async () => { await retainedResolve(1, "keep_server"); });
    await flush();

    expect(syncEngineMock.offlineSummary.mock.calls.length).toBe(summaryBaseline);
    expect(syncEngineMock.synchronize.mock.calls.length).toBe(syncBaseline);
    expect(syncEngineMock.resolveConflict.mock.calls.length).toBe(resolveBaseline);
    expect(onApplied.mock.calls.length).toBe(appliedBaseline);
  });

  it("does not run backend work or callbacks for a retained first-A method after A -> B -> A", async () => {
    const onApplied = vi.fn();
    const { result, rerender } = renderHook(
      ({ userId }: { userId: number }) => useOfflineSync(userId, 1, onApplied),
      { initialProps: { userId: 1 } },
    );
    await flush();

    const retainedFirstASyncNow = result.current.syncNow;
    const retainedFirstAResolve = result.current.resolve;

    rerender({ userId: 2 });
    await flush();

    rerender({ userId: 1 });
    await flush();

    const summaryBaseline = syncEngineMock.offlineSummary.mock.calls.length;
    const syncBaseline = syncEngineMock.synchronize.mock.calls.length;
    const resolveBaseline = syncEngineMock.resolveConflict.mock.calls.length;
    const appliedBaseline = onApplied.mock.calls.length;

    // Invoking the RETAINED first-A function is a NEW invocation, not the
    // completion of a pending first-A request. It must be inert.
    await act(async () => { await retainedFirstASyncNow(true); });
    await act(async () => { await retainedFirstAResolve(1, "keep_server"); });
    await flush();

    expect(syncEngineMock.offlineSummary.mock.calls.length).toBe(summaryBaseline);
    expect(syncEngineMock.synchronize.mock.calls.length).toBe(syncBaseline);
    expect(syncEngineMock.resolveConflict.mock.calls.length).toBe(resolveBaseline);
    expect(onApplied.mock.calls.length).toBe(appliedBaseline);
  });

  it("does not run backend work or callbacks for a retained method after unmount", async () => {
    const onApplied = vi.fn();
    const { result, unmount } = renderHook(() => useOfflineSync(1, 1, onApplied));
    await flush();

    const retainedSyncNow = result.current.syncNow;
    const retainedResolve = result.current.resolve;

    unmount();

    const summaryBaseline = syncEngineMock.offlineSummary.mock.calls.length;
    const syncBaseline = syncEngineMock.synchronize.mock.calls.length;
    const resolveBaseline = syncEngineMock.resolveConflict.mock.calls.length;
    const appliedBaseline = onApplied.mock.calls.length;

    await act(async () => { await retainedSyncNow(true); });
    await act(async () => { await retainedResolve(1, "keep_server"); });
    await flush();

    expect(syncEngineMock.offlineSummary.mock.calls.length).toBe(summaryBaseline);
    expect(syncEngineMock.synchronize.mock.calls.length).toBe(syncBaseline);
    expect(syncEngineMock.resolveConflict.mock.calls.length).toBe(resolveBaseline);
    expect(onApplied.mock.calls.length).toBe(appliedBaseline);
  });

  it("resolves and invokes onApplied for a current-scope sync under StrictMode", async () => {
    let total = 5;
    syncEngineMock.offlineSummary.mockImplementation(() => Promise.resolve(summary(total)));
    syncEngineMock.synchronize.mockImplementation(async () => { total = 0; });

    const onApplied = vi.fn();
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      createElement(StrictMode, null, children);
    const { result } = renderHook(() => useOfflineSync(1, 1, onApplied), { wrapper });
    await flush();

    await act(async () => {
      await result.current.syncNow(true);
    });
    await flush();

    expect(syncEngineMock.synchronize).toHaveBeenCalledWith(1, 1, true);
    expect(onApplied).toHaveBeenCalledTimes(1);
  });
});
