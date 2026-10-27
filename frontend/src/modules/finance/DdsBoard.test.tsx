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

const setupApi = (custom?: (path: string) => unknown) => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/receipts-by-basis")) return { contracts: [], totals: { invoiced: "0", received: "0", remaining: "0" } };
    if (custom) {
      const result = custom(path);
      if (result !== undefined) return result;
    }
    return response;
  });
};

it("shows the plan balance, the critical point and only unpaid invoices", async () => {
  setupApi();
  render(<DdsBoard projectId={5} currency="RUB" cashFlow={[]} />);
  expect(await screen.findByText("Критическая точка")).toBeTruthy();
  expect(screen.getByText(/кассовый разрыв/)).toBeTruthy();
  expect(screen.getByLabelText("График баланса по ДДС")).toBeTruthy();
  expect(screen.getByText("ООО Провод")).toBeTruthy();
  expect(screen.queryByText("ООО Кабель")).toBeNull();
  expect(screen.getByText("Кабель")).toBeTruthy();
});

it("pays an invoice and edits an additional cost description", async () => {
  setupApi();
  render(<DdsBoard projectId={5} currency="RUB" cashFlow={[]} />);
  fireEvent.click((await screen.findAllByText("Оплачен"))[0]);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/5/cash-flow/12/record-payment", expect.objectContaining({ method: "POST" })));
  const input = screen.getByLabelText("Описание Счёт № 7");
  fireEvent.change(input, { target: { value: "Аренда крана" } });
  fireEvent.blur(input);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/5/cash-flow/21/note", { method: "PATCH", body: JSON.stringify({ note: "Аренда крана" }) }));
});

it("names the contract stage behind a monthly receipt and records it with an editable date", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.includes("stage-register")) return { items: [
      { stage_number: 8, object_name: "ДУБНА", amount: "27646626.60", planned_finish: "2026-10-31" },
      { stage_number: 9, object_name: "ДУБНА", amount: "14383829.50", planned_finish: "2026-10-31" },
    ] };
    return response;
  });
  const row = { id: 167, direction: "inflow", title: "Этапы Дубна", planned_date: "2099-10-31", planned_amount: 70157242.28, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1 };
  const fact = { id: 268, direction: "inflow", title: "Этапы Дубна", planned_date: "2026-10-13", planned_amount: 0, actual_amount: 55773412.78, actual_date: "2026-10-13", plan_entry_id: 167, currency: "RUB", status: "received", record_version: 1 };
  render(<DdsBoard projectId={5} contractId={46} currency="RUB" cashFlow={[row, fact] as never} />);
  expect(await screen.findByText("9-й этап · Дубна")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Поступило от заказчика/ })).toBeTruthy();
  fireEvent.click(screen.getByText("Получено"));
  fireEvent.change(screen.getByLabelText("Дата поступления"), { target: { value: "2026-10-30" } });
  fireEvent.click(screen.getByText("Сохранить"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/5/cash-flow/167/record-fact",
    { method: "POST", body: JSON.stringify({ amount: "14383829.50", actual_date: "2026-10-30" }) }));
});

it("lists our act to the customer as an expected receipt and settles it against its plan line", async () => {
  setupApi();
  const plan = { id: 172, direction: "inflow", title: "Этапы Городец", planned_date: "2099-10-31", planned_amount: 100, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1 };
  const act = { id: 300, direction: "inflow", title: "Акт № 17", planned_date: "2099-10-20", planned_amount: 0, invoiced_amount: 40, actual_amount: 0, plan_entry_id: 172, counterparty: "ООО «БУЛАТ»", currency: "RUB", status: "approved", record_version: 1 };
  render(<DdsBoard projectId={5} currency="RUB" cashFlow={[plan, act] as never} />);
  expect(await screen.findByText("Акт № 17")).toBeTruthy();
  expect(screen.getByText("60,00 ₽")).toBeTruthy();
  fireEvent.click(screen.getAllByText("Получено")[0]);
  fireEvent.click(screen.getByText("Сохранить"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/5/cash-flow/172/record-fact",
    { method: "POST", body: expect.stringContaining('"basis_entry_id":300') }));
});

it("folds and unfolds a block from its title", async () => {
  setupApi();
  render(<DdsBoard projectId={5} currency="RUB" cashFlow={[]} />);
  const toggle = await screen.findByRole("button", { name: /К оплате/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(toggle.closest("section")?.className).toContain("folded");
});

it("pays an additional-cost invoice from its table and lists it under «К оплате»", async () => {
  setupApi();
  render(<DdsBoard projectId={5} currency="RUB" cashFlow={[]} />);
  fireEvent.click(await screen.findByRole("button", { name: /Дополнительные расходы/ }));
  expect(screen.getAllByText("ИП Кран").length).toBeGreaterThan(1);
  const buttons = screen.getAllByRole("button", { name: "Оплачен" });
  fireEvent.click(buttons[buttons.length - 1]);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/5/cash-flow/21/record-payment", expect.objectContaining({ method: "POST" })));
});
