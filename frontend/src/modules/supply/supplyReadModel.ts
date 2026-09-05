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
  | "propose_dds"
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
  unitPrice: string;
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
  decisionRequirements: { code: string; decisionBy: "OWNER" | "LEGAL"; message: string }[];
  automaticConversion: false;
  paymentCreated: false;
}

export interface SupplyEvidenceOption {
  evidenceId: string;
  evidenceRevision: 1;
  sourceVersionId: string;
  documentVersionId: number;
  assessmentVersion: number;
  verification: "verified" | "unverified";
  confidence: number | null;
  locator: { kind: "page"; page: number } | { kind: "exact_fragment" | "unavailable" };
}

type UnknownRecord = Record<string, unknown>;

const supplyStatuses = new Set<SupplyStatus>([
  "needs_review", "request_pending_approval", "request_rejected", "request_approved",
  "order_draft", "order_approved", "order_recorded", "partially_delivered", "delivered",
  "delivery_discrepancy", "act_pending_approval", "partially_accepted", "accepted", "cancelled",
]);

function record(value: unknown, label: string): UnknownRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}: invalid object`);
  return value as UnknownRecord;
}

function positiveInteger(value: unknown, label: string): number {
  if (!Number.isInteger(value) || Number(value) <= 0) throw new Error(`${label}: invalid integer`);
  return Number(value);
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label}: invalid text`);
  return value;
}

export function parseSupplyList(value: unknown, projectId: number): SupplyCaseView[] {
  const envelope = record(value, "supply response");
  if (!Array.isArray(envelope.items) || !Number.isInteger(envelope.total)) {
    throw new Error("supply response: invalid envelope");
  }
  const items = envelope.items.map((raw, index): SupplyCaseView => {
    const item = record(raw, `supply item ${index}`);
    const status = text(item.status, "status") as SupplyStatus;
    if (!supplyStatuses.has(status)) throw new Error("status: unsupported value");
    const reviewState = text(item.reviewState, "reviewState");
    if (!["needs_review", "verified", "rejected"].includes(reviewState)) {
      throw new Error("reviewState: unsupported value");
    }
    const rowProjectId = positiveInteger(item.projectId, "projectId");
    if (rowProjectId !== projectId) throw new Error("projectId: scope mismatch");
    if (item.evidenceRevision !== 1 || item.externalActionStatus !== "not_created") {
      throw new Error("supply item: unsafe evidence or external action state");
    }
    if (!Array.isArray(item.decisionRequirements) || item.automaticConversion !== false
      || item.paymentCreated !== false) {
      throw new Error("supply item: missing finance guard");
    }
    const decisionRequirements = item.decisionRequirements.map((rawDecision) => {
      const decision = record(rawDecision, "decision requirement");
      const decisionBy = text(decision.decision_by, "decision owner");
      if (!['OWNER', 'LEGAL'].includes(decisionBy)) throw new Error("decision owner: unsupported value");
      return { code: text(decision.code, "decision code"),
        decisionBy: decisionBy as "OWNER" | "LEGAL", message: text(decision.message, "decision message") };
    });
    return {
      id: positiveInteger(item.id, "id"),
      recordVersion: positiveInteger(item.recordVersion, "recordVersion"),
      title: text(item.title, "title"),
      supplier: text(item.supplier, "supplier"),
      status,
      reviewState: reviewState as SupplyCaseView["reviewState"],
      requestedQuantity: text(item.requestedQuantity, "requestedQuantity"),
      orderedQuantity: text(item.orderedQuantity, "orderedQuantity"),
      deliveredQuantity: text(item.deliveredQuantity, "deliveredQuantity"),
      acceptedQuantity: text(item.acceptedQuantity, "acceptedQuantity"),
      unit: text(item.unit, "unit"),
      unitPrice: text(item.unitPrice, "unitPrice"),
      currency: text(item.currency, "currency"),
      projectId: rowProjectId,
      contractId: positiveInteger(item.contractId, "contractId"),
      scheduleBaselineId: positiveInteger(item.scheduleBaselineId, "scheduleBaselineId"),
      scheduleBaselineVersion: positiveInteger(item.scheduleBaselineVersion, "scheduleBaselineVersion"),
      scheduleItemId: positiveInteger(item.scheduleItemId, "scheduleItemId"),
      taskId: positiveInteger(item.taskId, "taskId"),
      documentVersionId: positiveInteger(item.documentVersionId, "documentVersionId"),
      evidenceId: text(item.evidenceId, "evidenceId"),
      evidenceRevision: 1,
      sourceVersionId: text(item.sourceVersionId, "sourceVersionId"),
      discrepancyCode: item.discrepancyCode === null || typeof item.discrepancyCode === "undefined"
        ? null : text(item.discrepancyCode, "discrepancyCode"),
      externalActionStatus: "not_created",
      decisionRequirements,
      automaticConversion: false,
      paymentCreated: false,
    };
  });
  if (envelope.total !== items.length) throw new Error("supply response: total mismatch");
  return items;
}

export function parseSupplyEvidenceOptions(value: unknown, projectId: number): SupplyEvidenceOption[] {
  const envelope = record(value, "evidence response");
  if (positiveInteger(envelope.projectId, "evidence projectId") !== projectId
    || !Array.isArray(envelope.items) || !Number.isInteger(envelope.total)) {
    throw new Error("evidence response: invalid envelope");
  }
  const items = envelope.items.map((raw, index): SupplyEvidenceOption => {
    const item = record(raw, `evidence item ${index}`);
    const locator = record(item.locator, "evidence locator");
    const kind = text(locator.kind, "evidence locator kind");
    const verification = text(item.verification, "verification");
    const confidence = item.confidence === null ? null
      : typeof item.confidence === "number" && Number.isFinite(item.confidence)
        && item.confidence >= 0 && item.confidence <= 1 ? item.confidence : NaN;
    if (item.evidenceRevision !== 1 || !["verified", "unverified"].includes(verification)
      || Number.isNaN(confidence) || !["page", "exact_fragment", "unavailable"].includes(kind)) {
      throw new Error("evidence item: unsafe value");
    }
    const safeLocator = kind === "page"
      ? { kind: "page" as const, page: positiveInteger(locator.page, "evidence page") }
      : { kind: kind as "exact_fragment" | "unavailable" };
    if (safeLocator.kind === "page" && !safeLocator.page) throw new Error("evidence page: invalid");
    return {
      evidenceId: text(item.evidenceId, "evidenceId"),
      evidenceRevision: 1,
      sourceVersionId: text(item.sourceVersionId, "sourceVersionId"),
      documentVersionId: positiveInteger(item.documentVersionId, "documentVersionId"),
      assessmentVersion: positiveInteger(item.assessmentVersion, "assessmentVersion"),
      verification: verification as SupplyEvidenceOption["verification"],
      confidence: confidence as number | null,
      locator: safeLocator as SupplyEvidenceOption["locator"],
    };
  });
  if (items.length !== envelope.total) throw new Error("evidence response: total mismatch");
  return items;
}

export function parseProjectOrganization(value: unknown, projectId: number): number {
  const project = record(value, "project response");
  if (positiveInteger(project.id, "project id") !== projectId) throw new Error("project response: scope mismatch");
  return positiveInteger(project.organization_id, "organization id");
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
  order_recorded: ["record_delivery", "propose_dds"],
  partially_delivered: ["record_delivery", "propose_act", "propose_dds"],
  delivered: ["propose_act", "propose_dds"],
  partially_accepted: ["record_delivery", "propose_act", "propose_dds"],
  accepted: ["propose_dds"],
};

export function availableSupplyActions(
  item: SupplyCaseView,
  canManage: boolean,
  canEdit = canManage,
): SupplyAction[] {
  if (item.externalActionStatus !== "not_created") return [];
  const actions = canEdit ? [...(editorActions[item.status] ?? [])] : [];
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
  propose_dds: "Предложить запись ДДС",
  approve_act: "Согласовать акт внутри системы",
};

export const supplyStatusLabels: Record<SupplyStatus, string> = {
  needs_review: "Нужна проверка",
  request_pending_approval: "Заявка ждёт согласования",
  request_rejected: "Заявка отклонена",
  request_approved: "Заявка согласована",
  order_draft: "Проект заказа",
  order_approved: "Заказ согласован",
  order_recorded: "Размещение зафиксировано",
  partially_delivered: "Частичная поставка",
  delivered: "Поставка зафиксирована",
  delivery_discrepancy: "Расхождение поставки",
  act_pending_approval: "Акт ждёт согласования",
  partially_accepted: "Частичная приёмка",
  accepted: "Принято",
  cancelled: "Отменено",
};

export const supplyActiveStep: Record<SupplyStatus, number> = {
  needs_review: 0,
  request_pending_approval: 1,
  request_rejected: 1,
  request_approved: 1,
  order_draft: 2,
  order_approved: 2,
  order_recorded: 2,
  partially_delivered: 3,
  delivered: 3,
  delivery_discrepancy: 3,
  act_pending_approval: 4,
  partially_accepted: 4,
  accepted: 4,
  cancelled: 0,
};
