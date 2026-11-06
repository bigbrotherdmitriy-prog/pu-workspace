import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { OfflineCommand } from "./types";

const store = vi.hoisted(() => ({ rows: new Map<string, OfflineCommand>(), meta: new Map<string, unknown>() }));

vi.mock("../api/client", () => ({
  api: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number | null;
    requestId?: string;
    constructor(message: string, status: number | null, requestId?: string) { super(message); this.status = status; this.requestId = requestId; }
  },
}));
vi.mock("./db", () => ({
  putCommand: vi.fn(async (row: OfflineCommand) => { store.rows.set(row.id, structuredClone(row)); }),
  deleteCommand: vi.fn(async (id: string) => { store.rows.delete(id); }),
  listCommands: vi.fn(async (userId: number, projectId: number) => [...store.rows.values()].filter(
    (row) => row.userId === userId && row.projectId === projectId,
  )),
  getMeta: vi.fn(async (key: string) => store.meta.get(key)),
  setMeta: vi.fn(async (key: string, value: unknown) => { store.meta.set(key, value); }),
}));

import { enqueueNotificationRead, enqueueTaskUpdate, offlineSummary, synchronize } from "./syncEngine";

beforeEach(() => {
  store.rows.clear();
  store.meta.clear();
  vi.mocked(api).mockReset();
  Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
});

type Sent = {
  client_mutation_id: string;
  operation: string;
  entity_id: number;
  project_id: number;
};

function sentBody(): Sent {
  const call = vi.mocked(api).mock.calls[0];
  return JSON.parse(String((call[1] as { body: string }).body));
}

function entityTypeFor(operation: string): string {
  return operation === "notification.mark_read" ? "notification" : "task";
}

function appliedReceipt(receiptId: number) {
  const sent = sentBody();
  return {
    receipt_id: receiptId,
    client_mutation_id: sent.client_mutation_id,
    operation: sent.operation,
    entity_id: sent.entity_id,
    entity_type: entityTypeFor(sent.operation),
    status: "applied",
  };
}

function conflictReceipt(receiptId: number, conflictId: number) {
  const sent = sentBody();
  return {
    receipt_id: receiptId,
    client_mutation_id: sent.client_mutation_id,
    operation: sent.operation,
    entity_id: sent.entity_id,
    entity_type: entityTypeFor(sent.operation),
    status: "conflict",
    conflict: {
      id: conflictId,
      command_id: receiptId,
      entity_type: entityTypeFor(sent.operation),
      entity_id: sent.entity_id,
    },
  };
}

describe("offline synchronization queue", () => {
  it("coalesces safe edits of one task without discarding either field", async () => {
    await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });
    await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { assignee_user_id: 3 },
    });

    expect(store.rows.size).toBe(1);
    expect([...store.rows.values()][0].patch).toEqual({ status: "in_progress", assignee_user_id: 3 });
    expect((await offlineSummary(1, 17)).total).toBe(1);
  });

  it("keeps failed changes and marks them as requiring attention after three attempts", async () => {
    await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    vi.mocked(api).mockRejectedValue(new ApiError("network unavailable", null, "offline-test"));

    await synchronize(1, 17, true);
    await synchronize(1, 17, true);
    await synchronize(1, 17, true);

    let row = [...store.rows.values()][0];
    expect(row.status).toBe("attention");
    expect(row.attempts).toBe(3);
    expect((await offlineSummary(1, 17)).attention).toBe(1);
    vi.mocked(api).mockClear();
    await synchronize(1, 17, false);
    expect(api).not.toHaveBeenCalled();

    vi.mocked(api).mockImplementation(async () => appliedReceipt(9));
    await synchronize(1, 17, true);
    expect(store.rows.size).toBe(0);
  });

  it("preserves a real server conflict for explicit user resolution", async () => {
    await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "cancelled" },
    });
    vi.mocked(api).mockImplementation(async () => conflictReceipt(22, 71));

    await synchronize(1, 17, true);

    const row = [...store.rows.values()][0];
    expect(row.status).toBe("conflict");
    expect(row.receiptId).toBe(22);
    expect(row.conflictId).toBe(71);
    expect((await offlineSummary(1, 17)).conflicts).toBe(1);
  });
});

describe("offline synchronization auth suspension", () => {
  it.each([401, 403])("suspends a pending notification command on ApiError %i", async (status) => {
    const id = await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    const before = structuredClone(store.rows.get(id)!);
    vi.mocked(api).mockRejectedValue(new ApiError("unauthorized", status, "offline-test"));

    await synchronize(1, 17, true);

    const row = store.rows.get(id)!;
    expect(row).toBeDefined();
    expect(row.status).toBe("auth_required");
    expect(row.id).toBe(before.id);
    expect(row.patch).toEqual(before.patch);
    expect(row.clientCreatedAt).toBe(before.clientCreatedAt);
    expect(row.userId).toBe(before.userId);
    expect(row.projectId).toBe(before.projectId);
    expect(store.meta.has("last-synced:1:17")).toBe(false);

    const summary = await offlineSummary(1, 17);
    expect(summary.authRequired).toBe(1);
    expect(summary.total).toBe(1);

    vi.mocked(api).mockClear();
    await synchronize(1, 17, false);
    await synchronize(1, 17, true);
    expect(api).not.toHaveBeenCalled();
    expect(store.rows.get(id)!.status).toBe("auth_required");
    expect(store.rows.size).toBe(1);
  });

  it("delivers a fresh pending notification on a matching applied receipt", async () => {
    const id = await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    vi.mocked(api).mockImplementation(async () => appliedReceipt(9));

    await synchronize(1, 17, true);

    expect(api).toHaveBeenCalledTimes(1);
    expect(store.rows.has(id)).toBe(false);
    expect(store.rows.size).toBe(0);
    expect(store.meta.has("last-synced:1:17")).toBe(true);
  });

  it("does not deliver a queued command outside the requested scope", async () => {
    const id = await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    const before = structuredClone(store.rows.get(id)!);
    vi.mocked(api).mockImplementation(async () => appliedReceipt(9));

    await synchronize(2, 17, true);
    await synchronize(1, 18, true);

    expect(api).not.toHaveBeenCalled();
    expect(store.rows.get(id)).toEqual(before);
    expect(store.rows.size).toBe(1);
  });
});

describe("strict receipt guard", () => {
  type Case = { name: string; mutate: (r: Record<string, unknown>) => unknown };
  const cases: Case[] = [
    { name: "missing receipt_id", mutate: (r) => { delete r.receipt_id; return r; } },
    { name: "zero receipt_id", mutate: (r) => ({ ...r, receipt_id: 0 }) },
    { name: "negative receipt_id", mutate: (r) => ({ ...r, receipt_id: -5 }) },
    { name: "noninteger receipt_id", mutate: (r) => ({ ...r, receipt_id: 1.5 }) },
    { name: "unsafe receipt_id", mutate: (r) => ({ ...r, receipt_id: Number.MAX_SAFE_INTEGER + 2 }) },
    { name: "wrong client_mutation_id", mutate: (r) => ({ ...r, client_mutation_id: "other-id" }) },
    { name: "wrong operation", mutate: (r) => ({ ...r, operation: "task.update" }) },
    { name: "wrong entity_id", mutate: (r) => ({ ...r, entity_id: 999 }) },
    { name: "wrong entity_type", mutate: (r) => ({ ...r, entity_type: "task" }) },
    { name: "null response", mutate: () => null },
  ];

  it.each(cases)("rejects malformed applied receipt: $name", async ({ mutate }) => {
    const id = await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    const before = structuredClone(store.rows.get(id)!);
    vi.mocked(api).mockImplementation(async () => mutate(appliedReceipt(9)) as never);

    await synchronize(1, 17, true);

    const row = store.rows.get(id)!;
    expect(row).toBeDefined();
    expect(row.id).toBe(before.id);
    expect(row.patch).toEqual(before.patch);
    expect(row.clientCreatedAt).toBe(before.clientCreatedAt);
    expect(store.meta.has("last-synced:1:17")).toBe(false);
  });

  type CCase = { name: string; mutate: (r: Record<string, unknown>) => unknown };
  const conflictCases: CCase[] = [
    { name: "missing conflict id", mutate: (r) => { delete (r.conflict as Record<string, unknown>).id; return r; } },
    { name: "bad conflict id", mutate: (r) => ({ ...r, conflict: { ...(r.conflict as object), id: 0 } }) },
    { name: "wrong command_id", mutate: (r) => ({ ...r, conflict: { ...(r.conflict as object), command_id: 999 } }) },
    { name: "wrong conflict entity", mutate: (r) => ({ ...r, conflict: { ...(r.conflict as object), entity_id: 999 } }) },
  ];

  it.each(conflictCases)("rejects malformed conflict receipt: $name", async ({ mutate }) => {
    const id = await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    const before = structuredClone(store.rows.get(id)!);
    vi.mocked(api).mockImplementation(async () => mutate(conflictReceipt(22, 71)) as never);

    await synchronize(1, 17, true);

    const row = store.rows.get(id)!;
    expect(row).toBeDefined();
    expect(row.id).toBe(before.id);
    expect(row.patch).toEqual(before.patch);
    expect(row.clientCreatedAt).toBe(before.clientCreatedAt);
    expect(store.meta.has("last-synced:1:17")).toBe(false);
  });

  it("applies a valid task.update receipt", async () => {
    const id = await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });
    vi.mocked(api).mockImplementation(async () => appliedReceipt(31));

    await synchronize(1, 17, true);

    expect(store.rows.has(id)).toBe(false);
    expect(store.meta.has("last-synced:1:17")).toBe(true);
  });

  it("applies a valid notification.mark_read receipt", async () => {
    const id = await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    vi.mocked(api).mockImplementation(async () => appliedReceipt(32));

    await synchronize(1, 17, true);

    expect(store.rows.has(id)).toBe(false);
    expect(store.meta.has("last-synced:1:17")).toBe(true);
  });

  it("records a valid task.update conflict", async () => {
    const id = await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "cancelled" },
    });
    vi.mocked(api).mockImplementation(async () => conflictReceipt(33, 81));

    await synchronize(1, 17, true);

    const row = store.rows.get(id)!;
    expect(row.status).toBe("conflict");
    expect(row.receiptId).toBe(33);
    expect(row.conflictId).toBe(81);
  });
});

describe("historical PR379 immutable coalescing", () => {
  it("creates a distinct id after a failed attempt while the attempted row stays unchanged", async () => {
    const first = await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });
    vi.mocked(api).mockRejectedValue(new ApiError("network unavailable", null, "offline-test"));
    await synchronize(1, 17, true);
    const attempted = structuredClone(store.rows.get(first)!);

    const second = await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { assignee_user_id: 3 },
    });

    expect(second).not.toBe(first);
    expect(store.rows.get(first)).toEqual(attempted);
    expect(store.rows.get(second)!.patch).toEqual({ assignee_user_id: 3 });
  });

  it("retries the original lost reply with a byte-identical body", async () => {
    await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    vi.mocked(api).mockRejectedValue(new ApiError("network unavailable", null, "offline-test"));
    await synchronize(1, 17, true);
    const firstBody = String((vi.mocked(api).mock.calls[0][1] as { body: string }).body);

    vi.mocked(api).mockImplementation(async () => appliedReceipt(9));
    await synchronize(1, 17, true);
    const retryBody = String((vi.mocked(api).mock.calls[1][1] as { body: string }).body);

    expect(retryBody).toBe(firstBody);
  });

  it("never coalesces edits from separate users or projects", async () => {
    await enqueueTaskUpdate({ userId: 1, projectId: 17, taskId: 9, baseToken: "b", patch: { status: "in_progress" } });
    await enqueueTaskUpdate({ userId: 2, projectId: 17, taskId: 9, baseToken: "b", patch: { status: "done" } });
    await enqueueTaskUpdate({ userId: 1, projectId: 18, taskId: 9, baseToken: "b", patch: { status: "done" } });

    expect(store.rows.size).toBe(3);
  });
});
