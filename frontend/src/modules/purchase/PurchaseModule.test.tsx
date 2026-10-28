import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { PurchaseModule } from "./PurchaseModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); mockApi.mockReset(); });

const items = [
  {
    id: 1, name: "Кабель ВВГ 3x2.5", type_mark: "ВВГ", manufacturer: "Кабель-Завод",
    unit: "м", qty_needed: "150", qty_ordered: "0", qty_left: "150",
    status: "need", note: null, volumes: ["Том 1"],
  },
  {
    id: 2, name: "Автомат ВА47", type_mark: "ВА47", manufacturer: "IEK",
    unit: "шт", qty_needed: "4", qty_ordered: "4", qty_left: "0",
    status: "ordered", note: null, volumes: ["Том 1", "Том 2"],
  },
  {
    id: 3, name: "Щит ЩР", type_mark: "ЩР", manufacturer: null,
    unit: "шт", qty_needed: "1", qty_ordered: "1", qty_left: "0",
    status: "paid", note: null, volumes: [],
  },
  {
    id: 4, name: "Розетка", type_mark: null, manufacturer: null,
    unit: "шт", qty_needed: "10", qty_ordered: "10", qty_left: "0",
    status: "received", note: null, volumes: [],
  },
];

const summary = { total: 4, need: 1, ordered: 1, paid: 1, received: 1 };

const payload = { items, summary };

it("shows rows and counters", async () => {
  mockApi.mockResolvedValue(payload);
  render(<PurchaseModule projectId={7} />);
  expect(await screen.findByText("Кабель ВВГ 3x2.5")).toBeTruthy();
  expect(screen.getByText("Автомат ВА47")).toBeTruthy();
  expect(screen.getByText("ВВГ · Кабель-Завод")).toBeTruthy();
  expect(screen.getByText("Щит ЩР")).toBeTruthy();
  expect(screen.getByText("Розетка")).toBeTruthy();
});

it("filters list by counter click and toggles back", async () => {
  mockApi.mockResolvedValue(payload);
  render(<PurchaseModule projectId={7} />);
  await screen.findByText("Кабель ВВГ 3x2.5");
  const counters = screen.getAllByRole("button").filter((btn) => /Нужно заказать/.test(btn.textContent || ""));
  fireEvent.click(counters[0]);
  await waitFor(() => expect(screen.queryByText("Автомат ВА47")).toBeNull());
  expect(screen.getByText("Кабель ВВГ 3x2.5")).toBeTruthy();
  fireEvent.click(counters[0]);
  expect(await screen.findByText("Автомат ВА47")).toBeTruthy();
});

it("rebuild button posts and shows totals", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/purchase/rebuild")) return { items, created: 2, updated: 1, removed: 0 };
    return payload;
  });
  render(<PurchaseModule projectId={7} />);
  await screen.findByText("Кабель ВВГ 3x2.5");
  fireEvent.click(screen.getByText("Собрать из рабочей документации"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/7/purchase/rebuild",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(await screen.findByText("Строк: 4, новых: 2, обновлено: 1, убрано: 0")).toBeTruthy();
});

it("status change sends PATCH with ordered", async () => {
  mockApi.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.match(/\/purchase\/\d+$/) && init?.method === "PATCH") {
      return { ...items[0], status: "ordered", qty_ordered: "150", qty_left: "0" };
    }
    return payload;
  });
  render(<PurchaseModule projectId={7} />);
  await screen.findByText("Кабель ВВГ 3x2.5");
  const selects = screen.getAllByLabelText("Статус закупки") as HTMLSelectElement[];
  fireEvent.change(selects[0], { target: { value: "ordered" } });
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/7/purchase/1",
    expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"status":"ordered"') }),
  ));
});

it("shows PATCH error in role=alert", async () => {
  mockApi.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.match(/\/purchase\/\d+$/) && init?.method === "PATCH") {
      throw new Error("Нельзя сменить статус");
    }
    return payload;
  });
  render(<PurchaseModule projectId={7} />);
  await screen.findByText("Кабель ВВГ 3x2.5");
  const selects = screen.getAllByLabelText("Статус закупки") as HTMLSelectElement[];
  fireEvent.change(selects[0], { target: { value: "ordered" } });
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toMatch(/Нельзя сменить статус/);
});

it("shows hint on empty list", async () => {
  mockApi.mockResolvedValue({ items: [], summary: { total: 0, need: 0, ordered: 0, paid: 0, received: 0 } });
  render(<PurchaseModule projectId={7} />);
  expect(await screen.findByText(/Сначала подтвердите позиции в разделе «Рабочая документация»/)).toBeTruthy();
});
