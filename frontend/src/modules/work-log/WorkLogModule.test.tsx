import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { WorkLogModule } from "./WorkLogModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); mockApi.mockReset(); });

const entry = { id: 1, work_date: "2026-10-28", object_name: "ДУБНА", work_name: "Прокладка кабеля", unit: "м", qty: "120.000", location: "Этаж 2", weather: null, workers: 3, note: null };
const listing = { entries: [entry], summary: { entries: 1, days: 1, by_work: [] } };
const suggestions = { works: [{ work_name: "Прокладка кабеля", unit: "м" }], objects: ["ДУБНА"], rd_items: [] };

function mockApiOk() {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (path.includes("/suggestions")) return suggestions;
    if (init?.method === "POST") return { id: 2 };
    if (init?.method === "DELETE") return { deleted: 1 };
    return listing;
  });
}

it("lists entries with summary", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  expect(await screen.findByText("Прокладка кабеля")).toBeTruthy();
  expect(screen.getByText(/Записей: 1/)).toBeTruthy();
});

it("adds an entry from the phone form", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/), { target: { value: "Монтаж щита" } });
  fireEvent.change(screen.getByLabelText("Объём"), { target: { value: "2" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    expect(String((call![1] as { body: string }).body)).toContain("Монтаж щита");
  });
  expect(await screen.findByText("Запись добавлена")).toBeTruthy();
});

it("refuses an empty work name", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.click(screen.getByText("Добавить запись"));
  expect((await screen.findByRole("alert")).textContent).toContain("Укажите, какая работа");
});

it("deletes an entry and shows the empty state", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  fireEvent.click(await screen.findByLabelText("Удалить запись Прокладка кабеля"));
  await waitFor(() => expect(mockApi.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "DELETE")).toBe(true));
});

it("shows the empty hint", async () => {
  mockApi.mockImplementation(async (path: string) => (path.includes("/suggestions") ? suggestions : { entries: [], summary: { entries: 0, days: 0, by_work: [] } }));
  render(<WorkLogModule projectId={5} />);
  expect(await screen.findByText(/Записей пока нет/)).toBeTruthy();
});
