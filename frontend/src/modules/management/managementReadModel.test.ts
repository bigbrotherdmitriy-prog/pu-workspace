import { describe, expect, it } from "vitest";
import {
  evidencePinLabel,
  parseAttentionResponse,
  parseDigestState,
  parseDigestEnqueueResult,
  parseDigestPreference,
  parseHistoryResponse,
  parseMeetingProposals,
  parseMeetingProposalConfirmation,
  parseMeetingProposalEnvelope,
  parseNotificationsResponse,
  parseObligationsResponse,
} from "./managementReadModel";

const pin = { ref: { id: { value: "ev-17" } } };

function attention(overrides: Record<string, unknown> = {}) {
  return { kind: "obligation_review", entity_type: "obligation", entity_id: 7, record_version: 3,
    title: "Передать акт", priority: "high", due_at: "2026-09-07T12:00:00+03:00",
    status: "needs_confirmation", explanation: "human_review_required", evidence_pins: [pin], ...overrides };
}

function obligation(overrides: Record<string, unknown> = {}) {
  return { id: 7, project_id: 3, contract_id: null, task_id: null, title: "Передать акт",
    status: "needs_confirmation", due_date: "2026-09-07", due_time: "12:00:00", timezone: "Europe/Moscow",
    result_note: null, source_type: "evidence", source_name: "Договор.pdf", source_excerpt: "п. 5.2",
    confidence: 0.76, record_version: 3, evidence_pins: [pin], review_state: "needs_review",
    escalation_level: 1, ...overrides };
}

describe("management runtime validation", () => {
  it("maps an attention response without losing evidence or CAS version", () => {
    const result = parseAttentionResponse({ items: [attention()], total: 1, offset: 0, limit: 50,
      generated_at: "2026-09-05T10:00:00Z", external_actions_created: false });
    expect(result.items[0]).toMatchObject({ entityId: 7, recordVersion: 3, priority: "high" });
    expect(result.items[0].evidencePins).toEqual([pin]);
  });

  it.each([
    ["allows no claim that external actions happened", { external_actions_created: true }],
    ["rejects an unknown kind", { items: [attention({ kind: "magic" })] }],
    ["rejects a malformed due date", { items: [attention({ due_at: "tomorrow" })] }],
    ["rejects a missing version", { items: [attention({ record_version: 0 })] }],
  ])("%s", (_label, patch) => {
    expect(() => parseAttentionResponse({ items: [attention()], total: 1,
      generated_at: "2026-09-05T10:00:00Z", external_actions_created: false, ...patch })).toThrow("invalid_attention_response");
  });

  it("accepts obligations and the optional deadline policy", () => {
    const rows = parseObligationsResponse({ obligations: [obligation({ deadline_policy: {
      reminder_days: [7, 1], quiet_hours: { start: "20:00", end: "08:00" },
    } })], count: 1 });
    expect(rows[0].deadlinePolicy).toEqual({ reminderDays: [7, 1], quietHours: { start: "20:00", end: "08:00" } });
  });

  it("does not invent a deadline policy when the current API omits it", () => {
    expect(parseObligationsResponse({ obligations: [obligation()], count: 1 })[0].deadlinePolicy).toBeNull();
  });

  it.each([
    obligation({ confidence: 4 }),
    obligation({ evidence_pins: ["raw"] }),
    obligation({ project_id: "3" }),
    obligation({ deadline_policy: { reminder_days: [1], quiet_hours: { start: null, end: "08:00" } } }),
  ])("rejects an unsafe obligation shape", (row) => {
    expect(() => parseObligationsResponse({ obligations: [row], count: 1 })).toThrow("invalid_obligations_response");
  });

  it("validates append-only history entries", () => {
    const result = parseHistoryResponse({ history: [{ sequence: 1, event: "confirmed", from_status: "needs_confirmation",
      to_status: "confirmed", record_version: 4, reason: null, evidence_pins: [pin], occurred_at: "2026-09-05T10:00:00Z" }] });
    expect(result[0].recordVersion).toBe(4);
  });

  it("rejects malformed history", () => {
    expect(() => parseHistoryResponse({ history: [{ sequence: 1 }] })).toThrow("invalid_history_response");
  });

  it("validates notification dates", () => {
    expect(parseNotificationsResponse({ notifications: [{ id: 2, kind: "management_digest", title: "Сводка",
      body: "Требуют внимания: 2", entity_type: "project", entity_id: 3, is_read: false,
      created_at: "2026-09-05T10:00:00Z" }], unread: 1 })).toHaveLength(1);
  });

  it("validates service-shaped meeting proposals", () => {
    expect(parseMeetingProposals([{ kind: "task", entity_type: "obligation", entity_id: 8,
      record_version: 2, status: "needs_confirmation", review_state: "needs_review", task_id: null }]))
      .toEqual([{ kind: "task", entityType: "obligation", entityId: 8, recordVersion: 2,
        status: "needs_confirmation", reviewState: "needs_review", taskId: null }]);
  });

  it("preserves explicit unbound meeting authority flags", () => {
    expect(parseMeetingProposals([{ kind: "task", entity_type: "obligation", entity_id: 8,
      record_version: 2, status: "needs_confirmation", review_state: "needs_review", task_id: null,
      origin_status: "invalid_source", origin_reason: "meeting_source_binding_required", confirmation_available: false }])[0])
      .toMatchObject({ originStatus: "invalid_source", originReason: "meeting_source_binding_required", confirmationAvailable: false });
  });

  it.each([{ confirmation_available: "false" }, { confirmation_available: null },
    { origin_status: false }, { origin_status: "" }, { origin_reason: {} }])("rejects malformed optional origin flags %j", flags => {
    expect(() => parseMeetingProposals([{ kind: "task", entity_type: "obligation", entity_id: 8,
      record_version: 2, status: "needs_confirmation", review_state: "needs_review", task_id: null, ...flags }]))
      .toThrow("invalid_meeting_proposals");
  });

  it("requires the no-external-action flag on proposal envelopes", () => {
    const proposal = { kind: "decision", entity_type: "decision", entity_id: 8, record_version: 2,
      status: "needs_confirmation", review_state: "needs_review", task_id: null };
    expect(parseMeetingProposalEnvelope({ proposals: [proposal], external_actions_created: false })).toHaveLength(1);
    expect(parseMeetingProposalConfirmation({ proposal, external_actions_created: false }).entityId).toBe(8);
    expect(() => parseMeetingProposalEnvelope({ proposals: [proposal], external_actions_created: true })).toThrow();
  });

  it.each([
    [{ confirmation_available: false }, { confirmation_available: true }],
    [{ confirmation_available: true }, { confirmation_available: false }],
    [{ origin_status: "unknown_future", confirmation_available: true }, {}],
    [{}, { origin_status: "unknown_future", confirmation_available: true }],
  ])("never overrides a denial between envelope and row %j %j", (envelopeFlags, rowFlags) => {
    const proposal = { kind: "decision", entity_type: "decision", entity_id: 8, record_version: 2,
      status: "needs_confirmation", review_state: "needs_review", task_id: null, ...rowFlags };
    expect(parseMeetingProposalEnvelope({ proposals: [proposal], external_actions_created: false, ...envelopeFlags })[0]
      .confirmationAvailable).toBe(false);
  });

  it("rejects malformed envelope origin metadata even for an empty list", () => {
    expect(() => parseMeetingProposalEnvelope({ proposals: [], external_actions_created: false, origin_status: null }))
      .toThrow("invalid_meeting_proposals");
  });

  it("validates a durable digest enqueue receipt", () => {
    expect(parseDigestEnqueueResult({ job_id: 41, status: "queued", external_actions_created: false }))
      .toEqual({ jobId: 41, status: "queued", externalActionsCreated: false });
    expect(() => parseDigestEnqueueResult({ job_id: 41, status: "queued" })).toThrow();
  });

  it("validates persisted digest preferences and rejects external actions", () => {
    expect(parseDigestPreference({ project_id: 3, user_id: 2, timezone: "Europe/Moscow",
      quiet_start: "20:00:00", quiet_end: "08:00:00", channel: "in_app", cadence: "weekdays",
      record_version: 2, persisted: true, external_actions_enabled: false })).toMatchObject({
      projectId: 3, recordVersion: 2, cadence: "weekdays", externalActionsEnabled: false,
    });
    expect(() => parseDigestPreference({ project_id: 3, user_id: 2, timezone: "Europe/Moscow",
      quiet_start: "20:00:00", quiet_end: "08:00:00", channel: "in_app", cadence: "daily",
      record_version: 2, persisted: true, external_actions_enabled: true })).toThrow("invalid_digest_preference");
  });

  it("fails closed for an unknown digest status or external effect", () => {
    expect(() => parseDigestState({ status: "sent", local_date: "2026-09-05", external_actions_created: false })).toThrow();
    expect(() => parseDigestState({ status: "created", local_date: "2026-09-05", external_actions_created: true })).toThrow();
  });

  it("maps a deferred digest without inventing completion", () => {
    expect(parseDigestState({ status: "deferred_quiet_hours", local_date: "2026-09-05",
      deferred_until: "2026-09-06T08:00:00+03:00", external_actions_created: false })).toMatchObject({
      status: "deferred_quiet_hours", notificationId: null,
    });
  });

  it("renders only an opaque evidence identifier", () => {
    expect(evidencePinLabel(pin)).toBe("Доказательство ev-17");
    expect(evidencePinLabel({ locator: { page: 1 } })).toBe("Закреплённое доказательство");
  });
});
