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
  const extraRow = (await screen.findByText("Дополнительные затраты")).closest("tr")!;
  fireEvent.click(extraRow);

  const item = (await screen.findByText(/Доставка крана/)).closest("tr")!;
  const headerCells = item.closest("table")!.querySelectorAll("thead th").length;
  const cells = item.querySelectorAll("td");
  expect(cells.length).toBe(headerCells);
  expect(cells[0].textContent).toContain("ООО Кран");
  expect(cells[0].textContent).toContain("2026-10-10");
  expect(cells[3].textContent).toMatch(/70/);
  expect(cells[1].textContent).toBe("—");
});

it("shows the budget by DDS article with inline category and records an 80% payment", async () => {
  const budget = {
    currency: "RUB",
    income: pair("1000.00", "0.00"),
    direct_expense_total: { plan: "700.00", fact: "0.00", invoiced: "373.62" },
    expense_total: { plan: "700.00", fact: "0.00", invoiced: "373.62" },
    expense_by_category: [],
    expense_by_article: [
      { budget_line_id: 37, title: "Городец, кнопка, материалы", category_id: 2, category: "Материалы", plan: "300.00", invoiced: "0.00", paid: "0.00", remaining: "300.00", invoices: [] },
      { budget_line_id: 60, title: "АР", category_id: 3, category: "Прочие прямые", plan: "400.00", invoiced: "373.62", paid: "0.00", remaining: "26.38",
        invoices: [{ entry_id: 251, title: "Счёт № 92", counterparty: "ООО «Сириус»", invoiced: "373.62", paid: "0.00", planned_date: "2026-02-02", actual_date: null, status: "proposed", document_id: 5 }] },
    ],
    additional_costs: { plan: "0.00", fact: "0.00", items: [] },
    profit: pair("300.00", "0.00"),
    margin_pct: pair("30.00", null as unknown as string),
    by_month: [],
  };
  mockApi.mockImplementation(async (path: string) => {
    if (path.startsWith("/execution/cost-categories")) return { categories: [
      { id: 2, name: "Материалы", is_active: true }, { id: 3, name: "Прочие прямые", is_active: true }] };
    if (path.includes("record-payment")) return { id: 251, status: "paid" };
    return budget;
  });

  render(<BudgetFromDds projectId={29} contractId={46} currency="RUB" />);
  const first = await screen.findByText("Городец, кнопка, материалы");
  const rows = Array.from(first.closest("tbody")!.querySelectorAll("tr.budget-from-dds-article")).map((row) => row.querySelector("td")!.textContent);
  expect(rows).toEqual(["Городец, кнопка, материалы", "АР"]);
  expect((screen.getByLabelText("Категория статьи АР") as HTMLSelectElement).value).toBe("3");
  expect(screen.getByText(/Счёт: Счёт № 92 · ООО «Сириус»/)).toBeTruthy();

  fireEvent.click(screen.getByLabelText("Отметить оплату Счёт № 92"));
  fireEvent.change(screen.getByLabelText("Процент оплаты"), { target: { value: "80" } });
  fireEvent.click(screen.getByText("Сохранить оплату"));
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/29/cash-flow/251/record-payment",
    expect.objectContaining({ method: "POST", body: expect.stringContaining("\"percent\":80") })));
});
