import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { DdsBoard } from "./DdsBoard";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); mockApi.mockReset(); });

const response = {
  currency: "RUB",
  income: { plan: "1000", fact: "100" },
  expense_total: { plan: "900", fact: "300", invoiced: "500" },
  by_month: [
    { month: "2020-01", income_plan: "100", income_fact: "100", expense_plan: "300", expense_fact: "300", cumulative_plan: "-200", cumulative_fact: "-200" },
    { month: "2099-01", income_plan: "900", income_fact: "0", expense_plan: "600", expense_fact: "0", cumulative_plan: "100", cumulative_fact: "0" },
  ],
  expense_by_article: [{
    budget_line_id: 1, title: "Кабель", category: "Материалы", plan: "500", invoiced: "500", paid: "300", remaining: "0",
    invoices: [
      { entry_id: 11, title: "Счёт 1", counterparty: "ООО Кабель", invoiced: "300", paid: "300", planned_date: "2026-01-10", status: "paid" },
      { entry_id: 12, title: "Счёт 2", counterparty: "ООО Провод", invoiced: "200", paid: "0", planned_date: "2026-02-10", status: "approved" },
    ],
  }],
  additional_costs: { plan: "0", fact: "0", invoiced: "50", items: [{ entry_id: 21, note: null, title: "Счёт № 7", counterparty: "ИП Кран", planned_date: "2026-03-01", actual_date: null, fact: "0", invoiced: "50" }] },
};

it("shows the plan balance, the critical point and only unpaid invoices", async () => {
  mockApi.mockResolvedValue(response);
  render(<DdsBoard projectId={5} currency="RUB" cashFlow={[]} />);
  expect(await screen.findByText("Критическая точка")).toBeTruthy();
  expect(screen.getByText(/кассовый разрыв/)).toBeTruthy();
  expect(screen.getByLabelText("График баланса по ДДС")).toBeTruthy();
  expect(screen.getByText("ООО Провод")).toBeTruthy();
  expect(screen.queryByText("ООО Кабель")).toBeNull();
  expect(screen.getByText("Кабель")).toBeTruthy();
});

it("pays an invoice and edits an additional cost description", async () => {
  mockApi.mockResolvedValue(response);
  render(<DdsBoard projectId={5} currency="RUB" cashFlow={[]} />);
  fireEvent.click(await screen.findByText("Оплачен"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/5/cash-flow/12/record-payment", expect.objectContaining({ method: "POST" })));
  const input = screen.getByLabelText("Описание Счёт № 7");
  fireEvent.change(input, { target: { value: "Аренда крана" } });
  fireEvent.blur(input);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/5/cash-flow/21/note", { method: "PATCH", body: JSON.stringify({ note: "Аренда крана" }) }));
});
