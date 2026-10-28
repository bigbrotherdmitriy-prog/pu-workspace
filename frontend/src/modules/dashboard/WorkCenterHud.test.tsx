import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { mln, WorkCenterHud } from "./WorkCenterHud";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); mockApi.mockReset(); });

const budget = {
  income: { plan: "1000000", fact: "800000" },
  expense_total: { plan: "500000", fact: "100000", invoiced: "100000" },
  expense_by_category: [{ category: "Оборудование", plan: "400000", fact: "0" }, { category: "СМР", plan: "100000", fact: "100000" }],
  by_month: [
    { month: "2020-01", income_plan: "500000", cumulative_plan: "-100000", cumulative_fact: "-100000" },
    { month: "2099-01", income_plan: "500000", cumulative_plan: "300000", cumulative_fact: "0" },
  ],
};
const calendar = {
  items: [
    { kind: "sign_act", title: "Акт: 7-й этап · ДУБНА", counterparty: null, contract_id: 3, amount: "28126786.18", due_date: "2026-10-01", days_left: -22, level: "overdue" },
    { kind: "pay_plan", title: "SCADA Городец", counterparty: null, contract_id: null, amount: "1637392.50", due_date: "2026-11-01", days_left: 9, level: "near" },
  ],
  counts: { overdue: 1 }, unmarked: { count: 70, amount: "394756284.62" },
};
const hud = {
  objects: [{ name: "ГОРОДЕЦ", price: "300", acted: "200", stages: [
    { number: 1, state: "ok", amount: "100", finish: "2026-01-31" }, { number: 2, state: "draft", amount: "100", finish: "2026-02-28" },
    { number: 3, state: "late", amount: "100", finish: "2026-03-31" }, { number: 4, state: "ahead", amount: "100", finish: "2099-01-01" }] }],
  id_readiness: [{ object: "ДУБНА", percent: 38, required_total: 8, missing: 5 }],
  invoices_to_check: 4,
};

function route() {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/budget-from-dds")) return budget;
    if (path.endsWith("/money-calendar")) return calendar;
    if (path.endsWith("/work-center/hud")) return hud;
    throw new Error(`unexpected ${path}`);
  });
}

it("formats millions with a decimal comma", () => {
  expect(mln(573_952_430.22)).toBe("574,0");
  expect(mln(1_102_386.33, 2)).toBe("1,10");
});

it("shows the live numbers, the critical point and the alarm counters", async () => {
  route();
  render(<WorkCenterHud projectId={7} onOpenFinance={vi.fn()} />);
  expect(await screen.findByLabelText("Схема этапов")).toBeTruthy();
  expect(screen.getByText("Аварий: 1")).toBeTruthy();
  expect(screen.getByText("Счетов на проверку: 4")).toBeTruthy();
  expect(screen.getByText("Кассового разрыва по плану нет")).toBeTruthy();
  expect(screen.getByLabelText("Деньги проекта").textContent).toContain("0,8");
  expect(screen.getByLabelText("Приход и структура расходов").textContent).toContain("Оборудование");
  expect(screen.getByLabelText("Исполнительная документация").textContent).toContain("38%");
  expect(screen.getByText(/Фон:/).textContent).toContain("CC BY 4.0");
});

it("draws every stage as a breaker with its state and opens the event on click", async () => {
  route();
  const onOpenItem = vi.fn();
  render(<WorkCenterHud projectId={7} onOpenFinance={vi.fn()} onOpenItem={onOpenItem} />);
  const diagram = await screen.findByRole("img", { name: /Этапы по объектам/ });
  expect(diagram.querySelectorAll("rect").length).toBeGreaterThanOrEqual(4);
  expect(diagram.textContent).toContain("ГОРОДЕЦ");
  fireEvent.click(screen.getByRole("button", { name: /подписать акт: Акт: 7-й этап/ }));
  await waitFor(() => expect(onOpenItem).toHaveBeenCalledWith(expect.objectContaining({ contract_id: 3 })));
});

it("reports a cash gap when the plan balance goes below zero after today", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/budget-from-dds")) return { ...budget, by_month: [{ month: "2099-01", income_plan: "0", cumulative_plan: "-5000000", cumulative_fact: "0" }, { month: "2099-02", income_plan: "0", cumulative_plan: "-1000000", cumulative_fact: "0" }] };
    if (path.endsWith("/money-calendar")) return { items: [], counts: {} };
    return { objects: [], id_readiness: [], invoices_to_check: 0 };
  });
  render(<WorkCenterHud projectId={7} onOpenFinance={vi.fn()} />);
  expect(await screen.findByText("Кассовый разрыв")).toBeTruthy();
  expect(screen.getByText("Ожидается кассовый разрыв по плану")).toBeTruthy();
});

it("shows empty states for stages, events and executive docs when nothing is loaded", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/budget-from-dds")) return budget;
    if (path.endsWith("/money-calendar")) return { items: [], counts: {} };
    return { objects: [], id_readiness: [], invoices_to_check: 0 };
  });
  const onOpenDocuments = vi.fn();
  render(<WorkCenterHud projectId={7} onOpenFinance={vi.fn()} onOpenDocuments={onOpenDocuments} />);
  expect(await screen.findByText("Этапы по объектам ещё не заведены")).toBeTruthy();
  expect(screen.getByText("Событий, требующих внимания, нет — всё в порядке")).toBeTruthy();
  expect(screen.getByText("Данных по исполнительной документации пока нет")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Открыть договоры/ }));
  expect(onOpenDocuments).toHaveBeenCalled();
});

it("hides the documents action when onOpenDocuments is not provided", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/budget-from-dds")) return budget;
    if (path.endsWith("/money-calendar")) return { items: [], counts: {} };
    return { objects: [], id_readiness: [], invoices_to_check: 0 };
  });
  render(<WorkCenterHud projectId={7} onOpenFinance={vi.fn()} />);
  expect(await screen.findByText("Этапы по объектам ещё не заведены")).toBeTruthy();
  expect(screen.queryByText("Открыть договоры")).toBeNull();
});
