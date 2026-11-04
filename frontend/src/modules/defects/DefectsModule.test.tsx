// @ts-expect-error Vitest Node runtime supplies this builtin; browser TS config excludes Node types.
import { webcrypto } from "node:crypto";
// @ts-expect-error Vitest Node runtime supplies real structured-cloneable Blob/File, not browser production dependencies.
import { Blob as NativeBlob, File as NativeFile } from "node:buffer";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import { DefectsModule } from "./DefectsModule";
import { enqueuePreparedDefectCreate, synchronize } from "../../offline/syncEngine";
import { closeOfflineSession, resumeOfflineSession } from "../../offline/lifecycle";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
vi.mock("../../offline/syncEngine", async importOriginal => ({ ...(await importOriginal<typeof import("../../offline/syncEngine")>()), enqueuePreparedDefectCreate: vi.fn(), synchronize: vi.fn() }));
const mockApi = vi.mocked(api);
beforeEach(() => { vi.stubGlobal("crypto", webcrypto); vi.stubGlobal("Blob", NativeBlob); vi.stubGlobal("File", NativeFile); vi.mocked(synchronize).mockReset(); vi.mocked(synchronize).mockResolvedValue(); vi.mocked(enqueuePreparedDefectCreate).mockRejectedValue(new Error("Synthetic local storage unavailable")); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); mockApi.mockReset(); vi.mocked(enqueuePreparedDefectCreate).mockReset(); resumeOfflineSession(); Object.defineProperty(navigator, "onLine", { configurable: true, value: true }); });

it("never saves an old uncertain create after logout and another account signs in", async () => {
  mockAll(); const original = mockApi.getMockImplementation()!; let reject!: (reason: Error) => void;
  mockApi.mockImplementation(async (path, init) => path === "/mobile-sync/defect-create"
    ? new Promise((_, fail) => { reject = fail; }) : original(path, init));
  const view = render(<DefectsModule projectId={4} userId={1} />); await screen.findByText(/№ 7/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic old actor" } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  await waitFor(() => expect(mockApi.mock.calls.some(([path]) => path === "/mobile-sync/defect-create")).toBe(true));
  closeOfflineSession(); resumeOfflineSession(2); view.rerender(<DefectsModule projectId={4} userId={2} />);
  await act(async () => { reject(new ApiError("Synthetic old lost response", null, "synthetic-request")); });
  expect(enqueuePreparedDefectCreate).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Повторить неподтверждённое замечание" })).toBeNull();
});

it("binds metadata creation to the actual actor through the typed endpoint", async () => {
  mockAll();
  render(<DefectsModule projectId={4} userId={1} />);
  await screen.findByText(/№ 7/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic typed defect" } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  await waitFor(() => expect(mockApi.mock.calls.some(([path]) => path === "/mobile-sync/defect-create")).toBe(true));
});

it("persists metadata without photos offline and never pretends it has a server number", async () => {
  mockAll(); Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
  vi.mocked(enqueuePreparedDefectCreate).mockResolvedValue("synthetic-queued");
  render(<DefectsModule projectId={4} userId={1} />); await screen.findByText(/№ 7/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic offline metadata" } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  await waitFor(() => expect(enqueuePreparedDefectCreate).toHaveBeenCalledOnce());
  expect(mockApi.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  expect(screen.queryByText("Замечание № 8 добавлено")).toBeNull();
});

it("refuses create without an authenticated actor instead of inventing one", async () => {
  mockAll(); render(<DefectsModule projectId={4} />); await screen.findByText(/№ 7/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic missing actor" } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  expect((await screen.findByRole("alert")).textContent).toContain("Войдите заново");
  expect(mockApi.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
});

it("persists offline metadata and exact photo bytes together before clearing the draft", async () => {
 mockAll(); Object.defineProperty(navigator,"onLine",{configurable:true,value:false});vi.mocked(enqueuePreparedDefectCreate).mockResolvedValue("synthetic-local");
 render(<DefectsModule projectId={4} userId={1} />);await screen.findByText(/№ 7/);
 const title=screen.getByPlaceholderText(/не закреплён кабельный лоток/);fireEvent.change(title,{target:{value:"Synthetic offline photo"}});
 fireEvent.change(screen.getByLabelText("Фото замечания"),{target:{files:[new File(["synthetic"],"photo.jpg",{type:"image/jpeg"})]}});fireEvent.click(screen.getByText("Добавить замечание"));
 await waitFor(()=>expect(enqueuePreparedDefectCreate).toHaveBeenCalledOnce());await screen.findByText(/Замечание и исходные фото сохранены/);
 const command=vi.mocked(enqueuePreparedDefectCreate).mock.calls[0][0];expect(command.photos).toHaveLength(1);expect(await command.photos![0].blob.text()).toBe("synthetic");expect(command.photos![0].sha256).toMatch(/^[a-f0-9]{64}$/);
 expect(synchronize).not.toHaveBeenCalled();expect(mockApi.mock.calls.filter(([,init])=>init?.method==="POST")).toHaveLength(0);expect((title as HTMLInputElement).value).toBe("");
});

it("keeps metadata on local quota refusal without acknowledging persistence", async () => {
  mockAll(); Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
  render(<DefectsModule projectId={4} userId={1} />); await screen.findByText(/№ 7/);
  const title = screen.getByPlaceholderText(/не закреплён кабельный лоток/);
  fireEvent.change(title, { target: { value: "Synthetic quota draft" } }); fireEvent.click(screen.getByText("Добавить замечание"));
  await screen.findByRole("alert"); expect((title as HTMLInputElement).value).toBe("Synthetic quota draft");
  expect(screen.queryByText(/Замечание сохранено на устройстве/)).toBeNull();
});

it("retries the identical uncertain command while preserving a newer edited draft", async () => {
  mockAll(); const original = mockApi.getMockImplementation()!; let first = true;
  mockApi.mockImplementation(async (path, init) => {
    if (path === "/mobile-sync/defect-create" && first) { first = false; throw new ApiError("Synthetic lost reply", null, "synthetic-request"); }
    return original(path, init);
  });
  render(<DefectsModule projectId={4} userId={1} />); await screen.findByText(/№ 7/);
  const title = screen.getByPlaceholderText(/не закреплён кабельный лоток/);
  fireEvent.change(title, { target: { value: "Synthetic original" } }); fireEvent.click(screen.getByText("Добавить замечание"));
  await screen.findByRole("button", { name: "Повторить неподтверждённое замечание" });
  fireEvent.change(title, { target: { value: "Synthetic next draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Повторить неподтверждённое замечание" }));
  await screen.findByText("Замечание № 8 добавлено");
  const creates = mockApi.mock.calls.filter(([path]) => path === "/mobile-sync/defect-create");
  expect(creates).toHaveLength(2); expect(creates[0][1]?.body).toBe(creates[1][1]?.body);
  expect((title as HTMLInputElement).value).toBe("Synthetic next draft");
});

it("suspends an upload intent on account rejection rather than automatically resending it", async () => {
  mockAll(); const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, init) => { if (path === "/mobile-sync/defect-create") throw new ApiError("Synthetic other actor", 403, "synthetic-request"); return original(path, init); });
  vi.mocked(enqueuePreparedDefectCreate).mockResolvedValue("saved");
  render(<DefectsModule projectId={4} userId={1} />); await screen.findByText(/№ 7/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic owner intent" } });
  fireEvent.click(screen.getByText("Добавить замечание")); await waitFor(() => expect(enqueuePreparedDefectCreate).toHaveBeenCalledOnce());
  expect(vi.mocked(enqueuePreparedDefectCreate).mock.calls[0][0]).toMatchObject({ userId: 1, projectId: 4, status: "auth_required" });
  expect(mockApi.mock.calls.filter(([path]) => path === "/mobile-sync/defect-create")).toHaveLength(1);
});

it("only acknowledges synchronization events for its own queued actor, project and command", async () => {
  mockAll(); Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
  vi.mocked(enqueuePreparedDefectCreate).mockResolvedValue("saved");
  render(<DefectsModule projectId={4} userId={1} />); await screen.findByText(/№ 7/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic queued" } });
  fireEvent.click(screen.getByText("Добавить замечание")); await waitFor(() => expect(enqueuePreparedDefectCreate).toHaveBeenCalledOnce());
  const command = vi.mocked(enqueuePreparedDefectCreate).mock.calls[0][0];
  const detail = { userId: 1, projectId: 4, clientMutationId: command.id, defectNumber: 12 };
  act(() => { window.dispatchEvent(new CustomEvent("pu-defect-applied", { detail: { ...detail, userId: 2 } })); });
  expect(screen.queryByText("Замечание № 12 синхронизировано")).toBeNull();
  act(() => { window.dispatchEvent(new CustomEvent("pu-defect-applied", { detail })); });
  expect(await screen.findByText("Замечание № 12 синхронизировано")).toBeTruthy();
});

const defect = { id: 3, number: 7, title: "Не закреплён лоток", description: null, object_name: "ДУБНА", location: "Этаж 2", priority: "high", status: "open", status_label: "Открыто", responsible: "Иванов", due_date: "2026-10-01", overdue: true, photos: [{ id: 5, kind: "before", filename: "a.jpg" }], comments: [] };
const listing = { defects: [defect], counts: { open: 1, in_progress: 0, fixed: 0, accepted: 0 }, overdue: 1 };

function mockAll() {
  mockApi.mockImplementation(async (path: string, init?: { method?: string; body?: unknown }) => {
    if (path.endsWith("/status")) return { ...defect, status: "in_progress", status_label: "В работе" };
    if (path === "/mobile-sync/defect-create") {
      const body = JSON.parse(String(init?.body));
      return { receipt_id: 90, client_mutation_id: body.client_mutation_id, operation: "defect.create", entity_type: "defect", entity_id: 9, status: "applied",
        result: { id: 9, number: 8, project_id: body.project_id, author_user_id: 1 } };
    }
    if (/\/defects\/3$/.test(path)) return defect;
    return listing;
  });
}

it("lists defects with status counters, thumbnails and overdue mark", async () => {
  mockAll();
  render(<DefectsModule projectId={4} userId={1} />);
  expect(await screen.findByText(/№ 7\. Не закреплён лоток/)).toBeTruthy();
  expect(screen.getByText("Открыто: 1")).toBeTruthy();
  expect(screen.getAllByText(/Просрочено: 1/).length).toBeGreaterThan(0);
  expect(document.querySelector("img.df-thumb")?.getAttribute("src")).toBe("/projects/4/defects/3/photos/5");
});

it("queues photos before online synchronization and never falls back to legacy raw upload",async()=>{
 mockAll();vi.mocked(enqueuePreparedDefectCreate).mockResolvedValue("synthetic-queued");const fetchMock=vi.fn();vi.stubGlobal("fetch",fetchMock);
 render(<DefectsModule projectId={4} userId={1} />);await screen.findByText(/№ 7/);
 fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/),{target:{value:"Synthetic queued online photo"}});
 fireEvent.change(screen.getByLabelText("Фото замечания"),{target:{files:[new File(["x"],"p.jpg",{type:"image/jpeg"})]}});fireEvent.click(screen.getByText("Добавить замечание"));
 await waitFor(()=>expect(synchronize).toHaveBeenCalledWith(1,4,true));expect(enqueuePreparedDefectCreate).toHaveBeenCalledOnce();expect(fetchMock).not.toHaveBeenCalled();expect(vi.mocked(enqueuePreparedDefectCreate).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(synchronize).mock.invocationCallOrder[0]);
});

it("opens a defect and moves it to in progress", async () => {
  mockAll();
  render(<DefectsModule projectId={4} userId={1} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/));
  expect(await screen.findByRole("dialog")).toBeTruthy();
  fireEvent.click(screen.getByText("Взять в работу"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]).endsWith("/status"));
    expect(call).toBeTruthy();
    expect(String((call![1] as { body: string }).body)).toContain("in_progress");
  });
});

it("requires a description before saving", async () => {
  mockAll();
  render(<DefectsModule projectId={4} userId={1} />);
  await screen.findByText(/№ 7/);
  fireEvent.click(screen.getByText("Добавить замечание"));
  expect((await screen.findByRole("alert")).textContent).toContain("Опишите замечание");
});

it("shows the empty hint", async () => {
  mockApi.mockResolvedValue({ defects: [], counts: {}, overdue: 0 });
  render(<DefectsModule projectId={4} userId={1} />);
  expect(await screen.findByText(/Замечаний нет/)).toBeTruthy();
});


it("ignores a late defects list from the previous project", async () => {
  let finishOld!: (value: unknown) => void;
  const pending = new Promise<unknown>((resolve) => { finishOld = resolve; });
  const current = { ...listing, defects: [{ ...defect, title: "Current synthetic defect", photos: [] }] };
  mockApi.mockImplementation(async (path: string) => path.startsWith("/projects/4/") ? pending : current);
  const { rerender } = render(<DefectsModule projectId={4} userId={1} />);
  rerender(<DefectsModule projectId={5} userId={1} />);
  await screen.findByText(/Current synthetic defect/);
  await act(async () => { finishOld(listing); await pending; });
  expect(screen.queryByText(/Current synthetic defect/)).not.toBeNull();
  expect(screen.queryByText(/№ 7\. Не закреплён лоток/)).toBeNull();
});

it("hides already loaded defects while another project loads and preserves the draft", async () => {
  const pending = new Promise<never>(() => undefined);
  mockApi.mockImplementation(async (path: string) => path.startsWith("/projects/4/") ? listing : pending);
  const { rerender } = render(<DefectsModule projectId={4} userId={1} />);
  await screen.findByText(/№ 7\. Не закреплён лоток/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic unsaved defect" } });
  rerender(<DefectsModule projectId={5} userId={1} />);
  expect(screen.queryByText(/№ 7\. Не закреплён лоток/)).toBeNull();
  expect((screen.getByPlaceholderText(/не закреплён кабельный лоток/) as HTMLInputElement).value).toBe("Synthetic unsaved defect");
});


it("does not open an old project's late defect detail", async () => {
  let finish!: (value: unknown) => void;
  const pending = new Promise<unknown>((resolve) => { finish = resolve; });
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/projects/4/defects/3") return pending;
    return path.startsWith("/projects/4/") ? listing : { defects: [], counts: {}, overdue: 0 };
  });
  const { rerender } = render(<DefectsModule projectId={4} userId={1} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/));
  rerender(<DefectsModule projectId={5} userId={1} />);
  await act(async () => { finish(defect); await pending; });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("hides a loaded defect detail after switching project", async () => {
  mockAll();
  const { rerender } = render(<DefectsModule projectId={4} userId={1} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/));
  await screen.findByRole("dialog");
  rerender(<DefectsModule projectId={5} userId={1} />);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("keeps the newest chosen defect when earlier detail resolves later", async () => {
  let finishOld!: (value: unknown) => void;
  const pending = new Promise<unknown>((resolve) => { finishOld = resolve; });
  const next = { ...defect, id: 4, number: 8, title: "Synthetic newer detail", photos: [] };
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/defects/3")) return pending;
    if (path.endsWith("/defects/4")) return next;
    return { ...listing, defects: [defect, next] };
  });
  render(<DefectsModule projectId={4} userId={1} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/));
  fireEvent.click(screen.getByText(/№ 8\. Synthetic newer detail/));
  await screen.findByRole("dialog", { name: "Замечание 8" });
  await act(async () => { finishOld(defect); await pending; });
  expect(screen.queryByRole("dialog", { name: "Замечание 8" })).not.toBeNull();
  expect(screen.queryByRole("dialog", { name: "Замечание 7" })).toBeNull();
});


it("does not create server metadata when photo persistence fails",async()=>{
 mockAll();render(<DefectsModule projectId={4} userId={1} />);await screen.findByText(/№ 7/);
 const title=screen.getByPlaceholderText(/не закреплён кабельный лоток/);fireEvent.change(title,{target:{value:"Synthetic quota photo"}});
 fireEvent.change(screen.getByLabelText("Фото замечания"),{target:{files:[new File(["x"],"p.jpg",{type:"image/jpeg"})]}});fireEvent.click(screen.getByText("Добавить замечание"));await screen.findByRole("alert");
 expect(mockApi.mock.calls.filter(([,init])=>init?.method==="POST")).toHaveLength(0);expect(synchronize).not.toHaveBeenCalled();expect((title as HTMLInputElement).value).toBe("Synthetic quota photo");expect(screen.getByText("Выбрано фото: 1")).toBeTruthy();
});

it("keeps an edited draft and new selection while the earlier photo command persists",async()=>{
 mockAll();let finish!:(value:string)=>void;vi.mocked(enqueuePreparedDefectCreate).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockResolvedValue("synthetic-second");
 render(<DefectsModule projectId={4} userId={1} />);await screen.findByText(/№ 7/);const title=screen.getByPlaceholderText(/не закреплён кабельный лоток/);
 fireEvent.change(title,{target:{value:"Synthetic first"}});fireEvent.change(screen.getByLabelText("Фото замечания"),{target:{files:[new File(["old"],"old.jpg",{type:"image/jpeg"})]}});fireEvent.click(screen.getByText("Добавить замечание"));await waitFor(()=>expect(finish).toBeTypeOf("function"));
 fireEvent.change(title,{target:{value:"Synthetic next draft"}});fireEvent.change(screen.getByLabelText("Фото замечания"),{target:{files:[new File(["new"],"new.jpg",{type:"image/jpeg"})]}});await act(async()=>finish("synthetic-first"));
 expect((title as HTMLInputElement).value).toBe("Synthetic next draft");fireEvent.click(screen.getByText("Добавить замечание"));await waitFor(()=>expect(enqueuePreparedDefectCreate).toHaveBeenCalledTimes(2));expect(vi.mocked(enqueuePreparedDefectCreate).mock.calls[1][0].photos![0].name).toBe("new.jpg");
});

it("does not update another project after the old photo command finishes persisting",async()=>{
 mockAll();let finish!:(value:string)=>void;vi.mocked(enqueuePreparedDefectCreate).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 const view=render(<DefectsModule projectId={4} userId={1} />);await screen.findByText(/№ 7/);const title=screen.getByPlaceholderText(/не закреплён кабельный лоток/);
 fireEvent.change(title,{target:{value:"Synthetic old project"}});fireEvent.change(screen.getByLabelText("Фото замечания"),{target:{files:[new File(["old"],"old.jpg",{type:"image/jpeg"})]}});fireEvent.click(screen.getByText("Добавить замечание"));await waitFor(()=>expect(finish).toBeTypeOf("function"));
 view.rerender(<DefectsModule projectId={5} userId={1} />);fireEvent.change(title,{target:{value:"Synthetic other project draft"}});await act(async()=>finish("synthetic-old"));
 expect((title as HTMLInputElement).value).toBe("Synthetic other project draft");expect(screen.queryByText(/Замечание и исходные фото сохранены/)).toBeNull();expect(synchronize).not.toHaveBeenCalled();expect(vi.mocked(enqueuePreparedDefectCreate).mock.calls[0][0].projectId).toBe(4);
});

it("does not acknowledge a create response without a valid persisted defect id", async () => {
  mockAll();
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, init) => path === "/mobile-sync/defect-create" && init?.method === "POST"
    ? { id: 0, number: 8 } : original(path, init));
  const fetchMock = vi.fn();vi.stubGlobal("fetch", fetchMock);
  render(<DefectsModule projectId={4} userId={1} />);
  await screen.findByText(/№ 7\. Не закреплён лоток/);
  const title = screen.getByPlaceholderText(/не закреплён кабельный лоток/);
  fireEvent.change(title, { target: { value: "Synthetic unconfirmed response" } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  expect((await screen.findByRole("alert")).textContent).toContain("подтвердить создание");
  expect((title as HTMLInputElement).value).toBe("Synthetic unconfirmed response");
  expect(screen.queryByText("Замечание № 8 добавлено")).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();
});

it.each(["status", "comment", "delete"])("ignores a late %s mutation after the selected project changes", async (kind) => {
  let finish!: (value: unknown) => void;
  const pending = new Promise<unknown>((resolve) => { finish = resolve; });
  mockAll(); const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, init) => {
    if ((kind === "status" && path.endsWith("/status")) || (kind === "comment" && path.endsWith("/comments")) || (kind === "delete" && init?.method === "DELETE")) return pending;
    return original(path, init);
  });
  const { rerender } = render(<DefectsModule projectId={4} userId={1} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/)); await screen.findByRole("dialog");
  if (kind === "status") fireEvent.click(screen.getByText("Взять в работу"));
  else if (kind === "comment") {
    fireEvent.change(screen.getByPlaceholderText(/Комментарий/), { target: { value: "Synthetic old comment" } });
    fireEvent.click(screen.getByText("Только комментарий"));
  } else fireEvent.click(screen.getByText("Удалить замечание"));
  rerender(<DefectsModule projectId={5} userId={1} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/)); await screen.findByRole("dialog");
  await act(async () => { finish({ ...defect, title: "Synthetic stale mutation result" }); await pending; });
  expect(screen.queryByRole("dialog")).not.toBeNull();
  expect(screen.queryByText(/Synthetic stale mutation result/)).toBeNull();
});
