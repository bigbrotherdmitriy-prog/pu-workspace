import { canRequestReconciliation, type ProviderActionStatus } from "./providerActionReadModel";
import { useProviderActions } from "./useProviderActions";
import "./provider-actions.css";

type Props = { projectId: number | null; enabled?: boolean };

const businessLabels: Record<ProviderActionStatus["businessStatus"], string> = {
  awaiting_approval: "Ожидает подтверждения",
  queued: "В очереди",
  running: "Выполняется",
  completed: "Выполнено",
  not_applied: "Не применено",
  requires_reconciliation: "Результат требует проверки",
  blocked: "Заблокировано",
  cancelled: "Отменено",
};

const kindLabels: Record<ProviderActionStatus["actionKind"], string> = {
  "synthetic.effect.apply": "Тестовое применение",
  "synthetic.effect.send": "Тестовая отправка",
  "synthetic.effect.rollback": "Тестовый откат",
  "synthetic.effect.compensate": "Тестовая компенсация",
  "synthetic.effect.corrective": "Тестовая корректировка",
  "gmail.message.send": "Отправка письма Gmail",
  "google.tasks.upsert": "Обновление Google Tasks",
  "google.calendar.upsert": "Обновление Google Calendar",
};

const reasonLabels: Record<Exclude<ProviderActionStatus["safeReason"], null>, string> = {
  approval_revoked: "Подтверждение отозвано",
  approval_expired: "Срок подтверждения истёк",
  adapter_failure: "Сервис провайдера временно недоступен",
  precondition_failed: "Исходное состояние изменилось",
  provider_receipt_mismatch: "Ответ провайдера требует проверки",
  receipt_not_found: "Подтверждение результата не найдено",
  timeout_after_effect: "Связь прервалась после возможного выполнения",
  timeout_before_effect: "Связь прервалась до подтверждения выполнения",
  outcome_unknown: "Результат операции неизвестен",
  job_failed: "Фоновое задание завершилось ошибкой",
  action_blocked: "Действие заблокировано политикой",
};

function jobLabel(job: ProviderActionStatus["dispatch"]): string {
  if (!job) return "Нет активного задания";
  return `Задание № ${job.jobId}: ${job.status}, попытка ${job.attempts} из ${job.maxAttempts}`;
}

function ActionCard({ action, busy, onReconcile }: {
  action: ProviderActionStatus;
  busy: boolean;
  onReconcile: (action: ProviderActionStatus) => void;
}) {
  const canReconcile = canRequestReconciliation(action);
  return <article className={`provider-action-card provider-action-card--${action.businessStatus}`}>
    <header>
      <div>
        <span>{action.provider === "google_workspace" ? "GOOGLE WORKSPACE" : "СИНТЕТИЧЕСКИЙ РЕЖИМ"}</span>
        <h3>{kindLabels[action.actionKind]}</h3>
      </div>
      <strong>{businessLabels[action.businessStatus]}</strong>
    </header>
    <dl>
      <div><dt>Действие</dt><dd>{action.actionId}</dd></div>
      <div><dt>Ревизия</dt><dd>v{action.revision}{action.isCurrentRevision ? " · текущая" : " · историческая"}</dd></div>
      <div><dt>Подтверждение</dt><dd>{action.approvalStatus}</dd></div>
      <div><dt>Исполнение</dt><dd>{jobLabel(action.dispatch)}</dd></div>
      <div><dt>Проверка результата</dt><dd>{action.reconciliationStatus}</dd></div>
      <div><dt>Повторы</dt><dd>{action.retryState}</dd></div>
      <div><dt>Квитанция</dt><dd>{action.receiptId ? `№ ${action.receiptId}: ${action.receiptOutcome}` : "Нет"}</dd></div>
    </dl>
    {action.receiptLate && <p className="provider-action-note">Квитанция получена позднее и сохранена в истории.</p>}
    {action.safeReason && <p className="provider-action-warning" role="status">{reasonLabels[action.safeReason]}</p>}
    {canReconcile && <button type="button" disabled={busy} onClick={() => onReconcile(action)}>
      {busy ? "Ставлю в очередь…" : "Проверить результат"}
    </button>}
    {!canReconcile && action.businessStatus === "requires_reconciliation" && <p className="provider-action-note">
      Проверка уже выполняется либо доступна только для текущей ревизии.
    </p>}
  </article>;
}

export function ProviderActionCenter({ projectId, enabled = true }: Props) {
  const controller = useProviderActions(projectId, enabled);
  return <section className="provider-action-center management-card" aria-label="Контроль внешних действий">
    <header>
      <div>
        <span className="management-eyebrow">КОНТРОЛЬ ВНЕШНИХ ДЕЙСТВИЙ</span>
        <h2>Исполнение и квитанции</h2>
        <p>Показываются только безопасные статусы. Содержимое писем, адреса, токены и ответы провайдера скрыты.</p>
      </div>
      <button type="button" disabled={controller.loadState === "loading"} onClick={() => void controller.reload()}>
        Обновить
      </button>
    </header>
    {controller.loadState === "loading" && <p role="status">Загружаем статусы действий…</p>}
    {controller.loadState === "empty" && <div className="management-empty">
      <strong>Внешних действий по проекту пока нет</strong>
      <p>Они появятся только после явного подтверждения разрешённой операции.</p>
    </div>}
    {controller.loadState === "error" && <div role="alert" className="management-error">
      <p>{controller.error}</p><button type="button" onClick={() => void controller.reload()}>Повторить</button>
    </div>}
    {controller.mutationMessage && <p
      role={controller.mutationState === "saved" ? "status" : "alert"}
      className={controller.mutationState === "saved" ? "mutation-saved" : "mutation-error"}
    >{controller.mutationMessage}</p>}
    <div className="provider-action-list">
      {controller.items.map((action) => <ActionCard
        key={`${action.actionId}:${action.revision}`}
        action={action}
        busy={controller.busyKey === `${action.actionId}:${action.revision}`}
        onReconcile={(item) => void controller.reconcile(item)}
      />)}
    </div>
  </section>;
}
