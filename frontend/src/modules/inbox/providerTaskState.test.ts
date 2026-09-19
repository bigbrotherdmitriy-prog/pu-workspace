import { describe, expect, it } from "vitest";

import { inboxTaskDelivery } from "./providerTaskState";

describe("inbox provider task delivery", () => {
  it("does not infer a checkmark from a legacy executed flag without provider receipts", () => {
    const state = inboxTaskDelivery({ external_action_status: "executed" });
    expect(state.label).not.toContain("✓");
    expect(state.canApprove).toBe(false);
  });

  it("shows the pending state and suppresses repeat confirmation", () => {
    const state = inboxTaskDelivery({
      external_action_status: "queued",
      provider_effects: { task: { status: "pending" }, calendar: { status: "pending" } },
    });
    expect(state.label).toContain("ожидает");
    expect(state.canApprove).toBe(false);
  });

  it("keeps task and calendar receipts separate after a partial failure", () => {
    const state = inboxTaskDelivery({
      external_action_status: "failed",
      provider_effects: {
        task: { status: "applied", external_id: "verified-task" },
        calendar: { status: "failed" },
      },
    });
    expect(state.label).toContain("Google Tasks: ✓ применено");
    expect(state.label).toContain("Calendar: ошибка");
    expect(state.canApprove).toBe(true);
  });

  it("does not trust APPLIED without a provider external reference", () => {
    const state = inboxTaskDelivery({
      external_action_status: "executed",
      provider_effects: {
        task: { status: "applied" }, calendar: { status: "not_requested" },
      },
    });
    expect(state.label).not.toContain("✓");
  });

  it("offers reconciliation only for unknown effects with exact action pins", () => {
    const state = inboxTaskDelivery({
      external_action_status: "unknown",
      provider_effects: {
        task: { status: "unknown", action_id: "google-task-7", revision: 2 },
        calendar: { status: "unknown" },
      },
    });
    expect(state.canApprove).toBe(false);
    expect(state.reconcile).toEqual([{ status: "unknown", action_id: "google-task-7", revision: 2 }]);
  });

  it("surfaces a dead-lettered reconciliation instead of silently staying unknown", () => {
    const state = inboxTaskDelivery({
      external_action_status: "unknown",
      provider_effects: {
        task: {
          status: "unknown", action_id: "google-task-7", revision: 1,
          reconciliation_status: "failed",
        },
        calendar: { status: "not_requested" },
      },
    });
    expect(state.label).toContain("проверка не удалась, требуется вмешательство");
    expect(state.reconciliationFailed).toBe(true);
    expect(state.reconcile).toHaveLength(1);
    expect(state.resolveAbsence).toHaveLength(0);
  });

  it("offers explicit human absence confirmation only after an empty provider lookup", () => {
    const effect = {
      status: "unknown", action_id: "google-task-7", revision: 1,
      safe_code: "receipt_not_found", observation_sequence: 2,
      reconciliation_status: "completed", can_confirm_absence: true,
    };
    const state = inboxTaskDelivery({
      external_action_status: "unknown",
      provider_effects: { task: effect, calendar: { status: "not_requested" } },
    });
    expect(state.label).toContain("объект не найден, подтвердите отсутствие");
    expect(state.reconcile).toHaveLength(0);
    expect(state.resolveAbsence).toEqual([effect]);
    expect(state.canApprove).toBe(false);
  });

  it("shows an in-progress reconciliation and suppresses duplicate clicks", () => {
    const state = inboxTaskDelivery({
      external_action_status: "unknown",
      provider_effects: {
        task: {
          status: "unknown", action_id: "google-task-7", revision: 1,
          reconciliation_status: "running",
        },
        calendar: { status: "not_requested" },
      },
    });
    expect(state.label).toContain("проверяется в Google");
    expect(state.reconcile).toHaveLength(0);
  });
});
