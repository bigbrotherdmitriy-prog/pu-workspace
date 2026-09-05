export type EvidencePin = Readonly<Record<string, unknown>>;

export type AttentionKind =
  | "overdue_obligation"
  | "obligation_review"
  | "obligation"
  | "overdue_task"
  | "task"
  | "risk"
  | "decision";

export type AttentionItem = {
  kind: AttentionKind;
  entityType: "obligation" | "task" | "risk" | "decision";
  entityId: number;
  recordVersion: number;
  title: string;
  priority: "critical" | "high" | "normal" | "low";
  dueAt: string | null;
  status: string;
  explanation: "deadline_passed" | "human_review_required" | "open";
  evidencePins: EvidencePin[];
};

export type Obligation = {
  id: number;
  projectId: number;
  contractId: number | null;
  taskId: number | null;
  title: string;
  status: string;
  dueDate: string | null;
  dueTime: string | null;
  timezone: string;
  resultNote: string | null;
  sourceType: string;
  sourceName: string;
  sourceExcerpt: string;
  confidence: number;
  recordVersion: number;
  evidencePins: EvidencePin[];
  reviewState: string;
  escalationLevel: number;
  deadlinePolicy: DeadlinePolicy | null;
};

export type HistoryEvent = {
  sequence: number;
  event: string;
  fromStatus: string | null;
  toStatus: string;
  recordVersion: number;
  reason: string | null;
  evidencePins: EvidencePin[];
  occurredAt: string;
};

export type DigestNotification = {
  id: number;
  kind: string;
  title: string;
  body: string;
  entityType: string;
  entityId: number;
  isRead: boolean;
  createdAt: string;
};

export type DeadlinePolicy = {
  reminderDays: number[];
  quietHours: { start: string; end: string };
};

export type MeetingProposal = {
  kind: "obligation" | "task" | "decision";
  entityType: "obligation" | "decision";
  entityId: number;
  recordVersion: number;
  status: string;
  reviewState: string;
  taskId: number | null;
};

export type MeetingActionCandidate = {
  kind: "obligation" | "task" | "decision";
  title: string;
  owner_user_id: number;
  evidence_pins: EvidencePin[];
  due_date?: string | null;
  due_time?: string | null;
  timezone?: string;
};

export type DigestEnqueueResult = {
  jobId: number;
  status: string;
  externalActionsCreated: false;
};

export type DigestPreference = {
  projectId: number;
  userId: number;
  timezone: string;
  quietStart: string;
  quietEnd: string;
  channel: "in_app" | "disabled";
  cadence: "daily" | "weekdays";
  recordVersion: number;
  persisted: boolean;
  externalActionsEnabled: false;
};

export type DigestState = {
  status: "created" | "already_created" | "empty" | "disabled" | "deferred_quiet_hours" | "stale";
  localDate: string;
  deferredUntil: string | null;
  notificationId: number | null;
  externalActionsCreated: false;
};

type Dictionary = Record<string, unknown>;

function dictionary(value: unknown): Dictionary | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Dictionary
    : null;
}

function integer(value: unknown, minimum = 1): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum ? value : null;
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown, allowEmpty = false): string | null {
  return typeof value === "string" && (allowEmpty || value.trim().length > 0) ? value : null;
}

function nullableText(value: unknown): string | null | undefined {
  return value === null ? null : text(value) ?? undefined;
}

function dateTime(value: unknown): string | null {
  const parsed = text(value);
  return parsed && Number.isFinite(Date.parse(parsed)) ? parsed : null;
}

function pins(value: unknown): EvidencePin[] | null {
  if (!Array.isArray(value)) return null;
  const parsed = value.map(dictionary);
  return parsed.every((item): item is Dictionary => item !== null) ? parsed : null;
}

function parseDeadlinePolicy(value: unknown): DeadlinePolicy | null {
  const root = dictionary(value);
  const quiet = root && dictionary(root.quiet_hours);
  if (!root || !quiet || !Array.isArray(root.reminder_days)) return null;
  const reminderDays = root.reminder_days.map((item) => integer(item, 0));
  const start = text(quiet.start);
  const end = text(quiet.end);
  if (!reminderDays.every((item): item is number => item !== null) || !start || !end) return null;
  return { reminderDays, quietHours: { start, end } };
}

export function parseAttentionResponse(value: unknown): { items: AttentionItem[]; total: number; generatedAt: string } {
  const root = dictionary(value);
  if (!root || !Array.isArray(root.items)) throw new Error("invalid_attention_response");
  const total = integer(root.total, 0);
  const generatedAt = dateTime(root.generated_at);
  if (total === null || !generatedAt || root.external_actions_created !== false) {
    throw new Error("invalid_attention_response");
  }
  const allowedKinds: AttentionKind[] = ["overdue_obligation", "obligation_review", "obligation", "overdue_task", "task", "risk", "decision"];
  const allowedEntities = ["obligation", "task", "risk", "decision"] as const;
  const allowedPriorities = ["critical", "high", "normal", "low"] as const;
  const allowedExplanations = ["deadline_passed", "human_review_required", "open"] as const;
  const items = root.items.map((raw): AttentionItem => {
    const item = dictionary(raw);
    const kind = item && text(item.kind);
    const entityType = item && text(item.entity_type);
    const entityId = item && integer(item.entity_id);
    const recordVersion = item && integer(item.record_version);
    const title = item && text(item.title);
    const priority = item && text(item.priority);
    const dueAt = item && nullableText(item.due_at);
    const status = item && text(item.status);
    const explanation = item && text(item.explanation);
    const evidencePins = item && pins(item.evidence_pins);
    if (!item || !kind || !allowedKinds.includes(kind as AttentionKind)
      || !entityType || !allowedEntities.includes(entityType as typeof allowedEntities[number])
      || !entityId || !recordVersion || !title || !priority
      || !allowedPriorities.includes(priority as typeof allowedPriorities[number])
      || dueAt === undefined || !status || !explanation
      || !allowedExplanations.includes(explanation as typeof allowedExplanations[number]) || !evidencePins) {
      throw new Error("invalid_attention_response");
    }
    if (dueAt !== null && !dateTime(dueAt)) throw new Error("invalid_attention_response");
    return {
      kind: kind as AttentionKind,
      entityType: entityType as AttentionItem["entityType"],
      entityId,
      recordVersion,
      title,
      priority: priority as AttentionItem["priority"],
      dueAt,
      status,
      explanation: explanation as AttentionItem["explanation"],
      evidencePins,
    };
  });
  if (items.length > total) throw new Error("invalid_attention_response");
  return { items, total, generatedAt };
}

function parseObligation(raw: unknown): Obligation {
  const item = dictionary(raw);
  const id = item && integer(item.id);
  const projectId = item && integer(item.project_id);
  const contractId = !item ? undefined : item.contract_id === null ? null : integer(item.contract_id) ?? undefined;
  const taskId = !item ? undefined : item.task_id === null ? null : integer(item.task_id) ?? undefined;
  const title = item && text(item.title);
  const status = item && text(item.status);
  const dueDate = item && nullableText(item.due_date);
  const dueTime = item && nullableText(item.due_time);
  const timezone = item && text(item.timezone);
  const resultNote = item && nullableText(item.result_note);
  const sourceType = item && text(item.source_type);
  const sourceName = item && text(item.source_name);
  const sourceExcerpt = item && text(item.source_excerpt, true);
  const confidence = item && finite(item.confidence);
  const recordVersion = item && integer(item.record_version);
  const evidencePins = item && pins(item.evidence_pins);
  const reviewState = item && text(item.review_state);
  const escalationLevel = item && integer(item.escalation_level, 0);
  const deadlinePolicy = item?.deadline_policy === undefined || item.deadline_policy === null
    ? null : parseDeadlinePolicy(item.deadline_policy);
  if (!item || !id || !projectId || contractId === undefined || taskId === undefined || !title || !status
    || dueDate === undefined || dueTime === undefined || !timezone || resultNote === undefined
    || !sourceType || !sourceName || sourceExcerpt === null || confidence === null
    || confidence < 0 || confidence > 1 || !recordVersion || !evidencePins || !reviewState
    || escalationLevel === null || item.deadline_policy !== undefined && item.deadline_policy !== null && !deadlinePolicy) {
    throw new Error("invalid_obligations_response");
  }
  return {
    id, projectId, contractId: contractId as number | null, taskId: taskId as number | null,
    title, status, dueDate, dueTime, timezone, resultNote, sourceType, sourceName, sourceExcerpt,
    confidence, recordVersion, evidencePins, reviewState, escalationLevel, deadlinePolicy,
  };
}

export function parseObligationsResponse(value: unknown): Obligation[] {
  const root = dictionary(value);
  if (!root || !Array.isArray(root.obligations)) throw new Error("invalid_obligations_response");
  const result = root.obligations.map(parseObligation);
  const count = integer(root.count, 0);
  if (count === null || count !== result.length) throw new Error("invalid_obligations_response");
  return result;
}

export function parseHistoryResponse(value: unknown): HistoryEvent[] {
  const root = dictionary(value);
  if (!root || !Array.isArray(root.history)) throw new Error("invalid_history_response");
  return root.history.map((raw): HistoryEvent => {
    const item = dictionary(raw);
    const sequence = item && integer(item.sequence);
    const event = item && text(item.event);
    const fromStatus = item && nullableText(item.from_status);
    const toStatus = item && text(item.to_status);
    const recordVersion = item && integer(item.record_version);
    const reason = item && nullableText(item.reason);
    const evidencePins = item && pins(item.evidence_pins);
    const occurredAt = item && dateTime(item.occurred_at);
    if (!item || !sequence || !event || fromStatus === undefined || !toStatus || !recordVersion
      || reason === undefined || !evidencePins || !occurredAt) throw new Error("invalid_history_response");
    return { sequence, event, fromStatus, toStatus, recordVersion, reason, evidencePins, occurredAt };
  });
}

export function parseNotificationsResponse(value: unknown): DigestNotification[] {
  const root = dictionary(value);
  if (!root || !Array.isArray(root.notifications)) throw new Error("invalid_notifications_response");
  return root.notifications.map((raw): DigestNotification => {
    const item = dictionary(raw);
    const id = item && integer(item.id);
    const kind = item && text(item.kind);
    const title = item && text(item.title);
    const body = item && text(item.body);
    const entityType = item && text(item.entity_type);
    const entityId = item && integer(item.entity_id);
    const createdAt = item && dateTime(item.created_at);
    if (!item || !id || !kind || !title || !body || !entityType || !entityId
      || typeof item.is_read !== "boolean" || !createdAt) throw new Error("invalid_notifications_response");
    return { id, kind, title, body, entityType, entityId, isRead: item.is_read, createdAt };
  });
}

export function parseMeetingProposals(value: unknown): MeetingProposal[] {
  if (!Array.isArray(value)) throw new Error("invalid_meeting_proposals");
  return value.map((raw): MeetingProposal => {
    const item = dictionary(raw);
    const kind = item && text(item.kind);
    const entityType = item && text(item.entity_type);
    const entityId = item && integer(item.entity_id);
    const recordVersion = item && integer(item.record_version);
    const status = item && text(item.status);
    const reviewState = item && text(item.review_state);
    const taskId = !item ? undefined : item.task_id === null ? null : integer(item.task_id) ?? undefined;
    if (!item || !kind || !["obligation", "task", "decision"].includes(kind)
      || !entityType || !["obligation", "decision"].includes(entityType)
      || !entityId || !recordVersion || !status || !reviewState || taskId === undefined) {
      throw new Error("invalid_meeting_proposals");
    }
    return { kind: kind as MeetingProposal["kind"], entityType: entityType as MeetingProposal["entityType"],
      entityId, recordVersion, status, reviewState, taskId: taskId as number | null };
  });
}

export function parseMeetingProposalEnvelope(value: unknown): MeetingProposal[] {
  const root = dictionary(value);
  if (!root || root.external_actions_created !== false) throw new Error("invalid_meeting_proposals");
  return parseMeetingProposals(root.proposals);
}

export function parseMeetingProposalConfirmation(value: unknown): MeetingProposal {
  const root = dictionary(value);
  if (!root || root.external_actions_created !== false) throw new Error("invalid_meeting_proposals");
  const proposals = parseMeetingProposals([root.proposal]);
  return proposals[0];
}

export function parseDigestEnqueueResult(value: unknown): DigestEnqueueResult {
  const root = dictionary(value);
  const jobId = root && integer(root.job_id);
  const status = root && text(root.status);
  if (!root || !jobId || !status || root.external_actions_created !== false) {
    throw new Error("invalid_digest_enqueue_response");
  }
  return { jobId, status, externalActionsCreated: false };
}

export function parseDigestPreference(value: unknown): DigestPreference {
  const item = dictionary(value);
  const projectId = item && integer(item.project_id);
  const userId = item && integer(item.user_id);
  const timezone = item && text(item.timezone);
  const quietStart = item && text(item.quiet_start);
  const quietEnd = item && text(item.quiet_end);
  const recordVersion = item && integer(item.record_version, 0);
  if (!item || !projectId || !userId || !timezone || !quietStart || !quietEnd || recordVersion === null
    || !/^\d{2}:\d{2}(?::\d{2})?$/.test(quietStart) || !/^\d{2}:\d{2}(?::\d{2})?$/.test(quietEnd)
    || !["in_app", "disabled"].includes(String(item.channel))
    || !["daily", "weekdays"].includes(String(item.cadence))
    || typeof item.persisted !== "boolean" || item.external_actions_enabled !== false) {
    throw new Error("invalid_digest_preference");
  }
  return { projectId, userId, timezone, quietStart, quietEnd,
    channel: item.channel as DigestPreference["channel"], cadence: item.cadence as DigestPreference["cadence"],
    recordVersion, persisted: item.persisted, externalActionsEnabled: false };
}

export function parseDigestState(value: unknown): DigestState {
  const item = dictionary(value);
  const status = item && text(item.status);
  const localDate = item && text(item.local_date);
  const deferredUntil = !item ? undefined : item.deferred_until === undefined || item.deferred_until === null
    ? null : dateTime(item.deferred_until) ?? undefined;
  const notificationId = !item ? undefined : item.notification_id === undefined || item.notification_id === null
    ? null : integer(item.notification_id) ?? undefined;
  if (!item || !status || !["created", "already_created", "empty", "disabled", "deferred_quiet_hours", "stale"].includes(status)
    || !localDate || !/^\d{4}-\d{2}-\d{2}$/.test(localDate) || deferredUntil === undefined
    || notificationId === undefined || item.external_actions_created !== false) throw new Error("invalid_digest_state");
  return { status: status as DigestState["status"], localDate,
    deferredUntil: deferredUntil as string | null, notificationId: notificationId as number | null,
    externalActionsCreated: false };
}

export function evidencePinLabel(pin: EvidencePin): string {
  const ref = dictionary(pin.ref);
  const id = ref && dictionary(ref.id);
  const value = id && text(id.value);
  return value ? `Доказательство ${value}` : "Закреплённое доказательство";
}
