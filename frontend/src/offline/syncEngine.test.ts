import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { OfflineCommand } from "./types";

const store = vi.hoisted(() => ({ rows: new Map<string, OfflineCommand>(), meta: new Map<string, unknown>() }));

vi.mock("../api/client", () => ({
  api: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number | null;
    constructor(message: string, status: number | null) { super(message); this.status = status; }
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

function matchingReceipt(receiptId: number, status = "applied") {
  return async (_path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body));
    const entityType = body.operation === "task.update" ? "task" : "notification";
    return { receipt_id: receiptId, client_mutation_id: body.client_mutation_id, user_id: 1, project_id: 17,
      operation: body.operation, entity_type: entityType, entity_id: body.entity_id, status,
      conflict: status === "conflict" ? { id: 71, command_id: receiptId, entity_type: entityType, entity_id: body.entity_id } : null };
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

    vi.mocked(api).mockImplementation(matchingReceipt(9));
    await synchronize(1, 17, true);
    expect(store.rows.size).toBe(0);
  });

  it("preserves a real server conflict for explicit user resolution", async () => {
    await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "cancelled" },
    });
    vi.mocked(api).mockImplementation(matchingReceipt(22, "conflict"));

    await synchronize(1, 17, true);

    const row = [...store.rows.values()][0];
    expect(row.status).toBe("conflict");
    expect(row.receiptId).toBe(22);
    expect(row.conflictId).toBe(71);
    expect((await offlineSummary(1, 17)).conflicts).toBe(1);
  });

  it("does not coalesce into an attempted command after a network failure", async () => {
    const firstId = await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });
    const before = structuredClone(store.rows.get(firstId)!);
    vi.mocked(api).mockRejectedValue(new ApiError("network unavailable", null, "offline-test"));
    await synchronize(1, 17, true);

    const attempted = structuredClone(store.rows.get(firstId)!);
    expect(attempted.attempts).toBe(1);
    expect(attempted.status).toBe("queued");

    const secondId = await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { assignee_user_id: 3 },
    });

    expect(secondId).not.toBe(firstId);
    expect(store.rows.size).toBe(2);
    const untouched = store.rows.get(firstId)!;
    expect(untouched.id).toBe(before.id);
    expect(untouched.patch).toEqual(before.patch);
    expect(untouched.baseToken).toBe(before.baseToken);
    expect(untouched.clientCreatedAt).toBe(before.clientCreatedAt);
    expect(untouched.attempts).toBe(1);
    expect(untouched.status).toBe("queued");
    expect(store.rows.get(secondId)!.patch).toEqual({ assignee_user_id: 3 });
  });

  it("retries the original id with a byte-identical JSON payload", async () => {
    const id = await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });
    vi.mocked(api).mockRejectedValueOnce(new ApiError("network unavailable", null, "offline-test"));
    await synchronize(1, 17, true);
    const firstBody = vi.mocked(api).mock.calls[0][1]?.body as string;

    vi.mocked(api).mockImplementationOnce(matchingReceipt(5));
    await synchronize(1, 17, true);
    const secondBody = vi.mocked(api).mock.calls[1][1]?.body as string;

    expect(JSON.parse(firstBody).client_mutation_id).toBe(id);
    expect(secondBody).toBe(firstBody);
    expect(store.rows.size).toBe(0);
  });

  it("does not coalesce edits across users or projects", async () => {
    await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });
    await enqueueTaskUpdate({
      userId: 2, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });
    await enqueueTaskUpdate({
      userId: 1, projectId: 18, taskId: 9, baseToken: "signed-base",
      patch: { status: "in_progress" },
    });

    expect(store.rows.size).toBe(3);
    expect((await offlineSummary(1, 17)).total).toBe(1);
    expect((await offlineSummary(2, 17)).total).toBe(1);
    expect((await offlineSummary(1, 18)).total).toBe(1);
  });

  it.each(["client_mutation_id", "operation", "entity_id", "entity_type", "user_id", "project_id", "receipt_id"])("retains commands on mismatched %s", async field => {
    const id = await enqueueNotificationRead({ userId: 1, projectId: 17, notificationId: 44 });
    vi.mocked(api).mockImplementation(async (path, init) => ({ ...await matchingReceipt(9)(path, init), [field]: "wrong-synthetic-value" }));
    await synchronize(1, 17);
    expect(store.rows.has(id)).toBe(true); expect(store.rows.get(id)?.status).toBe("queued");
  });

  it("retains a conflict command if the conflict points to another receipt", async () => {
    const id = await enqueueTaskUpdate({ userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base", patch: { status: "cancelled" } });
    vi.mocked(api).mockImplementation(async (path, init) => ({ ...await matchingReceipt(22, "conflict")(path, init), conflict: { id: 71, command_id: 999, entity_type: "task", entity_id: 9 } }));
    await synchronize(1, 17);
    expect(store.rows.get(id)?.status).toBe("queued");
  });
});
