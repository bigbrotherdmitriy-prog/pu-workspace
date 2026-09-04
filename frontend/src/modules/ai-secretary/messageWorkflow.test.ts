import { describe, expect, it } from "vitest";
import { messageWorkflowClass, messageWorkflowLabel, type MessageWorkflowState } from "./messageWorkflow";

describe("AI Secretary message workflow", () => {
  it.each<[MessageWorkflowState, string]>([
    ["needs_context_confirmation", "Подтвердите контекст"],
    ["requires_action", "Требует действия"],
    ["awaiting_reply", "Ожидает ответа"],
    ["completed", "Обработано"],
    ["ready", "Новое"],
  ])("renders %s without inventing provider outcomes", (state, label) => {
    expect(messageWorkflowLabel(state)).toBe(label);
  });

  it("keeps attention and waiting states visually distinct", () => {
    expect(messageWorkflowClass("requires_action")).toBe("needs_context_confirmation");
    expect(messageWorkflowClass("awaiting_reply")).toBe("in_progress");
  });
});
