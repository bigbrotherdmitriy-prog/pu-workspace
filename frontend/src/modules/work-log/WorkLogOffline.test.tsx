import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { isOnline } from "../../offline/connectivity";
import { enqueueWorkLogCreate, WORK_LOG_APPLIED_EVENT } from "../../offline/syncEngine";
import { WorkLogModule } from "./WorkLogModule";
vi.mock("../../api/client", async (original) => ({ ...(await original<typeof import("../../api/client")>()), api: vi.fn() }));
vi.mock("../../offline/connectivity", () => ({ isOnline: vi.fn() }));
vi.mock("../../offline/syncEngine", () => ({ enqueueWorkLogCreate: vi.fn(), WORK_LOG_APPLIED_EVENT:"pu-work-log-applied" }));
beforeEach(() => {
  vi.mocked(isOnline).mockReturnValue(false);
  vi.mocked(api).mockImplementation(async (path: string) => {
    if(path.endsWith("/crews"))return {crews:[]};
    if(path.endsWith("/schedule"))return {baseline:null,items:[]};
    if(path.endsWith("/suggestions"))return {works:[],objects:[],rd_items:[]};
    return {entries:[],summary:{entries:0,days:0,by_work:[]}};
  });
});
afterEach(() => { cleanup(); vi.mocked(api).mockReset(); vi.mocked(enqueueWorkLogCreate).mockReset(); });
async function fill() {
  await screen.findByRole("form", {name:"Новая запись журнала"});
  fireEvent.change(screen.getByLabelText("Дата"),{target:{value:"2026-10-30"}});
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/),{target:{value:"Synthetic offline work"}});
  fireEvent.change(screen.getByLabelText("Объём"),{target:{value:"1,250"}});
}
it("acknowledges local saving only after persistence, without the online POST",async()=>{
  let resolveSave!: (id:string)=>void;
  vi.mocked(enqueueWorkLogCreate).mockImplementation(()=>new Promise(resolve=>{resolveSave=resolve;}));
  render(<WorkLogModule projectId={5} userId={7}/>);await fill();
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(()=>expect(enqueueWorkLogCreate).toHaveBeenCalledWith({userId:7,projectId:5,data:expect.objectContaining({work_date:"2026-10-30",work_name:"Synthetic offline work",qty:"1,250"})}));
  expect(screen.queryByText(/Запись сохранена на устройстве/)).toBeNull();
  await act(async()=>resolveSave("synthetic-mutation"));
  expect(await screen.findByText(/Запись сохранена на устройстве/)).toBeTruthy();
  expect(screen.queryByText("Запись добавлена")).toBeNull();
  expect(vi.mocked(api).mock.calls.some(([,init])=>init?.method==="POST")).toBe(false);
});
it("keeps the entered fact when local storage rejects persistence",async()=>{
  vi.mocked(enqueueWorkLogCreate).mockRejectedValue(new Error("Synthetic storage failure"));
  render(<WorkLogModule projectId={5} userId={7}/>);await fill();fireEvent.click(screen.getByText("Добавить запись"));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent","Synthetic storage failure");
  expect(screen.getByLabelText(/Какая работа выполнена/)).toHaveProperty("value","Synthetic offline work");
  expect(screen.getByLabelText("Объём")).toHaveProperty("value","1,250");
});
it("does not invent a user identity for offline persistence",async()=>{
  render(<WorkLogModule projectId={5}/>);await fill();fireEvent.click(screen.getByText("Добавить запись"));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent","Войдите заново, чтобы сохранить запись на устройстве.");
  expect(enqueueWorkLogCreate).not.toHaveBeenCalled();
});
it("refreshes only for an applied fact of the same user and project",async()=>{
  render(<WorkLogModule projectId={5} userId={7}/>);await fill();
  await waitFor(()=>expect(api).toHaveBeenCalled());const before=vi.mocked(api).mock.calls.length;
  await act(async()=>window.dispatchEvent(new CustomEvent(WORK_LOG_APPLIED_EVENT,{detail:{userId:8,projectId:5,entryId:23}})));
  expect(vi.mocked(api).mock.calls.length).toBe(before);
  await act(async()=>window.dispatchEvent(new CustomEvent(WORK_LOG_APPLIED_EVENT,{detail:{userId:7,projectId:5,entryId:23}})));
  await waitFor(()=>expect(vi.mocked(api).mock.calls.length).toBeGreaterThan(before));
});
async function settleLoads() {
  await screen.findByRole("form", {name:"Новая запись журнала"});
  await act(async()=>{ await Promise.resolve(); await Promise.resolve(); });
}
it.each([
  {label:"user switch", fromUser:7, toUser:8, project:5},
  {label:"project switch", fromUser:7, toUser:7, project:6},
])("does not leak a deferred save into a re-rendered $label scope", async({fromUser,toUser,project})=>{
  let resolveSave!: (id:string)=>void;
  vi.mocked(enqueueWorkLogCreate).mockImplementation(()=>new Promise(resolve=>{resolveSave=resolve;}));
  const {rerender} = render(<WorkLogModule projectId={project} userId={fromUser}/>);
  await fill();
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(()=>expect(enqueueWorkLogCreate).toHaveBeenCalledWith({userId:fromUser,projectId:project,data:expect.objectContaining({work_name:"Synthetic offline work",qty:"1,250"})}));
  rerender(<WorkLogModule projectId={project} userId={toUser}/>);
  await settleLoads();
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/),{target:{value:"Synthetic new scope work"}});
  fireEvent.change(screen.getByLabelText("Объём"),{target:{value:"9,500"}});
  await act(async()=>resolveSave("synthetic-mutation"));
  await act(async()=>{ await Promise.resolve(); await Promise.resolve(); });
  expect(enqueueWorkLogCreate).toHaveBeenCalledTimes(1);
  expect(enqueueWorkLogCreate).toHaveBeenCalledWith({userId:fromUser,projectId:project,data:expect.objectContaining({work_name:"Synthetic offline work",qty:"1,250"})});
  expect(screen.getByLabelText(/Какая работа выполнена/)).toHaveProperty("value","Synthetic new scope work");
  expect(screen.getByLabelText("Объём")).toHaveProperty("value","9,500");
  expect(screen.queryByText(/Запись сохранена на устройстве/)).toBeNull();
  expect(screen.queryByText("Запись добавлена")).toBeNull();
  expect(vi.mocked(api).mock.calls.some(([,init])=>init?.method==="POST")).toBe(false);
});
it("refreshes only for an applied fact of the current re-rendered scope",async()=>{
  const {rerender} = render(<WorkLogModule projectId={5} userId={7}/>);
  await settleLoads();
  rerender(<WorkLogModule projectId={5} userId={8}/>);
  await settleLoads();
  await act(async()=>{ await Promise.resolve(); await Promise.resolve(); });
  const before=vi.mocked(api).mock.calls.length;
  await act(async()=>window.dispatchEvent(new CustomEvent(WORK_LOG_APPLIED_EVENT,{detail:{userId:7,projectId:5,entryId:31}})));
  await act(async()=>{ await Promise.resolve(); await Promise.resolve(); });
  expect(vi.mocked(api).mock.calls.length).toBe(before);
  await act(async()=>window.dispatchEvent(new CustomEvent(WORK_LOG_APPLIED_EVENT,{detail:{userId:8,projectId:5,entryId:31}})));
  await waitFor(()=>expect(vi.mocked(api).mock.calls.length).toBeGreaterThan(before));
});
