// Codex fallback. In-memory queue double, not real IndexedDB/cookie-switch HTTP.
import { beforeEach, expect, it, vi } from "vitest";
import type { OfflineCommand } from "./types";
import { api, ApiError } from "../api/client";
const store = vi.hoisted(() => ({ rows: new Map<string, OfflineCommand>(), meta: new Map<string, unknown>() }));
vi.mock("../api/client", () => ({ api: vi.fn(), ApiError: class extends Error { constructor(message: string, public status: number | null) { super(message); } } }));
vi.mock("./db", () => ({
  putCommand: vi.fn(async (row: OfflineCommand) => { store.rows.set(row.id, { ...row }); }),
  deleteCommand: vi.fn(async (id: string) => { store.rows.delete(id); }),
  listCommands: vi.fn(async (user: number, project: number) => [...store.rows.values()].filter(row => row.userId === user && row.projectId === project)),
  getMeta: vi.fn(async (key: string) => store.meta.get(key)),
  setMeta: vi.fn(async (key: string, value: unknown) => { store.meta.set(key, value); }),
}));
import { enqueueDocumentUpload, recoverAuthRequired, synchronize } from "./syncEngine";
const args = () => ({ userId: 1, projectId: 17, files: [{ name: "synthetic.txt", path: "synthetic.txt", mimeType: "text/plain", blob: new Blob(["synthetic"], { type: "text/plain" }) }] });
beforeEach(() => { store.rows.clear(); store.meta.clear(); vi.mocked(api).mockReset(); Object.defineProperty(navigator, "onLine", { configurable: true, value: true }); });

it("transmits the immutable queue owner alongside the stable idempotency key", async () => {
  const id = await enqueueDocumentUpload(args());
  vi.mocked(api).mockResolvedValue({ status: "queued", jobs: [{ job_id: 91, status: "queued" }] });
  await synchronize(1, 17);
  expect(api).toHaveBeenCalledWith("/local-upload/analyze", expect.objectContaining({ headers: { "Idempotency-Key": id, "X-PU-Expected-User": "1" } }));
  expect(store.rows.size).toBe(0);
});

it("keeps wrong-cookie uploads auth-required until explicit same-account recovery", async () => {
  const id = await enqueueDocumentUpload(args());
  vi.mocked(api).mockRejectedValue(new ApiError("Synthetic other actor", 403, "synthetic-request"));
  await synchronize(1, 17); await synchronize(1, 17, true);
  expect(api).toHaveBeenCalledTimes(1); expect(store.rows.get(id)?.status).toBe("auth_required");
  vi.mocked(api).mockResolvedValue({ id: 2 });
  await expect(recoverAuthRequired(1, 17)).rejects.toThrow();
  expect(store.rows.get(id)?.status).toBe("auth_required");
});

it("does not send a corrupted queue identity", async () => {
  const id = await enqueueDocumentUpload(args());
  store.rows.get(id)!.userId = -1;
  await synchronize(-1, 17);
  expect(api).not.toHaveBeenCalled(); expect(store.rows.has(id)).toBe(true);
});

it.each([
  {}, { status: "queued", jobs: [] }, { status: "ok", jobs: [{ job_id: 91, status: "queued" }] },
  { status: "queued", jobs: [{ job_id: 0, status: "queued" }] },
  { status: "queued", jobs: [{ job_id: true, status: "queued" }] },
  { status: "queued", jobs: [{ job_id: 91, status: "failed" }] },
  { status: "queued", jobs: [{ job_id: 91, status: "queued" }, { job_id: 91, status: "queued" }] },
])("retains uploads after a malformed durable handoff: %j", async response => {
  const id = await enqueueDocumentUpload(args());
  vi.mocked(api).mockResolvedValue(response);
  await synchronize(1, 17);
  expect(store.rows.has(id)).toBe(true);
  expect(store.rows.get(id)?.lastError).toBeTruthy();
});

it("accepts the real server already-queued replay without claiming OCR completion", async () => {
  await enqueueDocumentUpload(args());
  vi.mocked(api).mockResolvedValue({ status: "queued", jobs: [{ job_id: 91, status: "already_queued" }] });
  await synchronize(1, 17);
  expect(store.rows.size).toBe(0);
});
