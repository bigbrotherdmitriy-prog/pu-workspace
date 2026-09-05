import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SupplyActionForm } from "./SupplyActionForm";
import type { SupplyAction, SupplyCaseView, SupplyEvidenceOption } from "./supplyReadModel";

afterEach(cleanup);

const item: SupplyCaseView = {
  id: 1, recordVersion: 5, title: "Synthetic item", supplier: "Synthetic supplier",
  status: "order_recorded", reviewState: "verified", requestedQuantity: "10.000",
  orderedQuantity: "10.000", deliveredQuantity: "0.000", acceptedQuantity: "0.000",
  unit: "pcs", unitPrice: "100.25", currency: "RUB", projectId: 7, contractId: 8, scheduleBaselineId: 9,
  scheduleBaselineVersion: 2, scheduleItemId: 10, taskId: 11, documentVersionId: 12,
  evidenceId: "00000000-0000-4000-8000-000000000013", evidenceRevision: 1,
  sourceVersionId: "00000000-0000-4000-8000-000000000014", externalActionStatus: "not_created",
  decisionRequirements: [], automaticConversion: false, paymentCreated: false,
};

const exact: SupplyEvidenceOption = {
  evidenceId: "00000000-0000-4000-8000-000000000020", evidenceRevision: 1,
  sourceVersionId: "00000000-0000-4000-8000-000000000021", documentVersionId: 22,
  assessmentVersion: 3, verification: "verified", confidence: 0.97,
  locator: { kind: "page", page: 2 },
};

function form(action: SupplyAction, onSubmit = vi.fn()) {
  render(<SupplyActionForm action={action} item={item} evidence={[exact]} evidenceLoading={false}
    onCancel={vi.fn()} onSubmit={onSubmit} />);
  return onSubmit;
}

describe("SupplyActionForm", () => {
  it("enforces three decimal quantity and two decimal money", () => {
    const submit = form("review");
    fireEvent.change(screen.getByLabelText(/Исправленное количество/), { target: { value: "1.0001" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    expect(screen.getByRole("alert")).toHaveTextContent("максимум 3");
    expect(submit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Исправленное количество/), { target: { value: "1.125" } });
    fireEvent.change(screen.getByLabelText(/Исправленная цена/), { target: { value: "10.001" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    expect(screen.getByRole("alert")).toHaveTextContent("максимум 2");
  });

  it("requires an explicit order quantity and reference", () => {
    const submit = form("prepare_order");
    fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "2.500" } });
    fireEvent.change(screen.getByLabelText("Номер заказа"), { target: { value: "PO-42" } });
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(submit).toHaveBeenCalledWith({ ordered_quantity: "2.500", order_reference: "PO-42" });
  });

  it("never reuses old evidence and requires an explicit current selection", () => {
    const submit = form("record_order");
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(screen.getByRole("alert")).toHaveTextContent("Выберите проверенное");
    expect(submit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Точное доказательство"), { target: { value: exact.evidenceId } });
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(submit).toHaveBeenCalledWith({ evidence: {
      evidence_id: exact.evidenceId, evidence_revision: 1,
      source_version_id: exact.sourceVersionId, document_version_id: 22,
    } });
  });

  it("binds delivery quantity, discrepancy decision and exact evidence", () => {
    const submit = form("record_delivery");
    fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "3.125" } });
    fireEvent.change(screen.getByLabelText("Расхождение"), { target: { value: "quantity" } });
    fireEvent.change(screen.getByLabelText("Описание расхождения"), { target: { value: "Short delivery" } });
    fireEvent.change(screen.getByLabelText("Точное доказательство"), { target: { value: exact.evidenceId } });
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({
      delivered_quantity: "3.125", discrepancy_code: "quantity", discrepancy_note: "Short delivery",
      evidence: expect.objectContaining({ evidence_id: exact.evidenceId }),
    }));
  });

  it("requires an explicit discrepancy resolution", () => {
    const submit = form("resolve_discrepancy");
    fireEvent.change(screen.getByLabelText("Решение"), { target: { value: "return_to_delivery" } });
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(submit).toHaveBeenCalledWith({ decision: "return_to_delivery" });
  });

  it("requires act number, quantity and newly selected evidence", () => {
    const submit = form("propose_act");
    fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "3.125" } });
    fireEvent.change(screen.getByLabelText("Номер акта"), { target: { value: "ACT-42" } });
    fireEvent.change(screen.getByLabelText("Точное доказательство"), { target: { value: exact.evidenceId } });
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({
      accepted_quantity: "3.125", act_number: "ACT-42",
      evidence: expect.objectContaining({ document_version_id: 22 }),
    }));
  });

  it("creates only an explicit stage-bound DDS proposal with exact evidence", () => {
    const submit = form("propose_dds");
    fireEvent.change(screen.getByLabelText("Плановая дата"), { target: { value: "2026-09-12" } });
    fireEvent.change(screen.getByLabelText(/Подтверждённая строка бюджета/), { target: { value: "44" } });
    fireEvent.change(screen.getByLabelText("Точное доказательство"), { target: { value: exact.evidenceId } });
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(submit).toHaveBeenCalledWith({
      contract_id: 8,
      schedule_item_id: 10,
      budget_line_id: 44,
      planned_date: "2026-09-12",
      amount: "1002.50",
      currency: "RUB",
      evidence_assessment_version: 3,
      evidence: {
        evidence_id: exact.evidenceId,
        evidence_revision: 1,
        source_version_id: exact.sourceVersionId,
        document_version_id: exact.documentVersionId,
      },
    });
    expect(screen.getByText(/только предложение/)).toBeInTheDocument();
  });
});
