// Codex fallback after DS29 generation was rejected. Unit simulation, not PWA acceptance.
// @ts-expect-error Node builtins exist in Vitest; this browser project's TS libs exclude Node types.
import { readFileSync } from "node:fs";
// @ts-expect-error Node builtins exist in Vitest; this browser project's TS libs exclude Node types.
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

const source = readFileSync("public/service-worker.js", "utf8");
const origin = "https://synthetic.example.test";

function worker() {
  const listeners = new Map<string, (event: any) => void>();
  const own = { match: vi.fn().mockResolvedValue(undefined), put: vi.fn().mockResolvedValue(undefined), addAll: vi.fn() };
  const caches = {
    keys: vi.fn().mockResolvedValue(["pu-workspace-shell-v1", "pu-workspace-shell-v2", "another-app", "pu-workspace-shellish"]),
    delete: vi.fn().mockResolvedValue(true),
    open: vi.fn().mockResolvedValue(own),
    match: vi.fn().mockResolvedValue({ body: "foreign-cache-poison" }),
  };
  const fetch = vi.fn().mockRejectedValue(new Error("synthetic offline"));
  runInNewContext(source, { URL, caches, fetch, self: {
    location: { origin }, skipWaiting: vi.fn(), clients: { claim: vi.fn() },
    addEventListener: (name: string, fn: (event: any) => void) => listeners.set(name, fn),
  } });
  async function request(path: string, mode = "cors", method = "GET") {
    let response: Promise<unknown> | undefined;
    listeners.get("fetch")!({ request: { url: origin + path, mode, method },
      respondWith: (value: Promise<unknown>) => { response = value; }, waitUntil: vi.fn() });
    return response;
  }
  return { listeners, own, caches, fetch, request };
}

describe("service worker cache ownership", () => {
  it("deletes only older owned shell caches, preserving sibling apps", async () => {
    const w = worker(); let finished: Promise<unknown> | undefined;
    w.listeners.get("activate")!({ waitUntil: (value: Promise<unknown>) => { finished = value; } });
    await finished;
    expect(w.caches.delete.mock.calls).toEqual([["pu-workspace-shell-v1"]]);
  });

  it("never serves a matching asset from another cache", async () => {
    const w = worker();
    expect(await w.request("/new/assets/app.js")).toBeUndefined();
    expect(w.caches.match).not.toHaveBeenCalled();
    expect(w.own.match).toHaveBeenCalled();
  });

  it("uses only the active shell for offline navigation", async () => {
    const w = worker(); const shell = { body: "owned-shell" };
    w.own.match.mockImplementation(async (key: unknown) => key === "/new/" ? shell : undefined);
    expect(await w.request("/new/synthetic-route", "navigate")).toBe(shell);
    expect(w.caches.match).not.toHaveBeenCalled();
  });

  it("does not intercept API calls, writes or paths outside the app", async () => {
    const w = worker();
    await w.request("/new/api/private"); await w.request("/new/", "cors", "POST"); await w.request("/sibling/");
    expect(w.fetch).not.toHaveBeenCalled(); expect(w.caches.open).not.toHaveBeenCalled();
  });
});
