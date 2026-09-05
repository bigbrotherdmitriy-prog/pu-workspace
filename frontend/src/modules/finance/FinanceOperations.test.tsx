import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FinanceOperations } from "./FinanceOperations";
import type { FinanceOverview } from "./types";

const finance: FinanceOverview = {
  summary: {
    budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0,
    budget_variance: 0, cash_balance_forecast: 0, cash_gap: 0, delayed_schedule: 0,
    late_procurement: 0, acts_pending: 0, pending_payments: 0, unlinked_invoices: 0,
  },
  baselines: [
    { id: 2, name: "ГПР v2", version: 2, status: "draft", is_current: false },
    { id: 1, name: "ГПР v1", version: 1, status: "approved", is_current: true },
  ],
  schedule: [
    {
      id: 10, baseline_id: 1, title: "Монтаж", planned_start: "2026-09-01",
      planned_finish: "2026-09-30", planned_progress: 100, actual_start: "2026-09-03",
      actual_progress: 25, status: "in_progress",
    },
  ],
  budget: [], cash_flow: [], procurement: [], acts: [],
};

afterEach(cleanup);

function renderOperations(overrides: Partial<Parameters<typeof FinanceOperations>[0]> = {}) {
  const props: Parameters<typeof FinanceOperations>[0] = {
    finance, preview: null, selectedRows: [], setSelectedRows: vi.fn(), selectedContractId: 0,
    kind: "schedule", title: "", amount: "", date: "", extra: "", sourceDocumentId: 0,
    scheduleItemId: 0, budgetLineId: 0, setKind: vi.fn(), setTitle: vi.fn(), setAmount: vi.fn(),
    setDate: vi.fn(), setExtra: vi.fn(), setScheduleItemId: vi.fn(), setBudgetLineId: vi.fn(),
    onClosePreview: vi.fn(), onImport: vi.fn(), onAdd: vi.fn(), onConfirm: vi.fn(),
    onCloneBaseline: vi.fn(), onUpdateSchedule: vi.fn(), onConfirmPayment: vi.fn(),
    onCorrectPayment: vi.fn(), includeEditor: false,
    ...overrides,
  };
  return { ...render(<FinanceOperations {...props} />), props };
}

describe("GPR baseline plan/fact register", () => {
  it("visibly separates current approved plan, draft revision and facts", () => {
    renderOperations();

    expect(screen.getByText("текущий утверждённый план", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("черновик новой редакции", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("План: 2026-09-01 → 2026-09-30 · 100%")) .toBeInTheDocument();
    expect(screen.getByText("Факт: 2026-09-03 → — · 25%")) .toBeInTheDocument();
    expect(screen.getByText(/План утверждённой версии неизменяем/)).toBeInTheDocument();
  });

  it("requires explicit actions for approval and actual progress", () => {
    const onConfirm = vi.fn();
    const onUpdateSchedule = vi.fn();
    renderOperations({ onConfirm, onUpdateSchedule });

    fireEvent.click(screen.getByRole("button", { name: "Проверить и утвердить" }));
    fireEvent.click(screen.getByRole("button", { name: "Обновить факт" }));

    expect(onConfirm).toHaveBeenCalledWith("baselines", 2, "approved");
    expect(onUpdateSchedule).toHaveBeenCalledWith(10, 25);
  });

  it("offers cloning only when no draft exists", () => {
    const onCloneBaseline = vi.fn();
    const onlyApproved = { ...finance, baselines: [finance.baselines[1]] };
    renderOperations({ finance: onlyApproved, onCloneBaseline });

    fireEvent.click(screen.getByRole("button", { name: "Новая версия" }));
    expect(onCloneBaseline).toHaveBeenCalledWith(1, 1);
  });
});

const summary = {
  budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0,
  budget_variance: 0, cash_balance_forecast: 0, cash_gap: 0,
  delayed_schedule: 0, late_procurement: 0, acts_pending: 0,
  pending_payments: 1, unlinked_invoices: 0,
};

function cashFinance(status: string): FinanceOverview {
  return {
    summary,
    baselines: [], schedule: [], budget: [], procurement: [], acts: [],
    cash_flow: [{
      id: 17,
      record_version: 4,
      contract_id: 2,
      schedule_item_id: 3,
      budget_line_id: 5,
      source_document_id: 7,
      source_document_version_id: 8,
      direction: "inflow",
      title: "Synthetic customer receipt",
      planned_date: "2026-09-10",
      actual_date: status === "received" ? "2026-09-11" : undefined,
      planned_amount: 75000,
      actual_amount: status === "received" ? 74250 : 0,
      status,
      review_status: "confirmed",
    }],
  };
}

function props(status: string) {
  return {
    finance: cashFinance(status), preview: null, selectedRows: [], setSelectedRows: vi.fn(),
    selectedContractId: 2, kind: "cash-in", title: "", amount: "", date: "", extra: "",
    sourceDocumentId: 0, scheduleItemId: 0, budgetLineId: 0,
    setKind: vi.fn(), setTitle: vi.fn(), setAmount: vi.fn(), setDate: vi.fn(), setExtra: vi.fn(),
    setScheduleItemId: vi.fn(), setBudgetLineId: vi.fn(), onClosePreview: vi.fn(), onImport: vi.fn(),
    onAdd: vi.fn(), onConfirm: vi.fn(), onConfirmPayment: vi.fn(), onCorrectPayment: vi.fn(),
    onCloneBaseline: vi.fn(), onUpdateSchedule: vi.fn(),
    includeEditor: false,
  };
}

afterEach(cleanup);

describe("FinanceOperations human confirmation boundary", () => {
  it("shows unresolved owner/legal decisions and does not claim an automatic financial effect", () => {
    const input = props("approved");
    input.finance = {
      ...input.finance,
      decision_requirements: [
        { code: "unknown_currency", decision_by: "OWNER", message: "Нужно выбрать поддерживаемую валюту." },
        { code: "vat_treatment", decision_by: "LEGAL", message: "Нужно подтвердить правило НДС." },
      ],
      external_effects: { payment_created: false, posting_created: false, automatic_conversion: false },
    };
    render(<FinanceOperations {...input} />);

    expect(screen.getByText("Нужны финансовые решения")).toBeInTheDocument();
    expect(screen.getByText(/OWNER/)).toBeInTheDocument();
    expect(screen.getByText(/LEGAL/)).toBeInTheDocument();
    expect(screen.getByText(/Автоматическая оплата, банковская проводка и конвертация не выполняются/)).toBeInTheDocument();
  });

  it("shows customer receipt and passes the record version to explicit confirmation", () => {
    const input = props("approved");
    render(<FinanceOperations {...input} />);

    expect(screen.getByText(/Поступление заказчика/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить оплату — факт пользователем" }));

    expect(input.onConfirmPayment).toHaveBeenCalledWith(17, 75000, 4);
  });

  it("passes the current record version to a separate correction action", () => {
    const input = props("received");
    render(<FinanceOperations {...input} />);

    fireEvent.click(screen.getByRole("button", { name: "Исправить факт оплаты" }));

    expect(input.onCorrectPayment).toHaveBeenCalledWith(17, 74250, "2026-09-11", 4);
  });
});
