import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
