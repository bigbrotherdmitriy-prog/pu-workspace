import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FinanceOperations } from "./FinanceOperations";
import type { FinanceOverview, FinanceStructuredPreview, InvoiceExtractionProposal } from "./types";


const proposal: InvoiceExtractionProposal = {
  id: 7,
  project_id: 3,
  source_document_id: 11,
  source_document_version_id: 12,
  source_document_sha256: "a".repeat(64),
  amount: 125400.5,
  amount_evidence_quote: "Итого 125 400,50 руб.",
  currency: "RUB",
  counterparty: "ООО Бетон",
  counterparty_evidence_quote: "ООО Бетон",
  payment_purpose: "строительные материалы",
  payment_purpose_evidence_quote: "строительные материалы",
  proposed_cost_category_id: 1,
  selected_cost_category_id: 1,
  category_evidence_quote: "строительные материалы",
  planned_date: "2026-09-20",
  confidence: 0.92,
  extraction_method: "llm",
  target_kind: "cash_flow",
  status: "proposed",
  requires_confirmation: true,
};

afterEach(cleanup);

function props(overrides: Record<string, unknown> = {}) {
  return {
    finance: { baselines: [{ id: 51, contract_id: 41 }], schedule: [{ id: 52, baseline_id: 51, title: "Монтаж" }],
      budget: [{ id: 61, contract_id: 41, description: "Монтаж" }] } as unknown as FinanceOverview,
    preview: null, selectedRows: [], setSelectedRows: vi.fn(),
    selectedContractId: 41, kind: "budget", title: "", amount: "", date: "", extra: "",
    objectName: "", category: "", note: "", sourceDocumentId: 0, scheduleItemId: 52,
    budgetLineId: 61, baselineId: 0, setKind: vi.fn(), setTitle: vi.fn(), setAmount: vi.fn(),
    setDate: vi.fn(), setExtra: vi.fn(), setObjectName: vi.fn(), setCategory: vi.fn(),
    setNote: vi.fn(), setScheduleItemId: vi.fn(), setBudgetLineId: vi.fn(),
    setBaselineId: vi.fn(), onClosePreview: vi.fn(), onImport: vi.fn(), onAdd: vi.fn(),
    onConfirm: vi.fn(), onConfirmPayment: vi.fn(), includeRegisters: false,
    costCategories: [
      { id: 1, name: "Прямые", is_active: true, sort_order: 10 },
      { id: 2, name: "Зарплата", is_active: true, sort_order: 20 },
    ],
    invoiceProposal: proposal,
    onEditInvoice: vi.fn(), onConfirmInvoice: vi.fn(), onRejectInvoice: vi.fn(),
    onCloseInvoice: vi.fn(), onAddCostCategory: vi.fn(), ...overrides,
  };
}

describe("invoice extraction review", () => {
  it("limits the separate GPR editor to schedule records", () => {
    render(<FinanceOperations {...props({
      invoiceProposal: null,
      editorScope: "gpr",
      kind: "baseline",
    })} />);

    const selector = screen.getByLabelText("Тип финансовой записи");
    expect(selector).toHaveTextContent("Версия ГПР");
    expect(selector).toHaveTextContent("Этап ГПР");
    expect(selector).not.toHaveTextContent("Строка бюджета");
    expect(screen.getByRole("heading", { name: "Добавить версию или задачу ГПР" })).toBeInTheDocument();
  });

  it("shows evidence and requires an explicit human confirmation", () => {
    const onConfirmInvoice = vi.fn();
    render(<FinanceOperations {...props({ onConfirmInvoice })} />);

    expect(screen.getByText(/Итого 125 400,50 руб\./)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Строка планового ДДС счёта"), { target: { value: "additional" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить счёт" }));
    expect(onConfirmInvoice).toHaveBeenCalledTimes(1);
  });

  it("books the invoice against a plan DDS line or «Дополнительные затраты» without GPR/budget pickers", () => {
    const onConfirmInvoice = vi.fn();
    const finance = { baselines: [], schedule: [], budget: [], cash_flow: [
      { id: 90, contract_id: 41, direction: "outflow", title: "Дубна, монтаж, материалы", planned_amount: 500000, counterparty: "ООО Бетон" },
      { id: 91, contract_id: 41, direction: "inflow", title: "Этапы Дубна", planned_amount: 900000 },
    ] } as unknown as FinanceOverview;
    render(<FinanceOperations {...props({ finance, onConfirmInvoice, scheduleItemId: 0, budgetLineId: 0 })} />);
    const select = screen.getByLabelText("Строка планового ДДС счёта");
    expect(within(select).getByRole("option", { name: /Дубна, монтаж, материалы/ })).toBeInTheDocument();
    expect(within(select).queryByRole("option", { name: /Этапы Дубна/ })).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: "90" } });
    expect(screen.queryByLabelText("Этап ГПР счёта")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить счёт" }));
    expect(onConfirmInvoice).toHaveBeenLastCalledWith({ plan_entry_id: 90 });
    fireEvent.change(select, { target: { value: "additional" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить счёт" }));
    expect(onConfirmInvoice).toHaveBeenLastCalledWith({ additional: true });
  });

  it("offers one plan article with its total and books the invoice into the month that still covers it", () => {
    const onConfirmInvoice = vi.fn();
    const finance = { baselines: [], schedule: [], budget: [], cash_flow: [
      { id: 191, contract_id: 41, direction: "outflow", title: "Материалы для ЭМ", planned_date: "2026-02-28", planned_amount: 2378718, invoiced_amount: 2378718 },
      { id: 192, contract_id: 41, direction: "outflow", title: "Материалы для ЭМ", planned_date: "2026-07-31", planned_amount: 14000, invoiced_amount: 0 },
    ] } as unknown as FinanceOverview;
    render(<FinanceOperations {...props({ finance, onConfirmInvoice, invoiceProposal: { ...proposal, amount: 5000 } })} />);
    const select = screen.getByLabelText("Строка планового ДДС счёта");
    const options = within(select).getAllByRole("option", { name: /Материалы для ЭМ/ });
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toMatch(/план 2\s?392\s?718/);
    expect(options[0].textContent).toMatch(/остаток 14\s?000/);
    fireEvent.change(select, { target: { value: options[0].getAttribute("value") } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить счёт" }));
    expect(onConfirmInvoice).toHaveBeenLastCalledWith({ plan_entry_id: 192 });
  });

  it("lets the manager override the AI category before import", () => {
    const onEditInvoice = vi.fn();
    render(<FinanceOperations {...props({ onEditInvoice })} />);

    fireEvent.change(screen.getByLabelText("Категория затрат счёта"), { target: { value: "2" } });
    expect(onEditInvoice).toHaveBeenCalledWith({ selected_cost_category_id: 2 });
  });

  it("does not enable confirmation while category is missing", () => {
    render(<FinanceOperations {...props({ invoiceProposal: { ...proposal, selected_cost_category_id: undefined } })} />);
    expect(screen.getByRole("button", { name: "Подтвердить счёт" })).toBeDisabled();
  });

  it("does not ask for a GPR stage or budget line on an invoice (owner 16.10.2026)", () => {
    render(<FinanceOperations {...props({ scheduleItemId: 0, budgetLineId: 0 })} />);
    expect(screen.queryByLabelText("Этап ГПР счёта")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Строка бюджета счёта")).not.toBeInTheDocument();
  });

  it("lets a manual budget line be marked as income or expense (ADR-V6-05-INCOME-BUDGET-RU)", () => {
    const setDirection = vi.fn();
    render(<FinanceOperations {...props({
      invoiceProposal: null, kind: "budget", direction: "outflow", setDirection,
    })} />);

    const select = screen.getByLabelText("Направление строки бюджета");
    expect(select).toHaveValue("outflow");
    fireEvent.change(select, { target: { value: "inflow" } });
    expect(setDirection).toHaveBeenCalledWith("inflow");
  });

  it("hides the direction selector for non-budget record kinds", () => {
    render(<FinanceOperations {...props({ invoiceProposal: null, kind: "cash-in" })} />);
    expect(screen.queryByLabelText("Направление строки бюджета")).not.toBeInTheDocument();
  });

  it("names the missing plan line instead of silently disabling confirmation", () => {
    render(<FinanceOperations {...props({ scheduleItemId: 0, budgetLineId: 0 })} />);

    expect(screen.getByRole("button", { name: "Подтвердить счёт" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/строка планового ДДС/);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/этап ГПР/);
  });

  it("offers AI retry only for a temporary provider fallback", () => {
    const onRetryInvoiceAi = vi.fn();
    render(<FinanceOperations {...props({
      invoiceProposal: { ...proposal, extraction_method: "regex", fallback_reason: "temporarily_unavailable" },
      onRetryInvoiceAi,
    })} />);

    fireEvent.click(screen.getByRole("button", { name: "Повторить AI-анализ" }));
    expect(onRetryInvoiceAi).toHaveBeenCalledTimes(1);
  });

  it("does not offer retry for a permanent AI fallback", () => {
    render(<FinanceOperations {...props({
      invoiceProposal: { ...proposal, extraction_method: "regex", fallback_reason: "policy_blocked" },
    })} />);

    expect(screen.queryByRole("button", { name: "Повторить AI-анализ" })).not.toBeInTheDocument();
  });

  it("disables the retry button while analysis is running", () => {
    render(<FinanceOperations {...props({
      invoiceProposal: { ...proposal, extraction_method: "regex", fallback_reason: "temporarily_unavailable" },
      invoiceAiRetrying: true,
    })} />);

    expect(screen.getByRole("button", { name: "Повторный анализ…" })).toBeDisabled();
  });
});

describe("monthly DDS review", () => {
  const monthlyPreview = {
    document_id: 90, name: "для PU ДДС.xlsx", kind: "cash-flow", mapping: {}, issues: [],
    truncated: false, layout: "monthly_matrix", plan_year: 2026, inferred_december: true,
    rows: [
      { selection_id: 3003, source_row: 3, source_sheet: "ДДС", source_coordinate: "ДДС!C3", title: "Этапы Дубна", category: "Прочее", planned_date: "2026-01-31", amount: "100.00", direction: "inflow", progress: 0, issues: [], importable: true },
      { selection_id: 3014, source_row: 3, source_sheet: "ДДС", source_coordinate: "ДДС!N3", title: "Этапы Дубна", category: "Прочее", planned_date: "2026-12-31", amount: "900.00", direction: "inflow", progress: 0, issues: [], importable: true },
    ],
  } as FinanceStructuredPreview;

  it("keeps separate month cells selectable when they come from the same source row", () => {
    const { rerender } = render(<FinanceOperations {...props({ invoiceProposal: null, preview: monthlyPreview, selectedRows: [3003] })} />);

    expect(screen.getByText(/2026 год/)).toHaveTextContent("Колонка после ноября распознана как декабрь");
    const review = screen.getByRole("heading", { name: "для PU ДДС.xlsx" }).closest("section");
    expect(review).not.toBeNull();
    const checkboxes = within(review as HTMLElement).getAllByRole("checkbox");
    expect(checkboxes[0]).toBeChecked();
    expect(checkboxes[1]).not.toBeChecked();

    rerender(<FinanceOperations {...props({ invoiceProposal: null, preview: monthlyPreview, selectedRows: [3014] })} />);
    const updated = within(screen.getByRole("heading", { name: "для PU ДДС.xlsx" }).closest("section") as HTMLElement).getAllByRole("checkbox");
    expect(updated[0]).not.toBeChecked();
    expect(updated[1]).toBeChecked();
  });

  it("lets the manager correct imported DDS fields before creating proposals", () => {
    const onEditPreviewRow = vi.fn();
    render(<FinanceOperations {...props({ invoiceProposal: null, preview: monthlyPreview, selectedRows: [3003], onEditPreviewRow })} />);

    fireEvent.change(screen.getByLabelText("Наименование ДДС!C3"), { target: { value: "Оплата оборудования" } });
    fireEvent.change(screen.getByLabelText("Дата ДДС!C3"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Сумма ДДС!C3"), { target: { value: "125000" } });

    expect(onEditPreviewRow).toHaveBeenCalledWith(3003, { title: "Оплата оборудования" });
    expect(onEditPreviewRow).toHaveBeenCalledWith(3003, { planned_date: "2026-02-10" });
    expect(onEditPreviewRow).toHaveBeenCalledWith(3003, { amount: "125000" });
  });
});
