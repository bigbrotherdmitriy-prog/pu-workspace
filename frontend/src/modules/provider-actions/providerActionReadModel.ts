export type ProviderJobStatus = {
  jobId: number;
  status: "queued" | "running" | "retrying" | "failed" | "dead_letter" | "completed" | "cancelled";
  progress: number;
  attempts: number;
  maxAttempts: number;
  durationMs: number | null;
};

export type ProviderActionStatus = {
  actionId: string;
  revision: number;
  projectId: number;
  provider: "synthetic" | "google_workspace";
  actionKind:
    | "synthetic.effect.apply"
    | "synthetic.effect.send"
    | "synthetic.effect.rollback"
    | "synthetic.effect.compensate"
    | "synthetic.effect.corrective"
    | "gmail.message.send"
    | "google.tasks.upsert"
    | "google.calendar.upsert";
  mode: "CONFIRM";
  reversibility: "REVERSIBLE" | "COMPENSATABLE" | "IRREVERSIBLE";
  businessStatus:
    | "awaiting_approval"
    | "queued"
    | "running"
    | "completed"
    | "not_applied"
    | "requires_reconciliation"
    | "blocked"
    | "cancelled";
  approvalStatus: "missing" | "granted" | "revoked" | "expired";
  isCurrentRevision: boolean;
  dispatch: ProviderJobStatus | null;
  reconciliationStatus:
    | "not_required"
    | "required"
    | "queued"
    | "running"
    | "retrying"
    | "failed"
    | "dead_letter"
    | "cancelled"
    | "resolved";
  reconciliation: ProviderJobStatus | null;
  receiptId: number | null;
  receiptOutcome: "APPLIED" | "NOT_APPLIED" | "UNKNOWN" | null;
  receiptLate: boolean;
  retryState: "none" | "retrying" | "failed" | "dead_letter";
  safeReason:
    | "approval_revoked"
    | "approval_expired"
    | "adapter_failure"
    | "precondition_failed"
    | "provider_receipt_mismatch"
    | "receipt_not_found"
    | "timeout_after_effect"
    | "timeout_before_effect"
    | "outcome_unknown"
    | "job_failed"
    | "action_blocked"
    | null;
  createdAt: string;
};

export type ReconciliationResult = {
  actionId: string;
  revision: number;
  jobId: number;
  alreadyQueued: boolean;
};

type Dictionary = Record<string, unknown>;

const actionKeys = [
  "action_id", "revision", "project_id", "provider", "action_kind", "mode", "reversibility",
  "business_status", "approval_status", "is_current_revision", "dispatch", "reconciliation_status",
  "reconciliation", "receipt_id", "receipt_outcome", "receipt_late", "retry_state", "safe_reason",
  "created_at",
] as const;
const jobKeys = ["job_id", "status", "progress", "attempts", "max_attempts", "duration_ms"] as const;

const providers = ["synthetic", "google_workspace"] as const;
const actionKinds = [
  "synthetic.effect.apply", "synthetic.effect.send", "synthetic.effect.rollback",
  "synthetic.effect.compensate", "synthetic.effect.corrective", "gmail.message.send",
  "google.tasks.upsert", "google.calendar.upsert",
] as const;
const reversibilities = ["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE"] as const;
const businessStatuses = [
  "awaiting_approval", "queued", "running", "completed", "not_applied",
  "requires_reconciliation", "blocked", "cancelled",
] as const;
const approvalStatuses = ["missing", "granted", "revoked", "expired"] as const;
const jobStatuses = ["queued", "running", "retrying", "failed", "dead_letter", "completed", "cancelled"] as const;
const reconciliationStatuses = [
  "not_required", "required", "queued", "running", "retrying", "failed", "dead_letter", "cancelled", "resolved",
] as const;
const outcomes = ["APPLIED", "NOT_APPLIED", "UNKNOWN"] as const;
const retryStates = ["none", "retrying", "failed", "dead_letter"] as const;
const safeReasons = [
  "approval_revoked", "approval_expired", "adapter_failure", "precondition_failed",
  "provider_receipt_mismatch", "receipt_not_found", "timeout_after_effect", "timeout_before_effect",
  "outcome_unknown", "job_failed", "action_blocked",
] as const;

function dictionary(value: unknown): Dictionary | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Dictionary : null;
}

function hasOnlyKeys(value: Dictionary, keys: readonly string[]): boolean {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key)) && keys.every((key) => key in value);
}

function integer(value: unknown, minimum = 1): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum ? value : null;
}

function member<T extends readonly string[]>(value: unknown, allowed: T): T[number] | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? value as T[number] : null;
}

function nullableInteger(value: unknown, minimum = 0): number | null | undefined {
  return value === null ? null : integer(value, minimum) ?? undefined;
}

function parseJob(value: unknown): ProviderJobStatus | null {
  if (value === null) return null;
  const item = dictionary(value);
  if (!item || !hasOnlyKeys(item, jobKeys)) throw new Error("invalid_provider_action_response");
  const jobId = integer(item.job_id);
  const status = member(item.status, jobStatuses);
  const progress = integer(item.progress, 0);
  const attempts = integer(item.attempts, 0);
  const maxAttempts = integer(item.max_attempts);
  const durationMs = nullableInteger(item.duration_ms, 0);
  if (!jobId || !status || progress === null || progress > 100 || attempts === null || !maxAttempts
    || durationMs === undefined) throw new Error("invalid_provider_action_response");
  return { jobId, status, progress, attempts, maxAttempts, durationMs };
}

export function parseProviderAction(value: unknown, expectedProjectId: number): ProviderActionStatus {
  const item = dictionary(value);
  if (!item || !hasOnlyKeys(item, actionKeys)) throw new Error("invalid_provider_action_response");
  const actionId = typeof item.action_id === "string" && item.action_id.length > 0 && item.action_id.length <= 200
    ? item.action_id : null;
  const revision = integer(item.revision);
  const projectId = integer(item.project_id);
  const provider = member(item.provider, providers);
  const actionKind = member(item.action_kind, actionKinds);
  const reversibility = member(item.reversibility, reversibilities);
  const businessStatus = member(item.business_status, businessStatuses);
  const approvalStatus = member(item.approval_status, approvalStatuses);
  const reconciliationStatus = member(item.reconciliation_status, reconciliationStatuses);
  const receiptId = nullableInteger(item.receipt_id, 1);
  const receiptOutcome = item.receipt_outcome === null ? null : member(item.receipt_outcome, outcomes) ?? undefined;
  const retryState = member(item.retry_state, retryStates);
  const safeReason = item.safe_reason === null ? null : member(item.safe_reason, safeReasons) ?? undefined;
  const createdAt = typeof item.created_at === "string" && Number.isFinite(Date.parse(item.created_at))
    ? item.created_at : null;
  if (!actionId || !revision || projectId !== expectedProjectId || !provider || !actionKind || item.mode !== "CONFIRM"
    || !reversibility || !businessStatus || !approvalStatus || typeof item.is_current_revision !== "boolean"
    || !reconciliationStatus || receiptId === undefined || receiptOutcome === undefined || typeof item.receipt_late !== "boolean"
    || !retryState || safeReason === undefined || !createdAt) throw new Error("invalid_provider_action_response");
  return {
    actionId, revision, projectId, provider, actionKind, mode: "CONFIRM", reversibility, businessStatus,
    approvalStatus, isCurrentRevision: item.is_current_revision, dispatch: parseJob(item.dispatch),
    reconciliationStatus, reconciliation: parseJob(item.reconciliation), receiptId,
    receiptOutcome, receiptLate: item.receipt_late, retryState, safeReason, createdAt,
  };
}

export function parseProviderActionList(value: unknown, expectedProjectId: number): ProviderActionStatus[] {
  const root = dictionary(value);
  if (!root || !hasOnlyKeys(root, ["items", "count"]) || !Array.isArray(root.items)) {
    throw new Error("invalid_provider_action_response");
  }
  const count = integer(root.count, 0);
  const items = root.items.map((item) => parseProviderAction(item, expectedProjectId));
  if (count === null || count !== items.length) throw new Error("invalid_provider_action_response");
  return items;
}

export function parseReconciliationResult(
  value: unknown,
  expected: Pick<ProviderActionStatus, "actionId" | "revision">,
): ReconciliationResult {
  const root = dictionary(value);
  if (!root || !hasOnlyKeys(root, ["action_id", "revision", "job_id", "already_queued"])) {
    throw new Error("invalid_reconciliation_response");
  }
  const jobId = integer(root.job_id);
  if (root.action_id !== expected.actionId || root.revision !== expected.revision || !jobId
    || typeof root.already_queued !== "boolean") throw new Error("invalid_reconciliation_response");
  return { actionId: expected.actionId, revision: expected.revision, jobId, alreadyQueued: root.already_queued };
}

export function canRequestReconciliation(action: ProviderActionStatus): boolean {
  return action.provider === "google_workspace"
    && action.isCurrentRevision
    && action.businessStatus === "requires_reconciliation"
    && action.receiptOutcome === "UNKNOWN"
    && (["required", "failed", "dead_letter", "cancelled"] as const).includes(
      action.reconciliationStatus as "required" | "failed" | "dead_letter" | "cancelled",
    );
}
