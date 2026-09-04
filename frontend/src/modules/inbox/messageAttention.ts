export type AttentionMessage = {
  status: string;
  context_confirmed: boolean;
  tasks: { external_action_status: string }[];
  drafts: { status: string }[];
  risks: { status: string }[];
  completion_suggestions: { status: string }[];
};

export function messageNeedsAttention(item: AttentionMessage): boolean {
  if (item.status === "filtered" || item.status === "completed") return false;
  return !item.context_confirmed
    || item.status === "in_progress"
    || item.tasks.some((task) => task.external_action_status === "proposed")
    || item.drafts.some((draft) => draft.status !== "sent")
    || item.risks.some((risk) => risk.status === "needs_confirmation")
    || item.completion_suggestions.some((suggestion) => suggestion.status === "proposed");
}
