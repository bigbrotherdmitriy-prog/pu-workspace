import { AlertTriangle, CheckCircle2, Clock3, ShieldCheck } from "lucide-react";

export type AutonomyReadiness = {
  project_id: number;
  observed_at: string;
  overall: { status: "OFF" | "BLOCKED" | "DEGRADED" | "ACTIVE"; blockers: string[]; warnings: string[] };
  runtime: { state: string; pilot_enabled: boolean; producer_enabled: boolean; notification_enabled: boolean; scope_matches: boolean; component_alignment: string };
  policy: { revision: number; hash_prefix: string; enabled: boolean; task_mode: string; notification_mode: string; external_message_mode: string; authority_epoch: number; valid_until: string; ttl_seconds: number; ready: boolean; history: Array<{ revision: number; enabled: boolean }> };
  authority: { state: string; membership_role?: string; authority_epoch?: number; record_version?: number; valid_until?: string; ttl_seconds?: number; ready: boolean };
  mailbox: { state: string; ready: boolean; valid?: boolean; producer_ready?: boolean; credential_generation?: number; cohort_record_version?: number; cutover?: Record<string, boolean> | null };
  quotas: {
    task_hourly: { used: number; limit: number | null };
    notification_project_hourly: { used: number; limit: number | null };
    notification_recipient_daily: { max_used: number; limit: number | null };
  };
  operations: { actions: Record<string, number>; receipts: Record<string, number>; jobs: Record<string, number>; incidents: Array<{ status: string }> };
};

const reasonLabels: Record<string, string> = {
  runtime_configuration_invalid: "Некорректная runtime-конфигурация",
  runtime_project_scope_mismatch: "Runtime настроен на другой проект",
  runtime_owner_scope_mismatch: "Runtime настроен на другого владельца",
  runtime_disabled: "AUTO выключен",
  policy_disabled_or_expired: "Политика выключена или истекла",
  authority_revoked_expired_or_mismatched: "Полномочия отозваны, истекли или не совпадают с политикой",
  mailbox_cutover_not_ready: "Mailbox cohort/cutover не готов",
  unresolved_incident: "Есть неразрешённый инцидент",
  runtime_component_alignment_unverified: "Версии runtime-компонентов нельзя подтвердить по текущим heartbeat-данным",
};

function ttl(seconds?: number) {
  if (!seconds) return "истёк";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours} ч ${minutes} мин`;
}

function quota(label: string, used: number, limit: number | null) {
  return <div className="setting-row"><span>{label}</span><strong>{used} / {limit ?? "—"}</strong></div>;
}

export function AutonomyReadinessPanel({ data }: { data: AutonomyReadiness }) {
  const reasons = [...data.overall.blockers, ...data.overall.warnings];
  return <section className="card span-settings autonomy-readiness" aria-label="Готовность AUTO">
    <div className="card-head"><div><h2>Готовность AUTO</h2><p>Только чтение · проект №{data.project_id} · проверено {new Date(data.observed_at).toLocaleString("ru-RU")}</p></div>
      <span className={`autonomy-state autonomy-state-${data.overall.status.toLowerCase()}`}>{data.overall.status}</span>
    </div>
    <div className="autonomy-readiness-grid">
      <div><h3><ShieldCheck /> Политика и полномочия</h3>
        <div className="setting-row"><span>Policy revision</span><strong>r{data.policy.revision} · {data.policy.hash_prefix}</strong></div>
        <div className="setting-row"><span>Policy TTL</span><strong>{ttl(data.policy.ttl_seconds)}</strong></div>
        <div className="setting-row"><span>История policy</span><strong>{data.policy.history.slice(0, 4).map((item) => `r${item.revision} ${item.enabled ? "ON" : "OFF"}`).join(" · ")}</strong></div>
        <div className="setting-row"><span>Authority epoch</span><strong>{data.authority.authority_epoch ?? "—"}</strong></div>
        <div className="setting-row"><span>Authority TTL</span><strong>{ttl(data.authority.ttl_seconds)}</strong></div>
      </div>
      <div><h3><Clock3 /> Runtime и mailbox</h3>
        <div className="setting-row"><span>Task / Notification</span><strong>{data.policy.task_mode} / {data.policy.notification_mode}</strong></div>
        <div className="setting-row"><span>Внешние сообщения</span><strong>{data.policy.external_message_mode}</strong></div>
        <div className="setting-row"><span>Producer / notification</span><strong>{data.runtime.producer_enabled ? "ON" : "OFF"} / {data.runtime.notification_enabled ? "ON" : "OFF"}</strong></div>
        <div className="setting-row"><span>Mailbox</span><strong>{data.mailbox.state} · generation {data.mailbox.credential_generation ?? "—"}</strong></div>
      </div>
      <div><h3>Квоты</h3>
        {quota("Задачи / час", data.quotas.task_hourly.used, data.quotas.task_hourly.limit)}
        {quota("Уведомления проекта / час", data.quotas.notification_project_hourly.used, data.quotas.notification_project_hourly.limit)}
        {quota("Максимум на получателя / день", data.quotas.notification_recipient_daily.max_used, data.quotas.notification_recipient_daily.limit)}
      </div>
      <div><h3>Исполнение</h3>
        <div className="setting-row"><span>UNKNOWN receipts/actions</span><strong>{(data.operations.receipts.UNKNOWN || 0) + (data.operations.actions.UNKNOWN || 0)}</strong></div>
        <div className="setting-row"><span>Dead-letter jobs</span><strong>{data.operations.jobs.dead_letter || 0}</strong></div>
        <div className="setting-row"><span>Инциденты</span><strong>{data.operations.incidents.length}</strong></div>
      </div>
    </div>
    <div className="safety-list autonomy-reasons">
      {reasons.length ? reasons.map((reason) => <p key={reason}><AlertTriangle /> {reasonLabels[reason] || reason}</p>) : <p><CheckCircle2 /> Все наблюдаемые проверки пройдены.</p>}
    </div>
    <p className="autonomy-readonly-note">Панель не изменяет policy, authority, mailbox и не запускает внешние действия.</p>
  </section>;
}
