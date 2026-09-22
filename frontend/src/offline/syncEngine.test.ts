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

    vi.mocked(api).mockResolvedValue({ receipt_id: 9, status: "applied" });
    await synchronize(1, 17, true);
    expect(store.rows.size).toBe(0);
  });

  it("preserves a real server conflict for explicit user resolution", async () => {
    await enqueueTaskUpdate({
      userId: 1, projectId: 17, taskId: 9, baseToken: "signed-base",
      patch: { status: "cancelled" },
    });
    vi.mocked(api).mockResolvedValue({
      receipt_id: 22,
      status: "conflict",
      conflict: { id: 71 },
    });

    await synchronize(1, 17, true);

    const row = [...store.rows.values()][0];
    expect(row.status).toBe("conflict");
    expect(row.receiptId).toBe(22);
    expect(row.conflictId).toBe(71);
    expect((await offlineSummary(1, 17)).conflicts).toBe(1);
  });
});
