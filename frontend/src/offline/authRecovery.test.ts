import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import type { OfflineCommand } from "./types";
const store = vi.hoisted(() => ({ rows: new Map<string, OfflineCommand>(), meta: new Map<string, unknown>() }));
vi.mock("../api/client",()=>({api:vi.fn(),ApiError:class ApiError extends Error{constructor(message:string,public status:number|null,public requestId:string){super(message);}}}));
vi.mock("./db",()=>({putCommand:vi.fn(async(row:OfflineCommand)=>{store.rows.set(row.id,structuredClone(row));}),deleteCommand:vi.fn(async(id:string)=>{store.rows.delete(id);}),listCommands:vi.fn(async(userId:number,projectId:number)=>[...store.rows.values()].filter(row=>row.userId===userId&&row.projectId===projectId)),getMeta:vi.fn(async(key:string)=>store.meta.get(key)),setMeta:vi.fn(async(key:string,value:unknown)=>{store.meta.set(key,value);})}));
import { enqueueWorkLogCreate, recoverAuthRequired, synchronize } from "./syncEngine";
beforeEach(()=>{store.rows.clear();store.meta.clear();vi.mocked(api).mockReset();Object.defineProperty(navigator,"onLine",{configurable:true,value:true});});
async function seed(userId=1,projectId=17){
  const id=await enqueueWorkLogCreate({userId,projectId,data:{work_date:"2026-10-30",work_name:"Synthetic recovery",qty:"1"}});
  const row=store.rows.get(id)!;store.rows.set(id,{...row,status:"auth_required",attempts:1,lastError:"Synthetic auth rejection"});return id;
}
it("explicitly revalidates the same account and preserves immutable command fields",async()=>{
  const id=await seed();const before=structuredClone(store.rows.get(id)!);
  vi.mocked(api).mockResolvedValue({id:1});
  expect(await recoverAuthRequired(1,17)).toBe(1);
  expect(api).toHaveBeenCalledWith("/auth/me");
  const after=store.rows.get(id)!;expect(after.status).toBe("queued");
  for(const key of ["id","userId","projectId","deviceId","operation","patch","clientCreatedAt","attempts"] as const)expect(after[key]).toEqual(before[key]);
  expect(vi.mocked(api).mock.calls.some(([,init])=>init?.method==="POST")).toBe(false);
});
it("does not resume a previous user's commands under a different current account",async()=>{
  const id=await seed();vi.mocked(api).mockResolvedValue({id:2});
  await expect(recoverAuthRequired(1,17)).rejects.toThrow();
  expect(store.rows.get(id)?.status).toBe("auth_required");
});
it("retains authentication failures without changing persisted state",async()=>{
  const id=await seed();const before=structuredClone(store.rows.get(id)!);
  vi.mocked(api).mockRejectedValue(new ApiError("Synthetic unauthorized",401,"synthetic-request"));
  await expect(recoverAuthRequired(1,17)).rejects.toThrow();
  expect(store.rows.get(id)).toEqual(before);
});
it("does not change other user or project queues",async()=>{
  const own=await seed();const otherUser=await seed(2,17);const otherProject=await seed(1,18);
  vi.mocked(api).mockResolvedValue({id:1});await recoverAuthRequired(1,17);
  expect(store.rows.get(own)?.status).toBe("queued");
  expect(store.rows.get(otherUser)?.status).toBe("auth_required");
  expect(store.rows.get(otherProject)?.status).toBe("auth_required");
});
it("preserves a server permission denial after explicit account recovery",async()=>{
  const id=await seed();vi.mocked(api).mockResolvedValue({id:1});await recoverAuthRequired(1,17);
  vi.mocked(api).mockRejectedValue(new ApiError("Synthetic permission still denied",403,"synthetic-request"));
  await synchronize(1,17,true);
  expect(store.rows.get(id)?.status).toBe("auth_required");
  await synchronize(1,17,true);expect(api).toHaveBeenCalledTimes(2);
});
it("never retries authentication while offline",async()=>{
  const id=await seed();Object.defineProperty(navigator,"onLine",{configurable:true,value:false});
  await expect(recoverAuthRequired(1,17)).rejects.toThrow();
  expect(api).not.toHaveBeenCalled();expect(store.rows.get(id)?.status).toBe("auth_required");
});
it("does nothing when no command requires recovery",async()=>{
  expect(await recoverAuthRequired(1,17)).toBe(0);expect(api).not.toHaveBeenCalled();
});
