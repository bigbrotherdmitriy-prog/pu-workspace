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

import { enqueueNotificationRead, enqueueTaskUpdate, offlineSummary, resolveConflict, synchronize } from "./syncEngine";

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

type Resolution = "keep_server" | "apply_local" | "latest_write_wins";

function ackFor(
  commandId: number,
  conflictId: number,
  entityType: string,
  entityId: number,
  resolution: string,
) {
  return {
    id: conflictId,
    command_id: commandId,
    entity_type: entityType,
    entity_id: entityId,
    status: "resolved",
    resolution,
  };
}

async function seedConflict(
  opts: { operation?: "task.update" | "notification.mark_read"; receiptId: number; conflictId: number; userId?: number; projectId?: number },
): Promise<{ id: string; entityId: number; entityType: string }> {
  const userId = opts.userId ?? 1;
  const projectId = opts.projectId ?? 17;
  const operation = opts.operation ?? "task.update";
  const entityId = operation === "notification.mark_read" ? 44 : 9;
  const id = operation === "notification.mark_read"
    ? await enqueueNotificationRead({ userId, projectId, notificationId: entityId })
    : await enqueueTaskUpdate({ userId, projectId, taskId: entityId, baseToken: "signed-base", patch: { status: "cancelled" } });
  const row = store.rows.get(id)!;
  store.rows.set(id, { ...row, status: "conflict", receiptId: opts.receiptId, conflictId: opts.conflictId });
  return { id, entityId, entityType: entityTypeFor(operation) };
}

describe("resolveConflict ACK guard", () => {
  it("accepts a valid matching ACK, clears exactly its own row and updates meta", async () => {
    const seeded = await seedConflict({ receiptId: 22, conflictId: 71 });
    const other = await seedConflict({ receiptId: 23, conflictId: 72 });
    vi.mocked(api).mockImplementation(async () => ackFor(22, 71, "task", 9, "keep_server"));

    await resolveConflict(1, 17, 71, "keep_server");

    expect(store.rows.has(seeded.id)).toBe(false);
    expect(store.rows.has(other.id)).toBe(true);
    expect(store.meta.has("last-synced:1:17")).toBe(true);
  });

  it("accepts a genuine resolved replay carrying an earlier different valid resolution", async () => {
    const seeded = await seedConflict({ receiptId: 22, conflictId: 71 });
    vi.mocked(api).mockImplementation(async () => ackFor(22, 71, "task", 9, "apply_local"));

    await resolveConflict(1, 17, 71, "keep_server");

    expect(store.rows.has(seeded.id)).toBe(false);
    expect(store.meta.has("last-synced:1:17")).toBe(true);
  });

  it("accepts a valid notification conflict ACK", async () => {
    const seeded = await seedConflict({ operation: "notification.mark_read", receiptId: 24, conflictId: 73 });
    vi.mocked(api).mockImplementation(async () => ackFor(24, 73, "notification", 44, "latest_write_wins"));

    await resolveConflict(1, 17, 73, "latest_write_wins");

    expect(store.rows.has(seeded.id)).toBe(false);
    expect(store.meta.has("last-synced:1:17")).toBe(true);
  });

  it("preserves unrelated actor and project rows while resolving one conflict", async () => {
    const target = await seedConflict({ receiptId: 22, conflictId: 71 });
    const otherActor = await seedConflict({ receiptId: 25, conflictId: 74, userId: 2 });
    const otherProject = await seedConflict({ receiptId: 26, conflictId: 75, projectId: 18 });
    vi.mocked(api).mockImplementation(async () => ackFor(22, 71, "task", 9, "keep_server"));

    await resolveConflict(1, 17, 71, "keep_server");

    expect(store.rows.has(target.id)).toBe(false);
    expect(store.rows.has(otherActor.id)).toBe(true);
    expect(store.rows.has(otherProject.id)).toBe(true);
  });

  it("accepts a valid server-only conflict with no local row and updates scoped last-synced", async () => {
    vi.mocked(api).mockImplementation(async () => ackFor(30, 90, "task", 9, "keep_server"));

    await resolveConflict(1, 17, 90, "keep_server");

    expect(store.rows.size).toBe(0);
    expect(store.meta.has("last-synced:1:17")).toBe(true);
  });

  type Bad = { name: string; ack: () => unknown };
  const malformed: Bad[] = [
    { name: "null", ack: () => null },
    { name: "empty object", ack: () => ({}) },
    { name: "array", ack: () => [] },
    { name: "wrong conflict id", ack: () => ackFor(22, 999, "task", 9, "keep_server") },
    { name: "invalid conflict id", ack: () => ackFor(22, 0, "task", 9, "keep_server") },
    { name: "invalid command_id", ack: () => ackFor(0, 71, "task", 9, "keep_server") },
    { name: "wrong command_id", ack: () => ackFor(999, 71, "task", 9, "keep_server") },
    { name: "wrong entity_type", ack: () => ackFor(22, 71, "notification", 9, "keep_server") },
    { name: "unknown entity_type", ack: () => ackFor(22, 71, "document", 9, "keep_server") },
    { name: "wrong entity_id", ack: () => ackFor(22, 71, "task", 999, "keep_server") },
    { name: "unresolved status", ack: () => ({ ...ackFor(22, 71, "task", 9, "keep_server"), status: "unresolved" }) },
    { name: "missing resolution", ack: () => { const a = ackFor(22, 71, "task", 9, "keep_server") as Record<string, unknown>; delete a.resolution; return a; } },
    { name: "unsupported resolution", ack: () => ackFor(22, 71, "task", 9, "merge_fields") },
  ];

  it.each(malformed)("rejects malformed ACK: $name and retains original row and last-synced marker", async ({ ack }) => {
    const seeded = await seedConflict({ receiptId: 22, conflictId: 71 });
    const before = structuredClone(store.rows.get(seeded.id)!);
    store.meta.set("last-synced:1:17", "preexisting-marker");
    vi.mocked(api).mockImplementation(async () => ack() as never);

    await expect(resolveConflict(1, 17, 71, "keep_server")).rejects.toBeDefined();

    expect(store.rows.get(seeded.id)).toEqual(before);
    expect(store.meta.get("last-synced:1:17")).toBe("preexisting-marker");
  });

  it.each([
    { name: "zero userId", userId: 0 },
    { name: "negative userId", userId: -1 },
    { name: "noninteger userId", userId: 1.5 },
    { name: "zero projectId", projectId: 0 },
    { name: "negative projectId", projectId: -3 },
    { name: "zero conflictId", conflictId: 0 },
    { name: "negative conflictId", conflictId: -7 },
    { name: "noninteger conflictId", conflictId: 2.5 },
  ])("rejects invalid positive identity input before any API call: $name", async ({ userId, projectId, conflictId }) => {
    const seeded = await seedConflict({ receiptId: 22, conflictId: 71 });
    const before = structuredClone(store.rows.get(seeded.id)!);
    vi.mocked(api).mockImplementation(async () => ackFor(22, 71, "task", 9, "keep_server"));

    await expect(resolveConflict(
      userId ?? 1, projectId ?? 17, conflictId ?? 71, "keep_server",
    )).rejects.toBeDefined();

    expect(api).not.toHaveBeenCalled();
    expect(store.rows.get(seeded.id)).toEqual(before);
  });

  it("rejects an unsupported resolution before any API call", async () => {
    const seeded = await seedConflict({ receiptId: 22, conflictId: 71 });
    const before = structuredClone(store.rows.get(seeded.id)!);
    vi.mocked(api).mockImplementation(async () => ackFor(22, 71, "task", 9, "keep_server"));

    await expect(resolveConflict(1, 17, 71, "merge_fields" as Resolution)).rejects.toBeDefined();

    expect(api).not.toHaveBeenCalled();
    expect(store.rows.get(seeded.id)).toEqual(before);
  });

  it("does not delete a row whose receiptId changed before the ACK arrives", async () => {
    const seeded = await seedConflict({ receiptId: 22, conflictId: 71 });
    vi.mocked(api).mockImplementation(async () => {
      const current = store.rows.get(seeded.id)!;
      store.rows.set(seeded.id, { ...current, receiptId: 99 });
      return ackFor(22, 71, "task", 9, "keep_server");
    });

    await resolveConflict(1, 17, 71, "keep_server");

    const row = store.rows.get(seeded.id)!;
    expect(row).toBeDefined();
    expect(row.receiptId).toBe(99);
    expect(store.meta.has("last-synced:1:17")).toBe(false);
  });
});
