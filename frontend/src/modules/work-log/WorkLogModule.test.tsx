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

const schedule = { baseline: { id: 1, name: "ГПР", version: 1, status: "approved" }, items: [
  { id: 9, title: "Монтаж кабельных лотков", is_group: false, is_milestone: false, planned_start: "2026-10-01", planned_finish: "2026-10-10", planned_progress: 100, actual_progress: 40, overdue: true, fact_entries: 0, fact_qty: null, fact_unit: null, fact_first: null, fact_last: null },
] };

function mockApiOk() {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (path.includes("/work-log/crews")) return { crews: [{ id: 4, name: "Бригада 1", foreman: "Иванов", members: "Петров\nСидоров", members_count: 3, note: null }] };
    if (path.includes("/work-log/schedule")) return schedule;
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

it("shows works from the schedule and enters a fact for the chosen work", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  fireEvent.click(await screen.findByText("Подсказка из графика (ГПР)"));
  fireEvent.click(await screen.findByText("Монтаж кабельных лотков"));
  expect(screen.getByText(/Вносим факт по работе/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Объём"), { target: { value: "30" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    const body = String((call![1] as { body: string }).body);
    expect(body).toContain("Монтаж кабельных лотков");
    expect(body).toContain("\"schedule_item_id\":9");
  });
});

it("adds an entry for a work outside the schedule", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  fireEvent.click(await screen.findByText("Подсказка из графика (ГПР)"));
  fireEvent.click(await screen.findByText("Монтаж кабельных лотков"));
  fireEvent.click(screen.getByText("Другая работа (вне графика)"));
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
  mockApi.mockImplementation(async (path: string) => {
    if (path.includes("/work-log/crews")) return { crews: [] };
    if (path.includes("/work-log/schedule")) return { baseline: null, items: [] };
    return path.includes("/suggestions") ? suggestions : { entries: [], summary: { entries: 0, days: 0, by_work: [] } };
  });
  render(<WorkLogModule projectId={5} />);
  expect(await screen.findByText(/Записей пока нет/)).toBeTruthy();
});


it("sends hours and crew with the entry and manages crews on the crews tab", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/), { target: { value: "Монтаж щита" } });
  fireEvent.change(screen.getByLabelText("Часов работы"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("Бригада"), { target: { value: "4" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]).endsWith("/work-log") && (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    const body = String((call![1] as { body: string }).body);
    expect(body).toContain("\"hours\":\"8\"");
    expect(body).toContain("\"crew_id\":4");
  });
  fireEvent.click(screen.getByText("Бригады"));
  expect(await screen.findByText(/человек: 3/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Название бригады"), { target: { value: "Бригада 2" } });
  fireEvent.click(screen.getByText("Добавить бригаду"));
  await waitFor(() => expect(mockApi.mock.calls.some((c) => String(c[0]).endsWith("/work-log/crews") && (c[1] as { method?: string } | undefined)?.method === "POST")).toBe(true));
});
