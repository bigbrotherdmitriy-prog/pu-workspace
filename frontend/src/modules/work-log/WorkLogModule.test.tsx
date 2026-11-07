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


it("fills people from the crew and sends the timesheet with rates as data", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.change(screen.getByLabelText(/Какая работа выполнена/), { target: { value: "Монтаж щита" } });
  fireEvent.change(screen.getByLabelText("Бригада"), { target: { value: "4" } });
  expect(await screen.findByLabelText("Работник 1")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Часов у работника 1"), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText("Ставка работника 1"), { target: { value: "450" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]).endsWith("/work-log") && (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    const body = String((call![1] as { body: string }).body);
    expect(body).toContain("\"labor\":[");
    expect(body).toContain("\"hours\":\"6\"");
    expect(body).toContain("\"hourly_rate\":\"450\"");
  });
});

it("adds a worker by hand", async () => {
  mockApiOk();
  render(<WorkLogModule projectId={5} />);
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
    if (init?.method === "POST") return { id: 9 };
    return listing;
  });
  render(<WorkLogModule projectId={5} />);
  await screen.findByText("Прокладка кабеля");
  fireEvent.change(await screen.findByLabelText(/Позиция сметы/), { target: { value: "77" } });
  fireEvent.change(screen.getByLabelText("Объём"), { target: { value: "25" } });
  fireEvent.click(screen.getByText("Добавить запись"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]).endsWith("/work-log") && (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    const body = String((call![1] as { body: string }).body);
    expect(body).toContain("\"estimate_line_id\":77");
    expect(body).toContain("Прокладка кабеля ВВГ");
  });
});


// --- STAGE3: GET lifetime regression for crews / schedule / suggestions ---

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void };
function defer<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

const crewOf = (id: number, name: string) => ({ id, name, foreman: null, members: "", members_count: 1, note: null });
const planItemOf = (id: number, title: string) => ({ id, title, is_group: false, is_milestone: false, planned_start: "2026-10-01", planned_finish: "2026-10-10", planned_progress: 100, actual_progress: 0, overdue: false, fact_entries: 0, fact_qty: null, fact_unit: null, fact_first: null, fact_last: null });
const schedOf = (name: string, item: ReturnType<typeof planItemOf>) => ({ baseline: { id: 1, name, version: 1, status: "approved" }, items: [item] });
const suggOf = (object: string) => ({ works: [], objects: [object], rd_items: [] });

const endpointOf = (path: string): "crews" | "schedule" | "suggestions" | "list" => {
  if (path.includes("/work-log/crews")) return "crews";
  if (path.includes("/work-log/schedule")) return "schedule";
  if (path.includes("/suggestions")) return "suggestions";
  return "list";
};

const projectOf = (path: string): number => Number(/\/projects\/(\d+)\//.exec(path)?.[1] ?? 0);

const scenarios = [
  { name: "crews", stale: { crews: [crewOf(41, "СТАРАЯ-БРИГАДА-5")] }, fresh: { crews: [crewOf(61, "НОВАЯ-БРИГАДА-6")] }, staleMarker: "СТАРАЯ-БРИГАДА-5", freshMarker: "НОВАЯ-БРИГАДА-6" },
  { name: "schedule", stale: schedOf("ГПР-СТАРЫЙ-5", planItemOf(91, "СТАРАЯ-РАБОТА-5")), fresh: schedOf("ГПР-НОВЫЙ-6", planItemOf(92, "НОВАЯ-РАБОТА-6")), staleMarker: "СТАРАЯ-РАБОТА-5", freshMarker: "НОВАЯ-РАБОТА-6" },
  { name: "suggestions", stale: suggOf("СТАРЫЙ-ОБЪЕКТ-5"), fresh: suggOf("НОВЫЙ-ОБЪЕКТ-6"), staleMarker: "СТАРЫЙ-ОБЪЕКТ-5", freshMarker: "НОВЫЙ-ОБЪЕКТ-6" },
] as const;

function expose(name: string) {
  if (name === "crews") fireEvent.click(screen.getByText("Бригады"));
  else if (name === "schedule") fireEvent.click(screen.getByText("Подсказка из графика (ГПР)"));
}

for (const sc of scenarios) {
  it(`ignores stale ${sc.name} from project 5 after switching to project 6`, async () => {
    const stale = defer<unknown>();
    mockApi.mockImplementation(async (path: string) => {
      const ep = endpointOf(path);
      const pid = projectOf(path);
      if (ep === sc.name) {
        if (pid === 5) return stale.promise;
        if (pid === 6) return sc.fresh;
      }
      if (ep === "crews") return { crews: [] };
      if (ep === "schedule") return { baseline: null, items: [] };
      if (ep === "suggestions") return { works: [], objects: [], rd_items: [] };
      return { entries: [], summary: { entries: 0, days: 0, by_work: [] } };
    });
    const { rerender } = render(<WorkLogModule projectId={5} />);
    await waitFor(() => expect(mockApi.mock.calls.some((c) => endpointOf(String(c[0])) === sc.name && projectOf(String(c[0])) === 5)).toBe(true));
    rerender(<WorkLogModule projectId={6} />);
    await waitFor(() => expect(mockApi.mock.calls.some((c) => endpointOf(String(c[0])) === sc.name && projectOf(String(c[0])) === 6)).toBe(true));
    expose(sc.name);
    await waitFor(() => expect(screen.queryByText(sc.freshMarker)).toBeTruthy());
    await waitFor(() => expect(screen.queryByText(sc.staleMarker)).toBeNull());
    await waitFor(async () => { stale.resolve(sc.stale); });
    await waitFor(() => expect(screen.queryByText(sc.freshMarker)).toBeTruthy());
    expect(screen.queryByText(sc.staleMarker)).toBeNull();
  });
}

it("clears crews/schedule/suggestions and skips GETs when switching to project 0", async () => {
  mockApi.mockImplementation(async (path: string) => {
    const ep = endpointOf(path);
    const pid = projectOf(path);
    if (pid === 5) {
      if (ep === "crews") return { crews: [crewOf(41, "СТАРАЯ-БРИГАДА-5")] };
      if (ep === "schedule") return schedOf("ГПР-СТАРЫЙ-5", planItemOf(91, "СТАРАЯ-РАБОТА-5"));
      if (ep === "suggestions") return suggOf("СТАРЫЙ-ОБЪЕКТ-5");
    }
    if (ep === "crews") return { crews: [] };
    if (ep === "schedule") return { baseline: null, items: [] };
    if (ep === "suggestions") return { works: [], objects: [], rd_items: [] };
    return { entries: [], summary: { entries: 0, days: 0, by_work: [] } };
  });
  const { rerender } = render(<WorkLogModule projectId={5} />);
  await waitFor(() => expect(mockApi.mock.calls.some((c) => endpointOf(String(c[0])) === "crews" && projectOf(String(c[0])) === 5)).toBe(true));
  await waitFor(() => expect(mockApi.mock.calls.some((c) => endpointOf(String(c[0])) === "schedule" && projectOf(String(c[0])) === 5)).toBe(true));
  await waitFor(() => expect(mockApi.mock.calls.some((c) => endpointOf(String(c[0])) === "suggestions" && projectOf(String(c[0])) === 5)).toBe(true));
  rerender(<WorkLogModule projectId={0} />);
  await waitFor(() => expect(screen.queryByText("СТАРАЯ-БРИГАДА-5")).toBeNull());
  fireEvent.click(screen.getByText("Подсказка из графика (ГПР)"));
  await waitFor(() => expect(screen.queryByText("СТАРАЯ-РАБОТА-5")).toBeNull());
  expect(screen.queryByText("СТАРЫЙ-ОБЪЕКТ-5")).toBeNull();
  expect(mockApi.mock.calls.some((c) => projectOf(String(c[0])) === 0)).toBe(false);
});

it("ignores old project 5 deferred data after A->B->A", async () => {
  const stale = defer<unknown>();
  mockApi.mockImplementation(async (path: string) => {
    const ep = endpointOf(path);
    const pid = projectOf(path);
    if (ep === "crews") {
      if (pid === 5) return stale.promise;
      if (pid === 6) return { crews: [crewOf(61, "НОВАЯ-БРИГАДА-6")] };
      return { crews: [] };
    }
    if (ep === "schedule") return { baseline: null, items: [] };
    if (ep === "suggestions") return { works: [], objects: [], rd_items: [] };
    return { entries: [], summary: { entries: 0, days: 0, by_work: [] } };
  });
  const { rerender } = render(<WorkLogModule projectId={5} />);
  await waitFor(() => expect(mockApi.mock.calls.some((c) => endpointOf(String(c[0])) === "crews" && projectOf(String(c[0])) === 5)).toBe(true));
  rerender(<WorkLogModule projectId={6} />);
  await waitFor(() => expect(mockApi.mock.calls.some((c) => endpointOf(String(c[0])) === "crews" && projectOf(String(c[0])) === 6)).toBe(true));
  rerender(<WorkLogModule projectId={5} />);
  await waitFor(() => expect(mockApi.mock.calls.filter((c) => endpointOf(String(c[0])) === "crews" && projectOf(String(c[0])) === 5).length).toBeGreaterThanOrEqual(2));
  await waitFor(async () => { stale.resolve({ crews: [crewOf(41, "СТАРАЯ-БРИГАДА-5")] }); });
  fireEvent.click(screen.getByText("Бригады"));
  await waitFor(() => expect(screen.queryByText("СТАРАЯ-БРИГАДА-5")).toBeNull());
});
