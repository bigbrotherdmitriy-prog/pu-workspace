import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function receipt(init: unknown, id = 2) {
  const body = JSON.parse(String((init as { body?: string } | undefined)?.body));
  return { receipt_id: 5, client_mutation_id: body.client_mutation_id, operation: "work_log.create", status: "applied", entity_type: "work_log", entity_id: id, result: { id, project_id: body.project_id, author_user_id: 1 } };
}

function mockApiOk() {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (path.includes("/work-log/crews")) return { crews: [{ id: 4, name: "Бригада 1", foreman: "Иванов", members: "Петров\nСидоров", members_count: 3, note: null }] };
    if (path.includes("/work-log/schedule")) return schedule;
    if (path.includes("/suggestions")) return suggestions;
    if (path === "/mobile-sync/work-log-create") return receipt(init);
    if (init?.method === "POST") return { id: 2 };
    if (init?.method === "DELETE") return { deleted: 1 };
    return listing;
  });
}

it("lists entries with summary", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} userId={1} />);
  expect(await screen.findByText("Прокладка кабеля")).toBeTruthy();
  expect(screen.getByText(/Записей: 1/)).toBeTruthy();
});

it("shows works from the schedule and enters a fact for the chosen work", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} userId={1} />);
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
  render(<WorkLogModule projectId={5} userId={1} />);
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
  render(<WorkLogModule projectId={5} userId={1} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.click(screen.getByText("Добавить запись"));
  expect((await screen.findByRole("alert")).textContent).toContain("Укажите, какая работа");
});

it("deletes an entry and shows the empty state", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} userId={1} />);
  fireEvent.click(await screen.findByLabelText("Удалить запись Прокладка кабеля"));
  await waitFor(() => expect(mockApi.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "DELETE")).toBe(true));
});

it("shows the empty hint", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.includes("/work-log/crews")) return { crews: [] };
    if (path.includes("/work-log/schedule")) return { baseline: null, items: [] };
    return path.includes("/suggestions") ? suggestions : { entries: [], summary: { entries: 0, days: 0, by_work: [] } };
  });
  render(<WorkLogModule projectId={5} userId={1} />);
  expect(await screen.findByText(/Записей пока нет/)).toBeTruthy();
});


it("sends hours and crew with the entry and manages crews on the crews tab", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} userId={1} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/), { target: { value: "Монтаж щита" } });
  fireEvent.change(screen.getByLabelText("Часов работы"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("Бригада"), { target: { value: "4" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]) === "/mobile-sync/work-log-create" && (c[1] as { method?: string } | undefined)?.method === "POST");
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


it("fills people from the crew and sends the timesheet with rates as data", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} userId={1} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/), { target: { value: "Монтаж щита" } });
  fireEvent.change(screen.getByLabelText("Бригада"), { target: { value: "4" } });
  expect(await screen.findByLabelText("Работник 1")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Часов у работника 1"), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText("Ставка работника 1"), { target: { value: "450" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]) === "/mobile-sync/work-log-create" && (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    const body = String((call![1] as { body: string }).body);
    expect(body).toContain("\"labor\":[");
    expect(body).toContain("\"hours\":\"6\"");
    expect(body).toContain("\"hourly_rate\":\"450\"");
  });
});

it("adds a worker by hand", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} userId={1} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.click(screen.getByText("Добавить работника"));
  expect(screen.getByLabelText("Работник 1")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Убрать работника 1"));
  expect(screen.queryByLabelText("Работник 1")).toBeNull();
});


it("links the entry to an estimate position and fills name and unit from it", async () => {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (path.includes("/work-log/crews")) return { crews: [] };
    if (path.includes("/work-log/schedule")) return { baseline: null, items: [] };
    if (path.includes("/suggestions")) return { ...suggestions, estimate_lines: [{ id: 77, position: "3", code: null, name: "Прокладка кабеля ВВГ", unit: "м", qty: "100" }] };
    if (path === "/mobile-sync/work-log-create") return receipt(init, 9);
    if (init?.method === "POST") return { id: 9 };
    return listing;
  });
  render(<WorkLogModule projectId={5} userId={1} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.change(await screen.findByLabelText(/Позиция сметы/), { target: { value: "77" } });
  fireEvent.change(screen.getByLabelText("Объём"), { target: { value: "25" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]) === "/mobile-sync/work-log-create" && (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    const body = String((call![1] as { body: string }).body);
    expect(body).toContain("\"estimate_line_id\":77");
    expect(body).toContain("Прокладка кабеля ВВГ");
  });
});


it.each(["crews", "schedule", "suggestions"])("ignores a late %s response from the previous project", async (kind) => {
  let finishOld!: (value: unknown) => void;
  const old = new Promise<unknown>((resolve) => { finishOld = resolve; });
  const resource = `/work-log/${kind}`;
  function payload(label: string) {
    if (kind === "crews") return { crews: [{ id: 41, name: label, foreman: null, members: "", members_count: 0, note: null }] };
    if (kind === "schedule") return { baseline: { id: 1, name: label, version: 1, status: "approved" }, items: [] };
    return { works: [{ work_name: label, unit: "m" }], objects: [], rd_items: [] };
  }
  mockApi.mockImplementation(async (path: string) => {
    if (path === `/projects/5${resource}`) return old;
    if (path === `/projects/6${resource}`) return payload("Current synthetic project");
    if (path.endsWith("/crews")) return { crews: [] };
    if (path.endsWith("/schedule")) return { baseline: null, items: [] };
    if (path.endsWith("/suggestions")) return { works: [], objects: [], rd_items: [] };
    return { entries: [], summary: { entries: 0, days: 0, by_work: [] } };
  });
  const { rerender, container } = render(<WorkLogModule projectId={5} userId={1} />);
  rerender(<WorkLogModule projectId={6} userId={1} />);
  if (kind === "crews") fireEvent.click(screen.getByText("Бригады"));
  if (kind === "schedule") fireEvent.click(screen.getByText("Подсказка из графика (ГПР)"));
  const currentVisible = () => kind === "suggestions"
    ? container.querySelector('option[value="Current synthetic project"]') !== null
    : screen.queryAllByText(/Current synthetic project/).length > 0;
  await waitFor(() => expect(currentVisible()).toBe(true));
  await act(async () => { finishOld(payload("Old synthetic project")); await old; });
  expect(currentVisible()).toBe(true);
  expect(screen.queryAllByText(/Old synthetic project/)).toHaveLength(0);
  expect(container.querySelector('option[value="Old synthetic project"]')).toBeNull();
});


it("hides loaded project context while the next project is still loading without clearing the draft", async () => {
  const never = new Promise<never>(() => undefined);
  mockApi.mockImplementation(async (path: string) => {
    if (path.startsWith("/projects/6/")) return never;
    if (path.endsWith("/crews")) return { crews: [{ id: 41, name: "Old synthetic crew", foreman: null, members: "", members_count: 0, note: null }] };
    if (path.endsWith("/schedule")) return { baseline: { id: 1, name: "Old synthetic schedule", version: 1, status: "approved" }, items: [] };
    if (path.endsWith("/suggestions")) return { works: [{ work_name: "Old synthetic suggestion", unit: "m" }], objects: [], rd_items: [] };
    return { entries: [], summary: { entries: 0, days: 0, by_work: [] } };
  });
  const { rerender, container } = render(<WorkLogModule projectId={5} userId={1} />);
  await waitFor(() => expect(container.querySelector('option[value="Old synthetic suggestion"]')).not.toBeNull());
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/), { target: { value: "Unsaved synthetic draft" } });
  rerender(<WorkLogModule projectId={6} userId={1} />);
  expect(container.querySelector('option[value="Old synthetic suggestion"]')).toBeNull();
  expect(container.querySelector('option[value="41"]')).toBeNull();
  expect((screen.getByLabelText(/Какая работа выполнена/) as HTMLInputElement).value).toBe("Unsaved synthetic draft");
  fireEvent.click(screen.getByText("Подсказка из графика (ГПР)"));
  expect(screen.queryByText(/Old synthetic schedule/)).toBeNull();
  fireEvent.click(screen.getByText("Бригады"));
  expect(screen.queryByText(/Old synthetic crew/)).toBeNull();
});
