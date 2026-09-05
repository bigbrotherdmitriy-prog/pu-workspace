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
