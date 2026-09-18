export type ProviderEffect = {
  status: string;
  external_id?: string | null;
  action_id?: string;
  revision?: number;
};

export type ProviderEffects = {
  task: ProviderEffect;
  calendar: ProviderEffect;
};

type InboxTaskDeliverySource = {
  external_action_status: string;
  provider_effects?: ProviderEffects;
};

function effectText(effect: ProviderEffect | undefined): string {
  if (!effect) return "не проверено";
  switch (effect.status) {
    case "pending": return "ожидает";
    case "executing": return "выполняется";
    case "unknown": return "результат неизвестен";
    case "applied": return effect.external_id ? "✓ применено" : "результат неизвестен";
    case "failed": return "ошибка";
    case "not_requested": return "не запрошено";
    default: return "результат неизвестен";
  }
}

export function inboxTaskDelivery(task: InboxTaskDeliverySource) {
  const effects = task.provider_effects;
  const active = [effects?.task, effects?.calendar];
  const reconcile = active.filter((effect): effect is ProviderEffect =>
    effect?.status === "unknown" && Boolean(effect.action_id) && Number.isInteger(effect.revision),
  );
  return {
    label: `Google Tasks: ${effectText(effects?.task)} · Calendar: ${effectText(effects?.calendar)}`,
    canApprove: ["proposed", "failed"].includes(task.external_action_status)
      && !active.some((effect) => ["pending", "executing", "unknown"].includes(effect?.status || "")),
    reconcile,
  };
}
