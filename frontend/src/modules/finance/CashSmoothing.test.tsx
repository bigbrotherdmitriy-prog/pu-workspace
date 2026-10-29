import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { CashSmoothing } from "./CashSmoothing";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows that there is no cash gap when ok", async () => {
  mockApi.mockResolvedValue({
    ok: true,
    before: { min_balance: "150000.00", min_date: "2026-03-15", negative_days: 0 },
    variants: [],
  });
  render(<CashSmoothing projectId={7} currency="RUB" />);
  expect(await screen.findByText(/Кассового разрыва нет/)).toBeTruthy();
});

it("renders three variants with resolved badge only for the resolved one", async () => {
  mockApi.mockResolvedValue({
    ok: false,
    before: { min_balance: "-20000.00", min_date: "2026-03-20", negative_days: 3 },
    variants: [
      { key: "delay_payments", title: "Отсрочить платежи", min_balance: "-5000.00", min_date: "2026-03-20", improvement: "15000.00", moves: [], moves_count: 2, max_shift_days: 10, resolved: false },
      { key: "pull_receipts", title: "Ускорить поступления", min_balance: "10000.00", min_date: "2026-03-20", improvement: "30000.00", moves: [], moves_count: 1, max_shift_days: 5, resolved: true },
      { key: "combined", title: "Комбинированный", min_balance: "25000.00", min_date: "2026-03-20", improvement: "45000.00", moves: [], moves_count: 3, max_shift_days: 12, resolved: true },
    ],
  });
  render(<CashSmoothing projectId={7} currency="RUB" />);
  expect(await screen.findByText("Отсрочить платежи")).toBeTruthy();
  expect(screen.getByText("Ускорить поступления")).toBeTruthy();
  expect(screen.getByText("Комбинированный")).toBeTruthy();
  expect(screen.getAllByText("Закрывает разрыв").length).toBe(2);
  expect(screen.getAllByText("Уменьшает разрыв").length).toBe(1);
});

it("expands moves and shows from → to", async () => {
  mockApi.mockResolvedValue({
    ok: false,
    before: { min_balance: "-20000.00", min_date: "2026-03-20", negative_days: 3 },
    variants: [
      { key: "delay_payments", title: "Отсрочить платежи", min_balance: "-5000.00", min_date: "2026-03-20", improvement: "15000.00", moves: [
        { entry_id: 11, title: "Оплата подрядчику", direction: "outflow", amount: "15000.00", from: "2026-03-18", to: "2026-03-25", days: 7 },
      ], moves_count: 1, max_shift_days: 7, resolved: false },
    ],
  });
  render(<CashSmoothing projectId={7} currency="RUB" />);
  const summary = await screen.findByText("Показать переносы");
  fireEvent.click(summary);
  expect(await screen.findByText("Оплата подрядчику")).toBeTruthy();
  expect(screen.getByText(/18\.03\.2026 → 25\.03\.2026/)).toBeTruthy();
});

it("shows an error in an alert", async () => {
  mockApi.mockRejectedValue(new Error("Сервер недоступен"));
  render(<CashSmoothing projectId={7} currency="RUB" />);
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Сервер недоступен");
});

it("shows the empty message when variants are empty", async () => {
  mockApi.mockResolvedValue({
    ok: false,
    before: { min_balance: "-20000.00", min_date: "2026-03-20", negative_days: 3 },
    variants: [],
  });
  render(<CashSmoothing projectId={7} currency="RUB" />);
  expect(await screen.findByText("Подходящих переносов не найдено: подвижных платежей и поступлений нет")).toBeTruthy();
});
