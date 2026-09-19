import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FinanceOperations } from "./FinanceOperations";
import type { InvoiceExtractionProposal } from "./types";


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
    finance: null, preview: null, selectedRows: [], setSelectedRows: vi.fn(),
    selectedContractId: 0, kind: "budget", title: "", amount: "", date: "", extra: "",
    objectName: "", category: "", note: "", sourceDocumentId: 0, scheduleItemId: 0,
    budgetLineId: 0, baselineId: 0, setKind: vi.fn(), setTitle: vi.fn(), setAmount: vi.fn(),
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
  it("shows evidence and requires an explicit human confirmation", () => {
    const onConfirmInvoice = vi.fn();
    render(<FinanceOperations {...props({ onConfirmInvoice })} />);

    expect(screen.getByText(/Итого 125 400,50 руб\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить и создать предложение" }));
    expect(onConfirmInvoice).toHaveBeenCalledTimes(1);
  });

  it("lets the manager override the AI category before import", () => {
    const onEditInvoice = vi.fn();
    render(<FinanceOperations {...props({ onEditInvoice })} />);

    fireEvent.change(screen.getByLabelText("Категория затрат счёта"), { target: { value: "2" } });
    expect(onEditInvoice).toHaveBeenCalledWith({ selected_cost_category_id: 2 });
  });

  it("does not enable confirmation while category is missing", () => {
    render(<FinanceOperations {...props({ invoiceProposal: { ...proposal, selected_cost_category_id: undefined } })} />);
    expect(screen.getByRole("button", { name: "Подтвердить и создать предложение" })).toBeDisabled();
  });
});
