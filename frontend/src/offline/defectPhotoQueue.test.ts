// @ts-expect-error Vitest Node runtime supplies this builtin; browser TS config excludes Node types.
import { webcrypto } from "node:crypto";
// @ts-expect-error Vitest Node runtime supplies real structured-cloneable Blob/File, not browser production dependencies.
import { Blob as NativeBlob, File as NativeFile } from "node:buffer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import type { OfflineCommand } from "./types";
import { closeOfflineSession, resumeOfflineSession } from "./lifecycle";
import { prepareDefectCreateWithPhotos, enqueuePreparedDefectCreate, synchronize, DEFECT_APPLIED_EVENT } from "./syncEngine";
import { prepareDefectPhotos, checkDefectPhotos } from "./defectPhotos";
const store=vi.hoisted(()=>({rows:new Map<string,OfflineCommand>(),fail:false}));
vi.mock("../api/client",()=>({api:vi.fn(),ApiError:class ApiError extends Error {constructor(message:string,public status:number|null,public requestId:string){super(message);}}}));
vi.mock("./db",()=>({putCommand:vi.fn(async(row:OfflineCommand)=>{if(store.fail)throw new DOMException("Synthetic quota","QuotaExceededError");store.rows.set(row.id,structuredClone(row));}),deleteCommand:vi.fn(async(id:string)=>{store.rows.delete(id);}),listCommands:vi.fn(async(user:number,project:number)=>[...store.rows.values()].filter(r=>r.userId===user&&r.projectId===project)),getMeta:vi.fn(),setMeta:vi.fn()}));
const args=()=>({userId:1,projectId:17,data:{title:"SYNTHETIC photo defect"},files:[new File(["synthetic-one"],"one.png",{type:"image/png"}),new File(["synthetic-two"],"two.png",{type:"image/png"})]});
beforeEach(()=>{resumeOfflineSession(1);vi.stubGlobal("crypto",webcrypto);vi.stubGlobal("Blob",NativeBlob);vi.stubGlobal("File",NativeFile);store.rows.clear();store.fail=false;vi.mocked(api).mockReset();localStorage.clear();Object.defineProperty(navigator,"onLine",{configurable:true,value:true});});
afterEach(()=>{vi.unstubAllGlobals();resumeOfflineSession();});
function metadata(init?:RequestInit){const b=JSON.parse(String(init?.body));expect(b).not.toHaveProperty("photos");expect(b.data).not.toHaveProperty("photos");return {status:"applied",receipt_id:4,client_mutation_id:b.client_mutation_id,operation:"defect.create",entity_type:"defect",entity_id:23,result:{id:23,number:7,project_id:17,author_user_id:1}};}
function ack(command:Awaited<ReturnType<typeof prepareDefectCreateWithPhotos>>,init?:RequestInit){const headers=new Headers(init?.headers);const p=command.photos!.find(p=>p.operationKey===headers.get("X-PU-Photo-Operation"))!;expect(headers.get("X-PU-Expected-User")).toBe("1");expect(init?.body).toBeInstanceOf(Blob);return {status:"applied",receipt_id:10,photo_id:20,project_id:17,defect_id:23,user_id:1,operation_key:p.operationKey,sha256:p.sha256,size_bytes:p.blob.size,kind:p.kind,filename:p.name,mime_type:p.mime};}
it("retains exact blobs on partial acknowledgement and retries unchanged keys after metadata replay",async()=>{
 const c=await prepareDefectCreateWithPhotos(args());await enqueuePreparedDefectCreate(c);let fail=true;const attempts:string[]=[];const applied=vi.fn();window.addEventListener(DEFECT_APPLIED_EVENT,applied);
 vi.mocked(api).mockImplementation(async(path,init)=>{if(path==="/mobile-sync/defect-create")return metadata(init);const key=new Headers(init?.headers).get("X-PU-Photo-Operation")!;attempts.push(key);if(fail&&key===c.photos![1].operationKey)throw new ApiError("Synthetic lost photo response",null,"synthetic");return ack(c,init);});
 try {await synchronize(1,17);expect(store.rows.has(c.id)).toBe(true);expect(applied).not.toHaveBeenCalled();const saved=store.rows.get(c.id);if(saved?.operation!=="defect.create")throw Error("wrong queue row");expect(await saved.photos![1].blob.text()).toBe("synthetic-two");fail=false;await synchronize(1,17,true);expect(store.rows.size).toBe(0);expect(attempts).toEqual([c.photos![0].operationKey,c.photos![1].operationKey,c.photos![0].operationKey,c.photos![1].operationKey]);expect(applied).toHaveBeenCalledOnce();} finally {window.removeEventListener(DEFECT_APPLIED_EVENT,applied);}
});
it.each(["user_id","project_id","defect_id","operation_key","sha256","size_bytes","kind","filename","mime_type","photo_id","receipt_id","status"])("keeps all local data if photo acknowledgement mismatches %s",async(field)=>{
 const c=await prepareDefectCreateWithPhotos({...args(),files:args().files.slice(0,1)});await enqueuePreparedDefectCreate(c);
 vi.mocked(api).mockImplementation(async(path,init)=>{if(path==="/mobile-sync/defect-create")return metadata(init);const r:Record<string,unknown>=ack(c,init);r[field]=typeof r[field]==="number"?0:"wrong";return r;});await synchronize(1,17);expect(store.rows.has(c.id)).toBe(true);
});
it("does not acknowledge quota failure or send anything before durable admission",async()=>{
 const c=await prepareDefectCreateWithPhotos(args());store.fail=true;await expect(enqueuePreparedDefectCreate(c)).rejects.toThrow(/место/);expect(store.rows.size).toBe(0);expect(api).not.toHaveBeenCalled();
});
it("rejects a changed stored blob before photo transmission",async()=>{
 const c=await prepareDefectCreateWithPhotos(args());await expect(checkDefectPhotos([{...c.photos![0],blob:new Blob(["tampered"])}])).rejects.toThrow(/отпечатка/);
 await enqueuePreparedDefectCreate(c);await expect(enqueuePreparedDefectCreate({...c,photos:[{...c.photos![0],name:"changed.png"},c.photos![1]]})).rejects.toThrow(/другими данными/);
});
it("stops photo loop and cannot resurrect queue after logout with a late acknowledgement",async()=>{
 const c=await prepareDefectCreateWithPhotos(args());await enqueuePreparedDefectCreate(c);let finish!:(r:unknown)=>void;let posts=0;
 vi.mocked(api).mockImplementation(async(path,init)=>{if(path==="/mobile-sync/defect-create")return metadata(init);posts++;return new Promise(resolve=>{finish=resolve;});});const pending=synchronize(1,17);await vi.waitFor(()=>expect(finish).toBeTypeOf("function"));closeOfflineSession();store.rows.clear();resumeOfflineSession(2);finish(ack(c,{body:c.photos![0].blob,headers:{"X-PU-Photo-Operation":c.photos![0].operationKey,"X-PU-Expected-User":"1"}}));await pending;expect(posts).toBe(1);expect(store.rows.size).toBe(0);
});
it("suspends deleted/tombstoned photo rather than claiming complete delivery",async()=>{
 const c=await prepareDefectCreateWithPhotos(args());await enqueuePreparedDefectCreate(c);vi.mocked(api).mockImplementation(async(path,init)=>{if(path==="/mobile-sync/defect-create")return metadata(init);throw new ApiError("Synthetic deleted photo",409,"synthetic");});await synchronize(1,17);expect(store.rows.has(c.id)).toBe(true);expect(store.rows.get(c.id)?.lastError).toContain("deleted");
});
it("checks complete count, MIME and byte budgets before saving or sending",async()=>{
 const f=()=>new File([new Uint8Array(8*1024*1024)],"large.png",{type:"image/png"});await expect(prepareDefectPhotos([f(),f(),f()])).rejects.toThrow(/20 МБ/);await expect(prepareDefectPhotos(Array(13).fill(args().files[0]))).rejects.toThrow(/12/);await expect(prepareDefectPhotos([new File(["x"],"x.html",{type:"text/html"})])).rejects.toThrow();expect(api).not.toHaveBeenCalled();expect(store.rows.size).toBe(0);
});
