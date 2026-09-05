export type MessageWorkflowState =
  | "filtered"
  | "needs_context_confirmation"
  | "requires_action"
  | "awaiting_reply"
  | "completed"
  | "ready";

export const messageWorkflowLabel = (state: MessageWorkflowState): string => {
  switch (state) {
    case "filtered": return "Отфильтровано";
    case "needs_context_confirmation": return "Подтвердите контекст";
    case "requires_action": return "Требует действия";
    case "awaiting_reply": return "Ожидает ответа";
    case "completed": return "Обработано";
    default: return "Новое";
  }
};

export const messageWorkflowClass = (state: MessageWorkflowState): string => {
  if (state === "needs_context_confirmation" || state === "requires_action") return "needs_context_confirmation";
  if (state === "awaiting_reply") return "in_progress";
  return state;
};
