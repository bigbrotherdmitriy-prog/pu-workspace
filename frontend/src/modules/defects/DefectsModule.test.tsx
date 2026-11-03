import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { DefectsModule } from "./DefectsModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); mockApi.mockReset(); });

const defect = { id: 3, number: 7, title: "Не закреплён лоток", description: null, object_name: "ДУБНА", location: "Этаж 2", priority: "high", status: "open", status_label: "Открыто", responsible: "Иванов", due_date: "2026-10-01", overdue: true, photos: [{ id: 5, kind: "before", filename: "a.jpg" }], comments: [] };
const listing = { defects: [defect], counts: { open: 1, in_progress: 0, fixed: 0, accepted: 0 }, overdue: 1 };

function mockAll() {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (path.endsWith("/status")) return { ...defect, status: "in_progress", status_label: "В работе" };
    if (path.endsWith("/defects") && init?.method === "POST") return { ...defect, id: 9, number: 8 };
    if (/\/defects\/3$/.test(path)) return defect;
    return listing;
  });
}

it("lists defects with status counters, thumbnails and overdue mark", async () => {
  mockAll();
  render(<DefectsModule projectId={4} />);
  expect(await screen.findByText(/№ 7\. Не закреплён лоток/)).toBeTruthy();
  expect(screen.getByText("Открыто: 1")).toBeTruthy();
  expect(screen.getAllByText(/Просрочено: 1/).length).toBeGreaterThan(0);
  expect(document.querySelector("img.df-thumb")?.getAttribute("src")).toBe("/projects/4/defects/3/photos/5");
});

it("creates a defect and uploads the photo with the CSRF token", async () => {
  document.cookie = "pu_csrf=tok-d";
  mockAll();
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  vi.stubGlobal("fetch", fetchMock);
  render(<DefectsModule projectId={4} />);
  await screen.findByText(/№ 7/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Трещина" } });
  fireEvent.change(screen.getByLabelText("Фото замечания"), { target: { files: [new File(["x"], "p.jpg", { type: "image/jpeg" })] } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(String(fetchMock.mock.calls[0][0])).toContain("/projects/4/defects/9/photos?name=p.jpg&kind=before");
  expect((fetchMock.mock.calls[0][1] as { headers: Record<string, string> }).headers["X-CSRF-Token"]).toBe("tok-d");
  expect(await screen.findByText("Замечание № 8 добавлено")).toBeTruthy();
  document.cookie = "pu_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

it("opens a defect and moves it to in progress", async () => {
  mockAll();
  render(<DefectsModule projectId={4} />);
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
  render(<DefectsModule projectId={4} />);
  await screen.findByText(/№ 7/);
  fireEvent.click(screen.getByText("Добавить замечание"));
  expect((await screen.findByRole("alert")).textContent).toContain("Опишите замечание");
});

it("shows the empty hint", async () => {
  mockApi.mockResolvedValue({ defects: [], counts: {}, overdue: 0 });
  render(<DefectsModule projectId={4} />);
  expect(await screen.findByText(/Замечаний нет/)).toBeTruthy();
});


it("ignores a late defects list from the previous project", async () => {
  let finishOld!: (value: unknown) => void;
  const pending = new Promise<unknown>((resolve) => { finishOld = resolve; });
  const current = { ...listing, defects: [{ ...defect, title: "Current synthetic defect", photos: [] }] };
  mockApi.mockImplementation(async (path: string) => path.startsWith("/projects/4/") ? pending : current);
  const { rerender } = render(<DefectsModule projectId={4} />);
  rerender(<DefectsModule projectId={5} />);
  await screen.findByText(/Current synthetic defect/);
  await act(async () => { finishOld(listing); await pending; });
  expect(screen.queryByText(/Current synthetic defect/)).not.toBeNull();
  expect(screen.queryByText(/№ 7\. Не закреплён лоток/)).toBeNull();
});

it("hides already loaded defects while another project loads and preserves the draft", async () => {
  const pending = new Promise<never>(() => undefined);
  mockApi.mockImplementation(async (path: string) => path.startsWith("/projects/4/") ? listing : pending);
  const { rerender } = render(<DefectsModule projectId={4} />);
  await screen.findByText(/№ 7\. Не закреплён лоток/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic unsaved defect" } });
  rerender(<DefectsModule projectId={5} />);
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
  const { rerender } = render(<DefectsModule projectId={4} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/));
  rerender(<DefectsModule projectId={5} />);
  await act(async () => { finish(defect); await pending; });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("hides a loaded defect detail after switching project", async () => {
  mockAll();
  const { rerender } = render(<DefectsModule projectId={4} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/));
  await screen.findByRole("dialog");
  rerender(<DefectsModule projectId={5} />);
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
  render(<DefectsModule projectId={4} />);
  fireEvent.click(await screen.findByText(/№ 7\. Не закреплён лоток/));
  fireEvent.click(screen.getByText(/№ 8\. Synthetic newer detail/));
  await screen.findByRole("dialog", { name: "Замечание 8" });
  await act(async () => { finishOld(defect); await pending; });
  expect(screen.queryByRole("dialog", { name: "Замечание 8" })).not.toBeNull();
  expect(screen.queryByRole("dialog", { name: "Замечание 7" })).toBeNull();
});


it("does not create the same defect again after a known create succeeds but photo upload fails", async () => {
  mockAll();
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, init) => path === "/projects/4/defects/9" ? { ...defect, id: 9, number: 8, photos: [] } : original(path, init));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: "Synthetic photo failure" }) }));
  render(<DefectsModule projectId={4} />);
  await screen.findByText(/№ 7\. Не закреплён лоток/);
  fireEvent.change(screen.getByPlaceholderText(/не закреплён кабельный лоток/), { target: { value: "Synthetic created defect" } });
  fireEvent.change(screen.getByLabelText("Фото замечания"), { target: { files: [new File(["x"], "synthetic.jpg", { type: "image/jpeg" })] } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  await screen.findByRole("alert");
  fireEvent.click(await screen.findByText("Добавить замечание"));
  const creates = () => mockApi.mock.calls.filter(([path, init]) => path === "/projects/4/defects" && init?.method === "POST");
  expect(creates()).toHaveLength(1);
  expect(screen.getByText(/Не все фото удалось загрузить/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Открыть созданное замечание" }));
  expect(await screen.findByRole("dialog", { name: "Замечание 8" })).toBeTruthy();
  expect(creates()).toHaveLength(1);
});

it("keeps a new draft and newly chosen photo when an earlier created defect's upload fails", async () => {
  mockAll();
  let rejectUpload!: (reason: unknown) => void;
  const pending = new Promise<unknown>((_, reject) => { rejectUpload = reject; });
  const fetchMock = vi.fn().mockImplementationOnce(() => pending).mockResolvedValue({ ok: true, status: 200 });
  vi.stubGlobal("fetch", fetchMock);
  render(<DefectsModule projectId={4} />);
  await screen.findByText(/№ 7\. Не закреплён лоток/);
  const title = screen.getByPlaceholderText(/не закреплён кабельный лоток/);
  fireEvent.change(title, { target: { value: "Synthetic first defect" } });
  fireEvent.change(screen.getByLabelText("Фото замечания"), { target: { files: [new File(["old"], "old.jpg", { type: "image/jpeg" })] } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  fireEvent.change(title, { target: { value: "Synthetic next draft" } });
  fireEvent.change(screen.getByLabelText("Фото замечания"), { target: { files: [new File(["new"], "new.jpg", { type: "image/jpeg" })] } });
  await act(async () => { rejectUpload(new Error("Synthetic old upload failure")); await pending.catch(() => undefined); });
  expect((title as HTMLInputElement).value).toBe("Synthetic next draft");
  fireEvent.click(await screen.findByText("Добавить замечание"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(String(fetchMock.mock.calls[1][0])).toContain("name=new.jpg");
});


it("keeps another project's draft intact when an old photo upload fails", async () => {
  mockAll();
  let rejectUpload!: (reason: unknown) => void;
  const pending = new Promise<unknown>((_, reject) => { rejectUpload = reject; });
  const fetchMock = vi.fn().mockImplementation(() => pending);
  vi.stubGlobal("fetch", fetchMock);
  const { rerender } = render(<DefectsModule projectId={4} />);
  await screen.findByText(/№ 7\. Не закреплён лоток/);
  const title = screen.getByPlaceholderText(/не закреплён кабельный лоток/);
  fireEvent.change(title, { target: { value: "Synthetic original project defect" } });
  fireEvent.change(screen.getByLabelText("Фото замечания"), { target: { files: [new File(["x"], "old-project.jpg", { type: "image/jpeg" })] } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  rerender(<DefectsModule projectId={5} />);
  fireEvent.change(title, { target: { value: "Synthetic other project draft" } });
  await act(async () => { rejectUpload(new Error("Synthetic old scope upload failure")); await pending.catch(() => undefined); });
  expect((title as HTMLInputElement).value).toBe("Synthetic other project draft");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: "Открыть созданное замечание" })).toBeNull();
  expect(String(fetchMock.mock.calls[0][0])).toContain("/projects/4/defects/9/photos");
  rerender(<DefectsModule projectId={4} />);
  expect(screen.getByRole("button", { name: "Открыть созданное замечание" })).toBeTruthy();
});

it("does not acknowledge a create response without a valid persisted defect id", async () => {
  mockAll();
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, init) => path === "/projects/4/defects" && init?.method === "POST"
    ? { id: 0, number: 8 } : original(path, init));
  const fetchMock = vi.fn();vi.stubGlobal("fetch", fetchMock);
  render(<DefectsModule projectId={4} />);
  await screen.findByText(/№ 7\. Не закреплён лоток/);
  const title = screen.getByPlaceholderText(/не закреплён кабельный лоток/);
  fireEvent.change(title, { target: { value: "Synthetic unconfirmed response" } });
  fireEvent.click(screen.getByText("Добавить замечание"));
  expect((await screen.findByRole("alert")).textContent).toContain("подтвердить создание");
  expect((title as HTMLInputElement).value).toBe("Synthetic unconfirmed response");
  expect(screen.queryByText("Замечание № 8 добавлено")).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();
});
