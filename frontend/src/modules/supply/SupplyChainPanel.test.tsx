import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SupplyChainPanel } from "./SupplyChainPanel";
import { availableSupplyActions, type SupplyCaseView } from "./supplyReadModel";

afterEach(cleanup);

const base: SupplyCaseView = {
  id: 1, recordVersion: 3, title: "Синтетическое оборудование", supplier: "Тестовый поставщик",
  status: "request_pending_approval", reviewState: "verified", requestedQuantity: "10",
  orderedQuantity: "0", deliveredQuantity: "0", acceptedQuantity: "0", unit: "шт", unitPrice: "100.25", currency: "RUB",
  projectId: 4, contractId: 5, scheduleBaselineId: 6, scheduleBaselineVersion: 2,
  scheduleItemId: 7, taskId: 8, documentVersionId: 9,
  evidenceId: "00000000-0000-4000-8000-000000000010", evidenceRevision: 1,
  sourceVersionId: "00000000-0000-4000-8000-000000000011", externalActionStatus: "not_created",
  decisionRequirements: [], automaticConversion: false, paymentCreated: false,
};

describe("SupplyChainPanel", () => {
  it("shows exact business links and the no-external-action boundary", () => {
    render(<SupplyChainPanel item={base} canManage onAction={vi.fn()} />);
    expect(screen.getByText("Заявка ждёт согласования")).toBeInTheDocument();
    expect(screen.getByText("Согласование")).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByText("Связи и доказательства"));
    expect(screen.getByText(/Договор #5/)).toBeInTheDocument();
    expect(screen.getByText(/ГПР #6 v2/)).toBeInTheDocument();
    expect(screen.getByText(/не размещает заказ/)).toBeInTheDocument();
  });

  it("offers monetary approval only to a manager", () => {
    expect(availableSupplyActions(base, false)).toEqual([]);
    expect(availableSupplyActions(base, true)).toEqual(["approve_request"]);
  });

  it("requires visible human review for low confidence", () => {
    const onAction = vi.fn();
    render(<SupplyChainPanel item={{ ...base, status: "needs_review", reviewState: "needs_review" }} canManage onAction={onAction} />);
    expect(screen.getByRole("alert")).toHaveTextContent("ручной проверки");
    fireEvent.click(screen.getByRole("button", { name: "Проверить вручную" }));
    expect(onAction).toHaveBeenCalledWith("review", expect.objectContaining({ id: 1 }));
  });

  it("blocks acceptance UI while a discrepancy is unresolved", () => {
    const item = { ...base, status: "delivery_discrepancy" as const, discrepancyCode: "quantity" };
    render(<SupplyChainPanel item={item} canManage onAction={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Акт заблокирован");
    expect(screen.queryByRole("button", { name: "Подготовить акт" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Разобрать расхождение" })).toBeInTheDocument();
  });

  it("exposes partial delivery and act actions without implying signature", () => {
    const item = { ...base, status: "partially_delivered" as const, deliveredQuantity: "4" };
    render(<SupplyChainPanel item={item} canManage={false} canEdit onAction={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Зафиксировать поставку" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подготовить акт" })).toBeInTheDocument();
    expect(screen.queryByText(/подписать/i)).not.toBeInTheDocument();
  });

  it("does not expose editor actions to a viewer", () => {
    const item = { ...base, status: "order_approved" as const };
    render(<SupplyChainPanel item={item} canManage={false} canEdit={false} onAction={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Зафиксировать размещение" })).not.toBeInTheDocument();
    expect(screen.getByText("Действий сейчас нет")).toBeInTheDocument();
  });
});
