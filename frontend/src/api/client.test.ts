import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";

// SYNTHETIC test data only. No real HTTP, IndexedDB, config, env or network.

const dbState = vi.hoisted(() => ({
  commands: new Map<string, Record<string, unknown>>(),
  meta: new Map<string, unknown>(),
  trace: [] as string[],
}));

vi.mock("../offline/db", () => ({
  putCommand: vi.fn(async (command: Record<string, unknown>) => {
    dbState.trace.push(`put:${String(command.id)}`);
    // Shallow copy preserves Blob references/bytes; clone only plain metadata.
    dbState.commands.set(String(command.id), { ...command });
  }),
  deleteCommand: vi.fn(async (id: string) => {
    dbState.trace.push(`delete:${id}`);
    dbState.commands.delete(id);
  }),
  listCommands: vi.fn(async (userId: number, projectId: number) =>
    [...dbState.commands.values()]
      .filter((item) => item.userId === userId && item.projectId === projectId)
      .map((item) => ({ ...item })),
  ),
  getMeta: vi.fn(async (key: string) => dbState.meta.get(key)),
  setMeta: vi.fn(async (key: string, value: unknown) => {
    dbState.trace.push(`meta:${key}`);
    dbState.meta.set(key, value);
  }),
}));

import { resetConnectivityForTests } from "../offline/connectivity";
import { enqueueDocumentUpload, synchronize } from "../offline/syncEngine";

const USER_ID = 7;
const PROJECT_ID = 11;
const STAGING_A = "0123456789abcdef0123456789abcdef";
const STAGING_B = "fedcba9876543210fedcba9876543210";

function makeFile(name: string, content: string): File {
  return new File([content], name, { type: "application/pdf" });
}

function normalizeHeaders(init?: RequestInit): Record<string, string> {
  const headers = new Headers(init?.headers);
  const out: Record<string, string> = {};
  headers.forEach((value, key) => { out[key.toLowerCase()] = value; });
  return out;
}

function responseJSON(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function ackBody(jobs: Array<Record<string, unknown>>, status = "queued") {
  return { status, jobs };
}

function seedCommand(id: string, files: File[]) {
  dbState.commands.set(id, {
    id,
    userId: USER_ID,
    projectId: PROJECT_ID,
    deviceId: "synthetic-device",
    operation: "document.upload",
    entityId: 0,
    patch: {},
    uploads: files.map((blob, index) => ({
      name: blob.name,
      path: `docs/${blob.name}`,
      mimeType: blob.type,
      blob,
    })),
    clientCreatedAt: new Date().toISOString(),
    status: "queued",
    attempts: 0,
    updatedAt: new Date().toISOString(),
  });
}

function clearTrace() {
  dbState.trace.length = 0;
}

beforeEach(() => {
  dbState.commands.clear();
  dbState.meta.clear();
  clearTrace();
  resetConnectivityForTests();
  vi.stubGlobal("navigator", { ...globalThis.navigator, onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  dbState.commands.clear();
  dbState.meta.clear();
  clearTrace();
});

describe("API structured errors", () => {
  it("preserves every rejected DDS row for the atomic confirmation report", async () => {
    const detail = { code: "CASH_FLOW_BATCH_REJECTED", confirmed_count: 0,
      rows: [{ id: 1, code: "NOT_APPLIED" }, { id: 2, code: "CASH_FLOW_VERSION_MISMATCH" }] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail }),
      { status: 409, headers: { "Content-Type": "application/json" } })));
    await expect(api("/execution/cash-flow/confirm-batch", { method: "POST" })).rejects.toMatchObject({ details: detail });
  });
  it("preserves a stable conflict code for truthful recovery UI", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: { code: "record_version_conflict" } }),
      { status: 409, headers: { "Content-Type": "application/json", "X-Request-ID": "request-1" } },
    )));
    await expect(api("/management/meetings/7/source-binding", { method: "POST" }))
      .rejects.toMatchObject({
        status: 409,
        code: "record_version_conflict",
        requestId: "request-1",
      });
  });
});

describe("document upload staging ACK", () => {
  it.each(["queued", "retrying", "running", "already_queued"])(
    "accepts a valid %s ACK, sends actor/idempotency headers and writes in order",
    async (jobStatus) => {
      const file = makeFile("synthetic.pdf", "SYNTHETIC-CONTENT");
      const commandId = await enqueueDocumentUpload({ userId: USER_ID, projectId: PROJECT_ID, files: [
        { name: file.name, path: "docs/synthetic.pdf", mimeType: file.type, blob: file },
      ] });
      clearTrace();

      const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
        const headers = normalizeHeaders(init);
        expect(headers["idempotency-key"]).toBe(commandId);
        expect(headers["x-pu-expected-user"]).toBe(String(USER_ID));
        const body = JSON.parse(String(init?.body));
        expect(body.project_id).toBe(PROJECT_ID);
        expect(Array.isArray(body.files)).toBe(true);
        expect(body.files).toHaveLength(1);
        expect(body.files[0].path).toBe("docs/synthetic.pdf");
        return responseJSON(ackBody([{ job_id: 101, staging_id: STAGING_A, status: jobStatus }]));
      });
      vi.stubGlobal("fetch", fetchMock);

      await synchronize(USER_ID, PROJECT_ID, true);

      const ackKey = `upload-staging-ack:${USER_ID}:${PROJECT_ID}:${commandId}`;
      const stored = dbState.meta.get(ackKey) as { status: string; jobs: Array<Record<string, unknown>> };
      expect(stored).toEqual({
        status: "queued",
        jobs: [{ job_id: 101, staging_id: STAGING_A, status: jobStatus }],
      });
      expect(JSON.stringify(stored)).not.toMatch(/base64|blob|path/i);

      const ackIndex = dbState.trace.indexOf(`meta:${ackKey}`);
      const deleteIndex = dbState.trace.indexOf(`delete:${commandId}`);
      const lastSyncedIndex = dbState.trace.indexOf(`meta:last-synced:${USER_ID}:${PROJECT_ID}`);
      expect(ackIndex).toBeGreaterThanOrEqual(0);
      expect(deleteIndex).toBeGreaterThan(ackIndex);
      expect(lastSyncedIndex).toBeGreaterThan(deleteIndex);
      expect(dbState.commands.has(commandId)).toBe(false);
    },
  );

  const malformed: Array<[string, unknown]> = [
    ["null body", null],
    ["empty object", {}],
    ["wrong job count", ackBody([{ job_id: 1, staging_id: STAGING_A, status: "queued" }, { job_id: 2, staging_id: STAGING_B, status: "queued" }])],
    ["unsafe job_id", ackBody([{ job_id: 0, staging_id: STAGING_A, status: "queued" }])],
    ["non-integer job_id", ackBody([{ job_id: 1.5, staging_id: STAGING_A, status: "queued" }])],
    ["staging_id wrong type", ackBody([{ job_id: 1, staging_id: 12345, status: "queued" }])],
    ["staging_id wrong length", ackBody([{ job_id: 1, staging_id: "abc", status: "queued" }])],
    ["staging_id uppercase", ackBody([{ job_id: 1, staging_id: STAGING_A.toUpperCase(), status: "queued" }])],
    ["unknown job status", ackBody([{ job_id: 1, staging_id: STAGING_A, status: "done" }])],
    ["duplicate job_id", ackBody([{ job_id: 1, staging_id: STAGING_A, status: "queued" }, { job_id: 1, staging_id: STAGING_B, status: "queued" }])],
    ["duplicate staging_id", ackBody([{ job_id: 1, staging_id: STAGING_A, status: "queued" }, { job_id: 2, staging_id: STAGING_A, status: "queued" }])],
  ];

  it.each(malformed)("rejects malformed ACK: %s", async (_label, body) => {
    const file = makeFile("synthetic.pdf", "SYNTHETIC-CONTENT");
    const commandId = await enqueueDocumentUpload({ userId: USER_ID, projectId: PROJECT_ID, files: [
      { name: file.name, path: "docs/synthetic.pdf", mimeType: file.type, blob: file },
    ] });
    const originalBlob = (dbState.commands.get(commandId)?.uploads as Array<{ blob: Blob }>)[0].blob;
    const previousMarker = "2020-01-01T00:00:00.000Z";
    dbState.meta.set(`last-synced:${USER_ID}:${PROJECT_ID}`, previousMarker);
    clearTrace();

    vi.stubGlobal("fetch", vi.fn(async () => responseJSON(body)));

    await synchronize(USER_ID, PROJECT_ID, true);

    const remaining = dbState.commands.get(commandId);
    expect(remaining).toBeTruthy();
    const remainingUploads = remaining?.uploads as Array<{ blob: Blob }>;
    expect(remainingUploads[0].blob).toBe(originalBlob);
    expect(remaining?.status).not.toBe("conflict");
    expect(dbState.meta.get(`last-synced:${USER_ID}:${PROJECT_ID}`)).toBe(previousMarker);
    expect(dbState.trace.some((entry) => entry.startsWith("delete:"))).toBe(false);
    expect(dbState.meta.has(`upload-staging-ack:${USER_ID}:${PROJECT_ID}:${commandId}`)).toBe(false);
  });

  it("keeps blob and marks auth_required on 403 offline_actor_mismatch", async () => {
    const file = makeFile("synthetic.pdf", "SYNTHETIC-CONTENT");
    const commandId = await enqueueDocumentUpload({ userId: USER_ID, projectId: PROJECT_ID, files: [
      { name: file.name, path: "docs/synthetic.pdf", mimeType: file.type, blob: file },
    ] });
    const originalBlob = (dbState.commands.get(commandId)?.uploads as Array<{ blob: Blob }>)[0].blob;
    clearTrace();

    vi.stubGlobal("fetch", vi.fn(async () => responseJSON(
      { detail: { code: "offline_actor_mismatch" } }, 403,
    )));

    await synchronize(USER_ID, PROJECT_ID, true);

    const remaining = dbState.commands.get(commandId);
    expect(remaining?.status).toBe("auth_required");
    expect((remaining?.uploads as Array<{ blob: Blob }>)[0].blob).toBe(originalBlob);
    expect(dbState.meta.has(`upload-staging-ack:${USER_ID}:${PROJECT_ID}:${commandId}`)).toBe(false);
    expect(dbState.trace.some((entry) => entry.startsWith("delete:"))).toBe(false);
  });

  it("retains blob and skips deletion when ACK meta write fails", async () => {
    const file = makeFile("synthetic.pdf", "SYNTHETIC-CONTENT");
    const commandId = await enqueueDocumentUpload({ userId: USER_ID, projectId: PROJECT_ID, files: [
      { name: file.name, path: "docs/synthetic.pdf", mimeType: file.type, blob: file },
    ] });
    const originalBlob = (dbState.commands.get(commandId)?.uploads as Array<{ blob: Blob }>)[0].blob;
    const previousMarker = "2020-01-01T00:00:00.000Z";
    dbState.meta.set(`last-synced:${USER_ID}:${PROJECT_ID}`, previousMarker);
    clearTrace();

    const ackKey = `upload-staging-ack:${USER_ID}:${PROJECT_ID}:${commandId}`;
    const db = await import("../offline/db");
    vi.mocked(db.setMeta).mockImplementationOnce(async () => {
      throw new Error("SYNTHETIC meta write failure");
    });

    vi.stubGlobal("fetch", vi.fn(async () => responseJSON(
      ackBody([{ job_id: 101, staging_id: STAGING_A, status: "queued" }]),
    )));

    await synchronize(USER_ID, PROJECT_ID, true);

    const remaining = dbState.commands.get(commandId);
    expect(remaining).toBeTruthy();
    expect((remaining?.uploads as Array<{ blob: Blob }>)[0].blob).toBe(originalBlob);
    expect(dbState.meta.has(ackKey)).toBe(false);
    expect(dbState.meta.get(`last-synced:${USER_ID}:${PROJECT_ID}`)).toBe(previousMarker);
    expect(dbState.trace.some((entry) => entry.startsWith("delete:"))).toBe(false);
  });
});
