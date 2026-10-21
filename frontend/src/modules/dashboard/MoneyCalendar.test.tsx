import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { MoneyCalendar } from "./MoneyCalendar";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("renders overdue and sign_act items and empty state", async () => {
  mockApi.mockResolvedValue({
    items: [
      {
        kind: "pay_invoice",
        title: "Счёт №1",
        counterparty: "ООО Ромашка",
        contract_id: null,
        contract_number: null,
        amount: "700.00",
        due_date: "2026-04-12",
        days_left: -3,
        level: "overdue",
      },
      {
        kind: "sign_act",
        title: "Акт: 1-й этап",
        counterparty: "ООО Подряд",
        contract_id: 1,
        contract_number: "Д-1",
        amount: "500.00",
        due_date: "2026-04-25",
        days_left: 10,
        level: "near",
      },
    ],
    counts: { overdue: 1, critical: 0, soon: 0, near: 1, ok: 0, none: 0 },
    totals: { to_pay: "700.00", to_receive: "0.00" },
  });

  render(<MoneyCalendar projectId={1} />);
  const overdue = await screen.findByText("⚠ просрочено на 3 дн.");
  const row = overdue.closest("li")!;
  expect(row.className).toContain("level-overdue");
  expect(screen.getByText("Подписать акт")).toBeTruthy();
});

it("renders empty state", async () => {
  mockApi.mockResolvedValue({
    items: [],
    counts: { overdue: 0, critical: 0, soon: 0, near: 0, ok: 0, none: 0 },
    totals: { to_pay: "0.00", to_receive: "0.00" },
  });
  render(<MoneyCalendar projectId={1} />);
  expect(await screen.findByText("В ближайшие 45 дней сроков нет")).toBeTruthy();
});
