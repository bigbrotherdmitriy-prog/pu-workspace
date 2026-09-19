export type ProviderEffect = {
  status: string;
  external_id?: string | null;
  action_id?: string;
  revision?: number;
  safe_code?: string | null;
  observation_sequence?: number | null;
  reconciliation_status?: string | null;
  reconciliation_job_id?: number | null;
  can_confirm_absence?: boolean;
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
  if (effect.status === "unknown" && effect.reconciliation_status === "failed") {
    return "проверка не удалась, требуется вмешательство";
  }
  if (effect.status === "unknown" && ["queued", "running"].includes(effect.reconciliation_status || "")) {
    return "проверяется в Google";
  }
  if (effect.status === "unknown" && effect.can_confirm_absence) {
    return "объект не найден, подтвердите отсутствие";
  }
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
    effect?.status === "unknown"
      && !effect.can_confirm_absence
      && !["queued", "running"].includes(effect.reconciliation_status || "")
      && Boolean(effect.action_id)
      && Number.isInteger(effect.revision),
  );
  const resolveAbsence = active.filter((effect): effect is ProviderEffect =>
    effect?.status === "unknown"
      && effect.can_confirm_absence === true
      && Boolean(effect.action_id)
      && Number.isInteger(effect.revision)
      && Number.isInteger(effect.observation_sequence),
  );
  return {
    label: `Google Tasks: ${effectText(effects?.task)} · Calendar: ${effectText(effects?.calendar)}`,
    canApprove: ["proposed", "failed"].includes(task.external_action_status)
      && !active.some((effect) => ["pending", "executing", "unknown"].includes(effect?.status || "")),
    reconcile,
    resolveAbsence,
    reconciliationFailed: active.some((effect) =>
      effect?.status === "unknown" && effect.reconciliation_status === "failed"),
  };
}
