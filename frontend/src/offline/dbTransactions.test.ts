// Synthetic IDB transaction events; real IndexedDB/multitab remains unrun.
import { afterEach, expect, it, vi } from "vitest";
import type { OfflineCommand } from "./types";
import { putCommands, clearOfflineData } from "./db";
afterEach(() => vi.unstubAllGlobals());

function syntheticDatabase(failAt = 0) {
  const requests: any[] = [];
  const transaction: any = { error: null, objectStore: () => ({ put: () => {
    if (failAt && requests.length + 1 === failAt) throw new DOMException("Synthetic quota", "QuotaExceededError");
    const request: any = { error: null }; requests.push(request); return request;
  }, clear: () => {
    if (failAt && requests.length + 1 === failAt) throw new DOMException("Synthetic quota", "QuotaExceededError");
    const request: any = { error: null }; requests.push(request); return request;
  } }), abort: vi.fn(() => queueMicrotask(() => transaction.onabort?.())) };
  const db = { transaction: vi.fn(() => transaction), close: vi.fn() };
  vi.stubGlobal("indexedDB", { open: () => {
    const request: any = { result: db }; queueMicrotask(() => request.onsuccess?.()); return request;
  } });
  return { requests, transaction, db };
}
const commands = [{ id: "synthetic-one" }, { id: "synthetic-two" }] as OfflineCommand[];

it("acknowledges selection persistence only after the entire transaction commits", async () => {
  const s = syntheticDatabase(); const completed = vi.fn();
  const operation = putCommands(commands).then(completed);
  await Promise.resolve(); await Promise.resolve();
  expect(s.requests).toHaveLength(2); expect(completed).not.toHaveBeenCalled();
  s.transaction.oncomplete(); await operation;
  expect(completed).toHaveBeenCalledOnce(); expect(s.db.close).toHaveBeenCalledOnce();
});

it("aborts all selection writes if the second put throws synchronously", async () => {
  const s = syntheticDatabase(2); const operation = putCommands(commands);
  await expect(operation).rejects.toMatchObject({ name: "QuotaExceededError" });
  expect(s.transaction.abort).toHaveBeenCalledOnce(); expect(s.db.close).toHaveBeenCalledOnce();
});

it("rejects a transaction abort even if earlier puts succeeded", async () => {
  const s = syntheticDatabase(); const operation = putCommands(commands);
  await Promise.resolve(); await Promise.resolve();
  s.requests[1].error = new Error("Synthetic write error"); s.requests[1].onerror();
  await expect(operation).rejects.toThrow("Synthetic write error");
  expect(s.transaction.abort).toHaveBeenCalledOnce();
});

it("settles a local logout clear on abort rather than hanging forever", async () => {
  const s = syntheticDatabase(); const operation = clearOfflineData();
  await Promise.resolve(); await Promise.resolve();
  expect(s.transaction.onabort).toBeTypeOf("function");
  s.transaction.error = new Error("Synthetic logout clear aborted"); s.transaction.onabort();
  await expect(operation).rejects.toThrow("Synthetic logout clear aborted");
  expect(s.db.close).toHaveBeenCalledOnce();
});

it("aborts both logout stores and closes the connection on synchronous clear failure", async () => {
  const s = syntheticDatabase(2); const operation = clearOfflineData();
  await expect(operation).rejects.toMatchObject({ name: "QuotaExceededError" });
  expect(s.transaction.abort).toHaveBeenCalledOnce(); expect(s.db.close).toHaveBeenCalledOnce();
});
