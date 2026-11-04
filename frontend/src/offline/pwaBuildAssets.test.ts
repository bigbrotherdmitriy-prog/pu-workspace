// Codex fallback: generated script unit contract, not browser installation.
// @ts-expect-error Vitest uses Node builtins; browser TS libs exclude Node declarations.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest uses Node builtins; browser TS libs exclude Node declarations.
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";
import { buildShellWorker } from "../../build/pwaShellPlugin.mjs";
const source = readFileSync("public/service-worker.js", "utf8");

it("precaches entry and lazy JS/CSS in a build-specific owned shell cache", async () => {
  const paths = ["/new/assets/entry-123.js", "/new/assets/lazy-456.js", "/new/assets/styles-789.css"];
  const built = buildShellWorker(source, paths, "a".repeat(64));
  const listeners = new Map<string, (event: any) => void>();
  const addAll = vi.fn().mockResolvedValue(undefined), open = vi.fn(async () => ({ addAll }));
  runInNewContext(built, { self: { addEventListener: (key: string, value: (event: any) => void) => listeners.set(key, value), skipWaiting: vi.fn() }, caches: { open } });
  let completed: Promise<unknown> | undefined;
  listeners.get("install")!({ waitUntil: (value: Promise<unknown>) => { completed = value; } }); await completed;
  expect(open).toHaveBeenCalledWith("pu-workspace-shell-build-" + "a".repeat(64));
  expect(addAll.mock.calls[0][0]).toEqual(expect.arrayContaining(["/new/", ...paths]));
  expect(built).not.toContain("PU_BUILD_ASSETS");
});

it.each([
  { paths: ["https://foreign.example.test/script.js"] }, { paths: ["/new/api/private.js"] },
  { paths: ["/new/assets/../private.js"] }, { paths: [] },
])("refuses unsafe or empty generated precache: %j", ({ paths }) => {
  expect(() => buildShellWorker(source, paths, "a".repeat(64))).toThrow();
});

it("fails the build if worker markers drift rather than silently shipping an empty precache", () => {
  expect(() => buildShellWorker("synthetic stale worker", ["/new/assets/app.js"], "a".repeat(64))).toThrow();
});
