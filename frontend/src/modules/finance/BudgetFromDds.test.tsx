import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { BudgetFromDds } from "./BudgetFromDds";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const pair = (plan: string, fact: string) => ({ plan, fact });

it("lines up additional cost items with the header columns and puts the amount under «Факт»", async () => {
  mockApi.mockResolvedValue({
    currency: "RUB",
    income: pair("1000.00", "800.00"),
    direct_expense_total: pair("600.00", "500.00"),
    expense_total: pair("600.00", "570.00"),
    expense_by_category: [
      { category_id: 1, category: "СМР", plan: "600.00", fact: "500.00", deviation: "-100.00", remaining: "100.00", counterparties: [] },
    ],
    additional_costs: {
      plan: "0.00", fact: "70.00",
      items: [{ title: "Доставка крана", counterparty: "ООО Кран", planned_date: null, actual_date: "2026-10-10", fact: "70.00" }],
    },
    profit: pair("400.00", "230.00"),
    margin_pct: pair("40.00", "28.75"),
    by_month: [],
  });

  render(<BudgetFromDds projectId={7} contractId={null} currency="RUB" />);
  const extraRow = (await screen.findByText("Дополнительные затраты (вне плана)")).closest("tr")!;
  fireEvent.click(extraRow);

  const item = (await screen.findByText(/Доставка крана/)).closest("tr")!;
  const headerCells = item.closest("table")!.querySelectorAll("thead th").length;
  const cells = item.querySelectorAll("td");
  expect(cells.length).toBe(headerCells);
  expect(cells[0].textContent).toContain("ООО Кран");
  expect(cells[0].textContent).toContain("2026-10-10");
  expect(cells[2].textContent).toMatch(/70/);
  expect(cells[1].textContent).toBe("—");
});
