import type { StorageApi } from "./storage-fixtures";

export const evidencePin = { ref: { id: { value: "synthetic-evidence-17" } } };

export function attention(entityType: "obligation" | "task" | "risk" | "decision", id: number, options: {
  title?: string;
  review?: boolean;
  overdue?: boolean;
  recordVersion?: number;
} = {}) {
  const review = options.review ?? false;
  const overdue = options.overdue ?? false;
  return {
    kind: entityType === "obligation"
      ? review ? "obligation_review" : overdue ? "overdue_obligation" : "obligation"
      : entityType === "task" ? overdue ? "overdue_task" : "task" : entityType,
    entity_type: entityType,
    entity_id: id,
    record_version: options.recordVersion ?? 3,
    title: options.title ?? `Synthetic ${entityType} ${id}`,
    priority: overdue ? "critical" : review ? "high" : "normal",
    due_at: overdue ? "2026-09-01T09:00:00+03:00" : null,
    status: review ? "needs_confirmation" : "open",
    explanation: overdue ? "deadline_passed" : review ? "human_review_required" : "open",
    evidence_pins: [evidencePin],
  };
}

export function obligation(id: number, projectId: number, options: { confidence?: number; review?: boolean; title?: string } = {}) {
  const review = options.review ?? false;
  return {
    id,
    project_id: projectId,
    contract_id: null,
    task_id: null,
    title: options.title ?? `Synthetic obligation ${id}`,
    status: review ? "needs_confirmation" : "confirmed",
    due_date: "2026-09-08",
    due_time: "12:00:00",
    timezone: "Europe/Moscow",
    result_note: null,
    source_type: "evidence",
    source_name: "Synthetic contract",
    source_excerpt: "Exact synthetic clause",
    confidence: options.confidence ?? (review ? 0.54 : 0.96),
    record_version: 3,
    evidence_pins: [evidencePin],
    review_state: review ? "needs_review" : "verified",
    escalation_level: 0,
    deadline_policy: { reminder_days: [7, 1], quiet_hours: { start: "20:00", end: "08:00" } },
  };
}

export function digestPreference(projectId: number, userId = 900, recordVersion = 4) {
  return {
    project_id: projectId,
    user_id: userId,
    timezone: "Europe/Moscow",
    quiet_start: "20:00:00",
    quiet_end: "08:00:00",
    channel: "in_app",
    cadence: "daily",
    record_version: recordVersion,
    persisted: recordVersion > 0,
    external_actions_enabled: false,
  };
}

export function installManagement(mock: StorageApi, projectId: number, rows: ReturnType<typeof attention>[], obligations: ReturnType<typeof obligation>[] = []) {
  mock.reply("GET", `/management/v2/attention?project_id=${projectId}`, { body: {
    items: rows, total: rows.length, offset: 0, limit: 50,
    generated_at: "2026-09-05T10:00:00Z", external_actions_created: false,
  } });
  mock.reply("GET", `/management/obligations?project_id=${projectId}`, { body: { obligations, count: obligations.length } });
  mock.reply("GET", `/management/notifications?project_id=${projectId}`, { body: { notifications: [], unread: 0 } });
  mock.reply("GET", `/management/v2/projects/${projectId}/digest-preference`, { body: digestPreference(projectId) });
}

export function history(entityType: "obligation" | "risk" | "decision", id: number) {
  return { body: { history: [{ sequence: 1, event: "created", from_status: null, to_status: "needs_confirmation",
    record_version: 1, reason: null, evidence_pins: [evidencePin], occurred_at: "2026-09-05T08:00:00Z" }] } };
}
