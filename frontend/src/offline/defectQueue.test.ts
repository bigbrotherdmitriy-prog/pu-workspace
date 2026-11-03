import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import type { OfflineCommand } from "./types";
const store = vi.hoisted(() => ({ rows: new Map<string, OfflineCommand>(), meta: new Map<string, unknown>(), failPut: false }));
vi.mock("../api/client", () => ({ api: vi.fn(), ApiError: class ApiError extends Error { constructor(message: string, public status: number | null, public requestId: string) { super(message); } } }));
vi.mock("./db", () => ({
  putCommand: vi.fn(async (row: OfflineCommand) => { if (store.failPut) throw new DOMException("Synthetic quota", "QuotaExceededError");store.rows.set(row.id, structuredClone(row)); }),
  deleteCommand: vi.fn(async (id: string) => { store.rows.delete(id); }),
  listCommands: vi.fn(async (user: number, project: number) => [...store.rows.values()].filter((row) => row.userId === user && row.projectId === project)),
  getMeta: vi.fn(async (key: string) => store.meta.get(key)),
  setMeta: vi.fn(async (key: string, value: unknown) => { store.meta.set(key, value); }),
}));
import { DEFECT_APPLIED_EVENT, enqueueDefectCreate, prepareDefectCreate, enqueuePreparedDefectCreate, submitPreparedDefectCreate, synchronize, recoverAuthRequired } from "./syncEngine";
const args = () => ({ userId: 1, projectId: 17, data: { title: "Synthetic defect", location: "Synthetic location", due_date: "2026-11-03" } });
const receipt = (body: Record<string, unknown>) => ({ receipt_id: 4, client_mutation_id: body.client_mutation_id, operation: "defect.create", entity_type: "defect", status: "applied", entity_id: 23, result: { id: 23, number: 7, project_id: body.project_id, author_user_id: 1 } });
beforeEach(() => { store.rows.clear();store.meta.clear();store.failPut=false;localStorage.clear();vi.mocked(api).mockReset();Object.defineProperty(navigator,"onLine",{configurable:true,value:true}); });

it("snapshots metadata without entity ids, photos or coalescing distinct defects", async () => {
  const input=args();const command=prepareDefectCreate(input);input.data.title="Changed input";
  expect(Object.isFrozen(command)).toBe(true);expect(Object.isFrozen(command.patch)).toBe(true);
  await enqueuePreparedDefectCreate(command);await enqueueDefectCreate(args());
  expect(store.rows.size).toBe(2);expect(command.patch.title).toBe("Synthetic defect");
  expect(command).not.toHaveProperty("entityId");expect(command).not.toHaveProperty("uploads");
});

it("sends the owner header and removes only a matching applied receipt", async () => {
  const id=await enqueueDefectCreate(args());const events:unknown[]=[];
  const listener=(event:Event)=>events.push((event as CustomEvent).detail);window.addEventListener(DEFECT_APPLIED_EVENT,listener);
  try {
    vi.mocked(api).mockImplementation(async(_path,init)=>receipt(JSON.parse(String(init?.body))));
    await synchronize(1,17);
    expect(store.rows.size).toBe(0);const[path,init]=vi.mocked(api).mock.calls[0];const body=JSON.parse(String(init?.body));
    expect(path).toBe("/mobile-sync/defect-create");expect(new Headers(init?.headers).get("X-PU-Expected-User")).toBe("1");
    expect(body).not.toHaveProperty("entity_id");expect(body).not.toHaveProperty("user_id");expect(body.data).toEqual(args().data);
    expect(events).toEqual([{userId:1,projectId:17,defectId:23,defectNumber:7,clientMutationId:id}]);
  } finally { window.removeEventListener(DEFECT_APPLIED_EVENT,listener); }
});

it("keeps the same command when direct response is lost and later queued", async () => {
  const command=prepareDefectCreate(args());let original="";
  vi.mocked(api).mockImplementationOnce(async(_path,init)=>{original=String(init?.body);throw new ApiError("Synthetic lost response",null,"synthetic");});
  await expect(submitPreparedDefectCreate(command)).rejects.toThrow();await enqueuePreparedDefectCreate(command);
  vi.mocked(api).mockImplementation(async(_path,init)=>{expect(String(init?.body)).toBe(original);return receipt(JSON.parse(original));});
  await synchronize(1,17);expect(store.rows.size).toBe(0);
});

it.each(["mutation","entity","project","author","number","receipt","status","type"])("retains the command on an invalid receipt %s",async(kind)=>{
  const id=await enqueueDefectCreate(args());
  vi.mocked(api).mockImplementation(async(_path,init)=>{
    const result=receipt(JSON.parse(String(init?.body)));
    if(kind==="mutation")result.client_mutation_id="synthetic-other-command";
    if(kind==="entity")result.entity_id=0;
    if(kind==="project")result.result.project_id=999;
    if(kind==="author")result.result.author_user_id=999;
    if(kind==="number")result.result.number=0;
    if(kind==="receipt")result.receipt_id=0;
    if(kind==="status")result.status="processing";
    if(kind==="type")result.entity_type="work_log";
    return result;
  });
  await synchronize(1,17);expect(store.rows.has(id)).toBe(true);
});

it("suspends another cookie session before the fake server creates a fact",async()=>{
  const id=await enqueueDefectCreate(args());let facts=0;
  vi.mocked(api).mockImplementation(async(_path,init)=>{
    const expected=new Headers(init?.headers).get("X-PU-Expected-User");
    if(expected!==null&&Number(expected)!==2)throw new ApiError("Synthetic account changed",403,"synthetic");
    facts+=1;const result=receipt(JSON.parse(String(init?.body)));result.result.author_user_id=2;return result;
  });
  await synchronize(1,17);await synchronize(1,17,true);
  expect(facts).toBe(0);expect(api).toHaveBeenCalledTimes(1);expect(store.rows.get(id)?.status).toBe("auth_required");
});

it("does not reset an existing suspended command or replace its payload",async()=>{
  const command=prepareDefectCreate(args());await enqueuePreparedDefectCreate(command);
  store.rows.set(command.id,{...command,status:"auth_required",attempts:2});
  await enqueuePreparedDefectCreate(command);expect(store.rows.get(command.id)?.status).toBe("auth_required");expect(store.rows.get(command.id)?.attempts).toBe(2);
  await expect(enqueuePreparedDefectCreate({...command,patch:{...command.patch,title:"Changed body"}})).rejects.toThrow();
  expect(store.rows.get(command.id)?.patch.title).toBe("Synthetic defect");
});

it("recovers only after explicit same-account verification and keeps the identity",async()=>{
  const id=await enqueueDefectCreate(args());vi.mocked(api).mockRejectedValue(new ApiError("Synthetic revoked session",403,"synthetic"));
  await synchronize(1,17);const bodyBefore=String(vi.mocked(api).mock.calls[0][1]?.body);
  vi.mocked(api).mockImplementation(async(path,init)=>path==="/auth/me"?{id:1}:receipt(JSON.parse(String(init?.body))));
  expect(await recoverAuthRequired(1,17)).toBe(1);expect(store.rows.get(id)?.attempts).toBe(1);
  await synchronize(1,17);expect(store.rows.size).toBe(0);
  expect(String(vi.mocked(api).mock.calls.at(-1)?.[1]?.body)).toBe(bodyBefore);
});

it("does not acknowledge quota failure and can persist the same prepared identity later",async()=>{
  const command=prepareDefectCreate(args());store.failPut=true;
  await expect(enqueuePreparedDefectCreate(command)).rejects.toThrow(/место/);expect(store.rows.size).toBe(0);
  store.failPut=false;await enqueuePreparedDefectCreate(command);expect(store.rows.has(command.id)).toBe(true);
});

it.each([{photos:["synthetic"]},{status:"accepted"},{title:" "},{description:NaN},{due_date:"2026-02-30"},{due_date:"0000-01-01"},{priority:"invalid"}])("rejects unsupported or invalid metadata before saving %j",async(data)=>{
  await expect(enqueueDefectCreate({...args(),data:{...args().data,...data}})).rejects.toThrow();expect(store.rows.size).toBe(0);
});

it.each([["title",500],["description",5000],["object_name",300],["location",300],["responsible",300]] as const)("validates the existing %s character cap without truncation",async(field,limit)=>{
  await expect(enqueueDefectCreate({...args(),data:{...args().data,[field]:"x".repeat(limit+1)}})).rejects.toThrow();
  const id=await enqueueDefectCreate({...args(),data:{...args().data,[field]:"x".repeat(limit)}});
  expect(store.rows.get(id)?.patch[field]).toBe("x".repeat(limit));
});

it("counts unicode characters consistently with the server",()=>{
  const title="😀".repeat(500);expect(prepareDefectCreate({...args(),data:{title}}).patch.title).toBe(title);
});

it("does not deliver another user's or project's metadata",async()=>{
  await enqueueDefectCreate(args());await synchronize(2,17);await synchronize(1,18);expect(api).not.toHaveBeenCalled();
});
