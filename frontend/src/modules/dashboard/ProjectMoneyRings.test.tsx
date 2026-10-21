import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ProjectMoneyRings } from "./ProjectMoneyRings";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("renders money pie charts with the schedule lag and the critical point", async () => {
  mockApi.mockResolvedValue({
    income: { plan: "10000000", fact: "2000000" },
    expense_total: { plan: "8000000", fact: "3000000", invoiced: "4000000" },
    profit: { plan: "2000000", fact: "-1000000" },
    margin_pct: { plan: "20.00", fact: null },
    by_month: [
      { month: "2020-01", income_plan: "5000000", income_fact: "2000000", expense_plan: "3000000", expense_fact: "3000000", cumulative_plan: "2000000", cumulative_fact: "-1000000" },
      { month: "2099-01", income_plan: "5000000", income_fact: "0", expense_plan: "5000000", expense_fact: "0", cumulative_plan: "-500000", cumulative_fact: "0" },
    ],
    expense_by_category: [
      { category: "СМР", plan: "6000000", fact: "2000000" },
      { category: "Материалы", plan: "2000000", fact: "1000000" },
    ],
  });

  render(<ProjectMoneyRings projectId={7} />);

  expect(await screen.findByText("Просрочено по графику")).toBeTruthy();
  expect(screen.getByText("отстаём на 3,0 млн ₽")).toBeTruthy();
  expect(screen.getByText("Структура расходов")).toBeTruthy();
  expect(screen.getByText("крупнейшая статья: СМР 75%")).toBeTruthy();
  expect(screen.getByText(/Критическая точка: январь 2099/)).toBeTruthy();
  expect(screen.getByText("Деньги сейчас")).toBeTruthy();
});
