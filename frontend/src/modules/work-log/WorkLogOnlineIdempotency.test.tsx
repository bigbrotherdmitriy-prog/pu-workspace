import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import type { OfflineCommand } from "../../offline/types";
const store=vi.hoisted(()=>({rows:new Map<string,OfflineCommand>(),meta:new Map<string,unknown>(),storageFails:false,lostOnce:false,validationFails:false,facts:new Map<string,Record<string,unknown>>()}));
vi.mock("../../api/client",async(original)=>({...await original<typeof import("../../api/client")>(),api:vi.fn()}));
vi.mock("../../offline/db",()=>({putCommand:vi.fn(async(row:OfflineCommand)=>{if(store.storageFails)throw new Error("Synthetic storage unavailable");store.rows.set(row.id,structuredClone(row));}),deleteCommand:vi.fn(async(id:string)=>{store.rows.delete(id);}),listCommands:vi.fn(async(user:number,project:number)=>[...store.rows.values()].filter(row=>row.userId===user&&row.projectId===project)),getMeta:vi.fn(async(key:string)=>store.meta.get(key)),setMeta:vi.fn(async(key:string,value:unknown)=>{store.meta.set(key,value);})}));
import { synchronize } from "../../offline/syncEngine";
import { WorkLogModule } from "./WorkLogModule";
beforeEach(()=>{
 store.rows.clear();store.meta.clear();store.facts.clear();store.storageFails=false;store.lostOnce=false;store.validationFails=false;
 Object.defineProperty(navigator,"onLine",{configurable:true,value:true});
 vi.mocked(api).mockImplementation(async(path:string,init?:RequestInit)=>{
  if(path==="/mobile-sync/work-log-create"){
   if(store.validationFails)throw new ApiError("Synthetic invalid quantity",422,"synthetic-request");
   const body=JSON.parse(String(init?.body));
   if(!store.facts.has(body.client_mutation_id))store.facts.set(body.client_mutation_id,body);
   if(store.lostOnce){store.lostOnce=false;throw new ApiError("Synthetic response lost after commit",null,"synthetic-request");}
   return {receipt_id:5,client_mutation_id:body.client_mutation_id,operation:"work_log.create",status:"applied",entity_type:"work_log",entity_id:23,result:{id:23,project_id:5,author_user_id:1}};
  }
  if(init?.method==="POST")throw new Error("Legacy non-idempotent POST must not be used");
  if(path.endsWith("/crews"))return {crews:[]};if(path.endsWith("/schedule"))return {baseline:null,items:[]};if(path.endsWith("/suggestions"))return {works:[],objects:[],rd_items:[]};
  return {entries:[],summary:{entries:0,days:0,by_work:[]}};
 });
});
afterEach(()=>{cleanup();vi.mocked(api).mockReset();});
async function fill(){await screen.findByRole("form",{name:"Новая запись журнала"});fireEvent.change(screen.getByLabelText("Дата"),{target:{value:"2026-10-30"}});fireEvent.change(screen.getByLabelText(/Какая работа выполнена/),{target:{value:"Synthetic online fact"}});fireEvent.change(screen.getByLabelText("Объём"),{target:{value:"1.250"}});}
const posts=()=>vi.mocked(api).mock.calls.filter(([,init])=>init?.method==="POST");
it("routes an uncertain online result into the same durable mutation without duplicating a fact",async()=>{
 store.lostOnce=true;render(<WorkLogModule projectId={5} userId={1}/>);await fill();fireEvent.click(screen.getByText("Добавить запись"));
 await waitFor(()=>expect(store.rows.size).toBe(1));expect(store.facts.size).toBe(1);
 expect(await screen.findByText(/Запись сохранена на устройстве/)).toBeTruthy();
 await act(async()=>synchronize(1,5,true));
 expect(store.rows.size).toBe(0);expect(store.facts.size).toBe(1);
 expect(posts().length).toBe(2);expect(String(posts()[0][1]?.body)).toBe(String(posts()[1][1]?.body));
 expect(posts().every(([path])=>path==="/mobile-sync/work-log-create")).toBe(true);
});
it("keeps a memory-only pending command and explicitly retries its original data when storage is unavailable",async()=>{
 store.lostOnce=true;store.storageFails=true;render(<WorkLogModule projectId={5} userId={1}/>);await fill();fireEvent.click(screen.getByText("Добавить запись"));
 const retry=await screen.findByRole("button",{name:"Повторить неподтверждённую запись"});
 expect(screen.getByText("Добавить запись")).toBeDisabled();expect(store.facts.size).toBe(1);
 fireEvent.change(screen.getByLabelText(/Какая работа выполнена/),{target:{value:"Different unsent draft"}});
 fireEvent.submit(screen.getByRole("form",{name:"Новая запись журнала"}));expect(posts().length).toBe(1);
 fireEvent.click(retry);await screen.findByText("Неподтверждённая запись добавлена");
 expect(store.facts.size).toBe(1);expect(posts().length).toBe(2);
 expect(String(posts()[0][1]?.body)).toBe(String(posts()[1][1]?.body));
 expect(screen.getByLabelText(/Какая работа выполнена/)).toHaveProperty("value","Different unsent draft");
 expect(screen.getByText("Добавить запись")).not.toBeDisabled();
});
it("preserves the form on a definitive initial validation error without queueing a new fact",async()=>{
 store.validationFails=true;render(<WorkLogModule projectId={5} userId={1}/>);await fill();fireEvent.click(screen.getByText("Добавить запись"));
 expect(await screen.findByRole("alert")).toHaveProperty("textContent","Synthetic invalid quantity");
 expect(store.facts.size).toBe(0);expect(store.rows.size).toBe(0);
 expect(screen.getByLabelText(/Какая работа выполнена/)).toHaveProperty("value","Synthetic online fact");
});
it("acknowledges a matching online receipt without requiring local queue storage",async()=>{
 store.storageFails=true;render(<WorkLogModule projectId={5} userId={1}/>);await fill();fireEvent.click(screen.getByText("Добавить запись"));
 expect(await screen.findByText("Запись добавлена")).toBeTruthy();expect(store.facts.size).toBe(1);expect(store.rows.size).toBe(0);
 expect(posts()[0][0]).toBe("/mobile-sync/work-log-create");
});
