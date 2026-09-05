export type SupplyStatus =
  | "needs_review"
  | "request_pending_approval"
  | "request_rejected"
  | "request_approved"
  | "order_draft"
  | "order_approved"
  | "order_recorded"
  | "partially_delivered"
  | "delivered"
  | "delivery_discrepancy"
  | "act_pending_approval"
  | "partially_accepted"
  | "accepted"
  | "cancelled";

export type SupplyAction =
  | "review"
  | "approve_request"
  | "prepare_order"
  | "approve_order"
  | "record_order"
  | "record_delivery"
  | "resolve_discrepancy"
  | "propose_act"
  | "approve_act";

export interface SupplyCaseView {
  id: number;
  recordVersion: number;
  title: string;
  supplier: string;
  status: SupplyStatus;
  reviewState: "needs_review" | "verified" | "rejected";
  requestedQuantity: string;
  orderedQuantity: string;
  deliveredQuantity: string;
  acceptedQuantity: string;
  unit: string;
  currency: string;
  projectId: number;
  contractId: number;
  scheduleBaselineId: number;
  scheduleBaselineVersion: number;
  scheduleItemId: number;
  taskId: number;
  documentVersionId: number;
  evidenceId: string;
  evidenceRevision: 1;
  sourceVersionId: string;
  discrepancyCode?: string | null;
  externalActionStatus: "not_created";
}

const managerActions: Partial<Record<SupplyStatus, SupplyAction>> = {
  needs_review: "review",
  request_pending_approval: "approve_request",
  order_draft: "approve_order",
  delivery_discrepancy: "resolve_discrepancy",
  act_pending_approval: "approve_act",
};

const editorActions: Partial<Record<SupplyStatus, SupplyAction[]>> = {
  request_approved: ["prepare_order"],
  order_approved: ["record_order"],
  order_recorded: ["record_delivery"],
  partially_delivered: ["record_delivery", "propose_act"],
  delivered: ["propose_act"],
  partially_accepted: ["record_delivery", "propose_act"],
};

export function availableSupplyActions(item: SupplyCaseView, canManage: boolean): SupplyAction[] {
  if (item.externalActionStatus !== "not_created") return [];
  const actions = [...(editorActions[item.status] ?? [])];
  const managerAction = managerActions[item.status];
  if (canManage && managerAction) actions.unshift(managerAction);
  return actions;
}

export const supplyActionLabels: Record<SupplyAction, string> = {
  review: "Проверить вручную",
  approve_request: "Согласовать заявку",
  prepare_order: "Подготовить заказ",
  approve_order: "Согласовать заказ",
  record_order: "Зафиксировать размещение",
  record_delivery: "Зафиксировать поставку",
  resolve_discrepancy: "Разобрать расхождение",
  propose_act: "Подготовить акт",
  approve_act: "Согласовать акт внутри системы",
};
