import { describe, expect, it } from "vitest";
import { messageNeedsAttention, type AttentionMessage } from "./messageAttention";

const message = (overrides: Partial<AttentionMessage> = {}): AttentionMessage => ({
  status: "ready",
  context_confirmed: true,
  tasks: [],
  drafts: [],
  risks: [],
  completion_suggestions: [],
  ...overrides,
});

describe("messageNeedsAttention", () => {
  it("keeps filtered and completed messages out of the action queue", () => {
    expect(messageNeedsAttention(message({ status: "filtered", context_confirmed: false }))).toBe(false);
    expect(messageNeedsAttention(message({ status: "completed", context_confirmed: false }))).toBe(false);
  });

  it("includes only messages with a pending human action", () => {
    expect(messageNeedsAttention(message())).toBe(false);
    expect(messageNeedsAttention(message({ context_confirmed: false }))).toBe(true);
    expect(messageNeedsAttention(message({ tasks: [{ external_action_status: "proposed" }] }))).toBe(true);
    expect(messageNeedsAttention(message({ drafts: [{ status: "draft" }] }))).toBe(true);
  });
});
