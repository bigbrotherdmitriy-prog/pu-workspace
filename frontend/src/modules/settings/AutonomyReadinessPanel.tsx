import { AlertTriangle, CheckCircle2, Clock3, ShieldCheck } from "lucide-react";

export type AutonomyReadiness = {
  project_id: number;
  observed_at: string;
  overall: { status: "OFF" | "BLOCKED" | "DEGRADED" | "ACTIVE"; blockers: string[]; warnings: string[] };
  runtime: { state: string; pilot_enabled: boolean; producer_enabled: boolean; notification_enabled: boolean; scope_matches: boolean; component_alignment: string };
  policy: { revision: number; hash_prefix: string; enabled: boolean; task_mode: string; notification_mode: string; external_message_mode: string; authority_epoch: number; valid_until: string; ttl_seconds: number; ready: boolean; history: Array<{ revision: number; enabled: boolean }> };
  authority: { state: string; membership_role?: string; authority_epoch?: number; record_version?: number; valid_until?: string; ttl_seconds?: number; ready: boolean };
  mailbox: {
    state: string; ready: boolean; valid?: boolean; producer_ready?: boolean;
    credential_generation?: number; cohort_record_version?: number; cutover?: Record<string, boolean> | null;
    organization_id?: number;
    mail_connection_id?: string | null;
    current_credential_generation?: number | null;
    current_binding_epoch?: number | null;
    actor_authority_version?: number | null;
    authority_valid_until?: string | null;
    /** False (or absent on older servers) while AUTO cannot act on Gmail letters (ADR-V6-07). */
    auto_source_chain_ready?: boolean;
    auto_blocked_reason?: string | null;
  };
  quotas: {
    task_hourly: { used: number; limit: number | null };
    notification_project_hourly: { used: number; limit: number | null };
    notification_recipient_daily: { max_used: number; limit: number | null };
  };
  operations: { actions: Record<string, number>; receipts: Record<string, number>; jobs: Record<string, number>; incidents: Array<{ status: string }> };
};

/** Shown wherever an AUTO-for-mail action is hidden until the V6-07 cutover. */
export const MAIL_AUTO_DEFERRED_NOTE = "AUTO для почты появится после перехода почты на основное чтение (этап V6-07)";

const reasonLabels: Record<string, string> = {
  runtime_configuration_invalid: "Некорректная runtime-конфигурация",
  runtime_project_scope_mismatch: "Runtime настроен на другой проект",
  runtime_owner_scope_mismatch: "Runtime настроен на другого владельца",
  runtime_disabled: "AUTO выключен",
  policy_disabled_or_expired: "Политика выключена или истекла",
  authority_revoked_expired_or_mismatched: "Полномочия отозваны, истекли или не совпадают с политикой",
  mailbox_cutover_not_ready: "Mailbox cohort/cutover не готов",
  mailbox_source_chain_not_ready: "Почта ещё не на основном чтении: AUTO не может действовать по письмам",
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

type Props = {
  data: AutonomyReadiness;
  canBootstrapAuthority?: boolean;
  onBootstrapAuthority?: () => void | Promise<void>;
  bootstrappingAuthority?: boolean;
  canLaunchPilot?: boolean;
  onLaunchPilot?: () => void | Promise<void>;
  launchingPilot?: boolean;
  onEnableAutoPilot?: () => void | Promise<void>;
  enablingAutoPilot?: boolean;
  canExpandAuthority?: boolean;
  onExpandAuthority?: () => void | Promise<void>;
  expandingAuthority?: boolean;
  canRejoinMailbox?: boolean;
  onRejoinMailbox?: () => void | Promise<void>;
  rejoiningMailbox?: boolean;
  canRenewMailboxAuthority?: boolean;
  onRenewMailboxAuthority?: () => void | Promise<void>;
  renewingMailboxAuthority?: boolean;
  now?: Date;
};

export function AutonomyReadinessPanel({
  data, canBootstrapAuthority, onBootstrapAuthority, bootstrappingAuthority,
  canLaunchPilot, onLaunchPilot, launchingPilot, onEnableAutoPilot, enablingAutoPilot,
  canExpandAuthority, onExpandAuthority, expandingAuthority,
  canRejoinMailbox, onRejoinMailbox, rejoiningMailbox,
  canRenewMailboxAuthority, onRenewMailboxAuthority, renewingMailboxAuthority, now,
}: Props) {
  // Mail access expires (owner decision 13.10.2026: renew up to 7 days at a
  // time). Without a live grant every new letter is rejected, so the expiry is
  // shown always and the renew button is offered to the owner at any time.
  const mailAccessUntil = data.mailbox.authority_valid_until ? new Date(data.mailbox.authority_valid_until) : null;
  const mailAccessLeftMs = mailAccessUntil ? mailAccessUntil.getTime() - (now ?? new Date()).getTime() : null;
  const mailAccessState = mailAccessLeftMs == null ? null
    : mailAccessLeftMs <= 0 ? "expired" : mailAccessLeftMs <= 24 * 3600 * 1000 ? "expiring" : "ok";
  const canOfferRenewMailboxAuthority = Boolean(
    canRenewMailboxAuthority && onRenewMailboxAuthority
    && data.mailbox.mail_connection_id != null
    && data.mailbox.current_credential_generation != null
    && data.mailbox.current_binding_epoch != null
    && data.mailbox.actor_authority_version != null,
  );
  const reasons = [...data.overall.blockers, ...data.overall.warnings];
  // Offered only when the project already has SOME cohort history on this
  // mailbox but it isn't on the account's current credential generation --
  // "stale" (one enabled row, out of date) or "ambiguous" (more than one
  // enabled row, the pre-existing bug shape this button exists to fix).
  // Never for "missing": a project that was never part of this mailbox at
  // all gets no join offer here -- that's a separate, out-of-scope flow.
  const canOfferRejoinMailbox = Boolean(
    canRejoinMailbox && onRejoinMailbox
    && (data.mailbox.state === "stale" || data.mailbox.state === "ambiguous")
    && data.mailbox.mail_connection_id != null
    && data.mailbox.current_credential_generation != null
    && data.mailbox.current_binding_epoch != null
    && data.mailbox.actor_authority_version != null,
  );
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
    {canBootstrapAuthority && onBootstrapAuthority && <div className="autonomy-bootstrap">
      <p>Единственное исключение: если мандат пилота истёк и его некому продлить (замкнутый круг — продление само требует действующего мандата), администратор может восстановить его.</p>
      <button type="button" className="secondary" disabled={bootstrappingAuthority} onClick={() => void onBootstrapAuthority()}>
        {bootstrappingAuthority ? "Восстанавливаем…" : "Восстановить мандат пилота"}
      </button>
      {canLaunchPilot && onLaunchPilot && <>
        <p>Отдельно — реальный запуск: отправка внешних писем всегда требует вашего подтверждения (это нельзя переключить на авто), независимо от выбранного ниже режима.</p>
        <button type="button" disabled={launchingPilot} onClick={() => void onLaunchPilot()}>
          {launchingPilot ? "Запускаем…" : "Запустить пилот (всё через подтверждение)"}
        </button>
        {onEnableAutoPilot && !data.mailbox.auto_source_chain_ready && <p className="autonomy-readonly-note autonomy-auto-deferred">{MAIL_AUTO_DEFERRED_NOTE}</p>}
        {onEnableAutoPilot && data.mailbox.auto_source_chain_ready && <>
          <p>AUTO-пилот: система сама создаёт внутренние задачи и уведомления по входящим письмам (лимиты в час, уверенность от 0.9). Внешние письма — всё равно только по подтверждению. Сначала мандат пилота должен быть действующим.</p>
          <button type="button" disabled={enablingAutoPilot} onClick={() => void onEnableAutoPilot()}>
            {enablingAutoPilot ? "Включаем…" : "Включить AUTO для задач и уведомлений на 7 дней"}
          </button>
        </>}
      </>}
      {canExpandAuthority && onExpandAuthority && <>
        <p>Если панель выше показывает «Полномочия... не совпадают с политикой» при живом и неистёкшем мандате — это значит, что мандат выдан не на полный список операций пилота. Можно расширить его без пересоздания.</p>
        <button type="button" className="secondary" disabled={expandingAuthority} onClick={() => void onExpandAuthority()}>
          {expandingAuthority ? "Расширяем…" : "Выдать полный набор полномочий"}
        </button>
      </>}
    </div>}
    {mailAccessUntil && <div className="autonomy-bootstrap" data-mail-access={mailAccessState ?? undefined}>
      <p>
        {mailAccessState === "expired" && <><AlertTriangle /> Доступ системы к почте истёк {mailAccessUntil.toLocaleString("ru-RU")}. Новые письма не загружаются, пока доступ не продлён.</>}
        {mailAccessState === "expiring" && <><AlertTriangle /> Доступ системы к почте истекает {mailAccessUntil.toLocaleString("ru-RU")}. Продлите его, чтобы почта не остановилась.</>}
        {mailAccessState === "ok" && <><CheckCircle2 /> Доступ системы к почте действует до {mailAccessUntil.toLocaleString("ru-RU")}.</>}
      </p>
      {canOfferRenewMailboxAuthority && <button type="button" className={mailAccessState === "ok" ? "secondary" : undefined}
        disabled={renewingMailboxAuthority} onClick={() => void onRenewMailboxAuthority!()}>
        {renewingMailboxAuthority ? "Продлеваем…" : "Продлить доступ к почте на 7 дней"}
      </button>}
    </div>}
    {canOfferRejoinMailbox && <div className="autonomy-bootstrap">
      <p>Mailbox этого проекта подключён к устаревшему поколению учётных данных почты (аккаунт уже переподключал Gmail из другого проекта). Переподключение отключит для этого проекта устаревшее поколение и подключит его к текущему — без этого шага AUTO-обработка писем для проекта не возобновится.</p>
      <button type="button" className="secondary" disabled={rejoiningMailbox} onClick={() => void onRejoinMailbox!()}>
        {rejoiningMailbox ? "Переподключаем…" : "Переподключить mailbox к текущему поколению"}
      </button>
    </div>}
  </section>;
}
