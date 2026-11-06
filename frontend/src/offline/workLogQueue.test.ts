import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import type { OfflineCommand } from "./types";
const store = vi.hoisted(() => ({ rows: new Map<string, OfflineCommand>(), meta: new Map<string, unknown>() }));
vi.mock("../api/client", () => ({ api: vi.fn(), ApiError: class ApiError extends Error { constructor(message: string, public status: number | null, public requestId: string) { super(message); } } }));
vi.mock("./db", () => ({
  putCommand: vi.fn(async (row: OfflineCommand) => { store.rows.set(row.id, structuredClone(row)); }),
  deleteCommand: vi.fn(async (id: string) => { store.rows.delete(id); }),
  listCommands: vi.fn(async (userId: number, projectId: number) => [...store.rows.values()].filter((row) => row.userId === userId && row.projectId === projectId)),
  getMeta: vi.fn(async (key: string) => store.meta.get(key)),
  setMeta: vi.fn(async (key: string, value: unknown) => { store.meta.set(key, value); }),
}));
import { enqueueWorkLogCreate, prepareWorkLogCreate, enqueuePreparedWorkLogCreate, submitPreparedWorkLogCreate, synchronize } from "./syncEngine";
const args = () => ({ userId: 1, projectId: 17, data: { work_date: "2026-10-30", work_name: "Synthetic work", qty: "1.250", labor: [{ person_name: "Synthetic worker", hours: "2" }] } });
const validReceipt = (body: Record<string, unknown>) => ({ receipt_id: 5, client_mutation_id: body.client_mutation_id, operation: "work_log.create", status: "applied", entity_type: "work_log", entity_id: 23, result: { id: 23, project_id: body.project_id, author_user_id: 1 } });
beforeEach(() => { store.rows.clear(); store.meta.clear(); vi.mocked(api).mockReset(); Object.defineProperty(navigator,"onLine",{ configurable: true,value: true }); });
it("snapshots explicit-date journal bodies without merging separate facts or inventing entity ids", async () => {
  const data = args(); const id = await enqueueWorkLogCreate(data);
  data.data.labor[0].hours = "9";
  await enqueueWorkLogCreate(args());
  expect(store.rows.size).toBe(2);
  const saved = store.rows.get(id)!;
  expect(saved.operation).toBe("work_log.create");
  expect(saved).not.toHaveProperty("entityId");
  expect(saved.patch.labor).toEqual([{person_name:"Synthetic worker",hours:"2"}]);
  expect(saved.patch.work_date).toBe("2026-10-30");
});
it("sends the immutable create envelope and removes a fact only on a matching applied receipt", async () => {
  await enqueueWorkLogCreate(args());
  vi.mocked(api).mockImplementation(async (_path, init) => validReceipt(JSON.parse(String(init?.body))));
  await synchronize(1,17);
  expect(store.rows.size).toBe(0);
  const [path, init] = vi.mocked(api).mock.calls[0];
  const body = JSON.parse(String(init?.body));
  expect(path).toBe("/mobile-sync/work-log-create");
  expect(body).not.toHaveProperty("entity_id");
  expect(body).not.toHaveProperty("user_id");
  expect(body.data).toEqual(args().data);
});
it("retains the identical mutation/body/date after an ambiguous lost response", async () => {
  const id = await enqueueWorkLogCreate(args());
  let firstBody = "";
  vi.mocked(api).mockImplementation(async (_path, init) => { firstBody = String(init?.body); throw new ApiError("Synthetic connection loss",null,"synthetic-request"); });
  await synchronize(1,17);
  expect(store.rows.get(id)?.status).toBe("queued");
  vi.mocked(api).mockImplementation(async (_path, init) => { expect(String(init?.body)).toBe(firstBody); return validReceipt(JSON.parse(firstBody)); });
  await synchronize(1,17);
  expect(store.rows.size).toBe(0);
});
it.each([401,403])("keeps rejected authentication %s for explicit recovery without auto replay", async (status) => {
  const id = await enqueueWorkLogCreate(args());
  vi.mocked(api).mockRejectedValue(new ApiError("Synthetic auth failure",status,"synthetic-request"));
  await synchronize(1,17);
  expect(store.rows.get(id)?.status).toBe("auth_required");
  await synchronize(1,17,true);
  expect(api).toHaveBeenCalledTimes(1);
});
it.each(["wrong_mutation","wrong_entity","wrong_project","missing_receipt","wrong_author"])("retains a queued fact on a malformed receipt: %s", async (kind) => {
  const id = await enqueueWorkLogCreate(args());
  vi.mocked(api).mockImplementation(async (_path,init) => {
    const r = validReceipt(JSON.parse(String(init?.body)));
    if (kind==="wrong_mutation") r.client_mutation_id="different-synthetic-mutation";
    if (kind==="wrong_entity") r.entity_id=0;
    if (kind==="wrong_project") r.result.project_id=999;
    if (kind==="missing_receipt") r.receipt_id=0;
    if (kind==="wrong_author") r.result.author_user_id=999;
    return r;
  });
  await synchronize(1,17);
  expect(store.rows.has(id)).toBe(true);
});
it("never delivers another user's or project's queued facts", async () => {
  await enqueueWorkLogCreate(args());
  await synchronize(2,17); await synchronize(1,18);
  expect(api).not.toHaveBeenCalled(); expect(store.rows.size).toBe(1);
});
it.each(["","2026-02-30","2026-1-03","0000-01-01"])("rejects a noncanonical explicit work date %s before persistence", async (work_date) => {
  await expect(enqueueWorkLogCreate({...args(),data:{...args().data,work_date}})).rejects.toThrow();
  expect(store.rows.size).toBe(0);
});

it("retries the same prepared online create after a lost response without regenerating identity", async () => {
  const original = args(); const command = prepareWorkLogCreate(original);
  original.data.labor[0].hours = "9";
  expect(store.rows.size).toBe(0);
  let firstBody = "";
  vi.mocked(api).mockImplementationOnce(async (_path, init) => { firstBody=String(init?.body); throw new ApiError("Synthetic lost online response",null,"synthetic-request"); });
  await expect(submitPreparedWorkLogCreate(command)).rejects.toThrow();
  vi.mocked(api).mockImplementationOnce(async (_path, init) => { expect(String(init?.body)).toBe(firstBody); return validReceipt(JSON.parse(firstBody)); });
  expect(await submitPreparedWorkLogCreate(command)).toEqual({receiptId:5,entityId:23});
  expect(JSON.parse(firstBody).data.labor[0].hours).toBe("2");
});
it("moves an uncertain prepared online create into the durable queue with the same envelope", async () => {
  const command=prepareWorkLogCreate(args()); let body="";
  vi.mocked(api).mockImplementationOnce(async (_path,init)=>{body=String(init?.body);throw new ApiError("Synthetic lost response",null,"synthetic-request");});
  await expect(submitPreparedWorkLogCreate(command)).rejects.toThrow();
  expect(await enqueuePreparedWorkLogCreate(command)).toBe(command.id);
  vi.mocked(api).mockImplementation(async(_path,init)=>{expect(String(init?.body)).toBe(body);return validReceipt(JSON.parse(body));});
  await synchronize(1,17);expect(store.rows.size).toBe(0);
});
it("does not reset a suspended prepared command when it is queued again", async () => {
  const command=prepareWorkLogCreate(args());await enqueuePreparedWorkLogCreate(command);
  vi.mocked(api).mockRejectedValue(new ApiError("Synthetic denied",403,"synthetic-request"));await synchronize(1,17);
  await enqueuePreparedWorkLogCreate(command);expect(store.rows.get(command.id)?.status).toBe("auth_required");
});
it("rejects changed data under an already queued prepared mutation identity", async () => {
  const command=prepareWorkLogCreate(args());await enqueuePreparedWorkLogCreate(command);
  await expect(enqueuePreparedWorkLogCreate({...command,patch:{...command.patch,qty:"9"}})).rejects.toThrow();
  expect(store.rows.get(command.id)?.patch.qty).toBe("1.250");
});
it("keeps prepared nested data immutable after the first request is prepared", async () => {
  const command=prepareWorkLogCreate(args());
  try {(command.patch.labor as {hours:string}[])[0].hours="9";} catch { /* Immutable snapshots may reject writes. */ }
  vi.mocked(api).mockImplementation(async(_path,init)=>validReceipt(JSON.parse(String(init?.body))));
  await submitPreparedWorkLogCreate(command);
  const body=JSON.parse(String(vi.mocked(api).mock.calls[0][1]?.body));expect(body.data.labor[0].hours).toBe("2");
});


it("does not deliver an old owner's queued create under another cookie session", async () => {
  const id = await enqueueWorkLogCreate(args());
  const actualSessionUser = 2;
  let wrongOwnerFacts = 0;
  vi.mocked(api).mockImplementation(async (_path, init) => {
    const expectedOwner = new Headers(init?.headers).get("X-PU-Expected-User");
    if (expectedOwner !== null && Number(expectedOwner) !== actualSessionUser) {
      throw new ApiError("Synthetic session owner changed", 403, "synthetic");
    }
    wrongOwnerFacts += 1;
    const receipt = validReceipt(JSON.parse(String(init?.body)));
    receipt.result.author_user_id = actualSessionUser;
    return receipt;
  });
  await synchronize(1, 17);
  expect(wrongOwnerFacts).toBe(0);
  expect(store.rows.get(id)?.status).toBe("auth_required");
  expect(new Headers(vi.mocked(api).mock.calls[0][1]?.headers).get("X-PU-Expected-User")).toBe("1");
});

it.each([
  "wrong_operation",
  "status_queued",
  "entity_type_task",
  "result_id_mismatch",
  "fractional_receipt_id",
  "unsafe_receipt_id",
  "fractional_entity_id",
  "unsafe_entity_id",
])("retains the queued journal fact on a malformed create receipt: %s", async (kind) => {
  const id = await enqueueWorkLogCreate(args());
  const before = store.rows.get(id)!;
  const snapshot = {
    id: before.id,
    clientCreatedAt: before.clientCreatedAt,
    patch: structuredClone(before.patch),
    userId: before.userId,
    projectId: before.projectId,
  };
  store.meta.set("last-synced:1:17", "old-marker");
  vi.mocked(api).mockImplementation(async (_path, init) => {
    const body = JSON.parse(String(init?.body));
    const r = validReceipt(body);
    if (kind === "wrong_operation") r.operation = "task.update";
    if (kind === "status_queued") r.status = "queued";
    if (kind === "entity_type_task") r.entity_type = "task";
    if (kind === "result_id_mismatch") r.result.id = 24;
    if (kind === "fractional_receipt_id") r.receipt_id = 1.5;
    if (kind === "unsafe_receipt_id") r.receipt_id = Number.MAX_SAFE_INTEGER + 1;
    if (kind === "fractional_entity_id") { r.entity_id = 1.5; r.result.id = 1.5; }
    if (kind === "unsafe_entity_id") { r.entity_id = Number.MAX_SAFE_INTEGER + 1; r.result.id = Number.MAX_SAFE_INTEGER + 1; }
    return r;
  });
  await synchronize(1, 17);
  const after = store.rows.get(id);
  expect(after).toBeDefined();
  expect(after!.id).toBe(snapshot.id);
  expect(after!.clientCreatedAt).toBe(snapshot.clientCreatedAt);
  expect(after!.patch).toEqual(snapshot.patch);
  expect(after!.patch.work_date).toBe("2026-10-30");
  expect(after!.userId).toBe(snapshot.userId);
  expect(after!.projectId).toBe(snapshot.projectId);
  expect(after).not.toHaveProperty("entityId");
  expect(after).not.toHaveProperty("deleteCommand");
  expect(store.meta.get("last-synced:1:17")).toBe("old-marker");
});
