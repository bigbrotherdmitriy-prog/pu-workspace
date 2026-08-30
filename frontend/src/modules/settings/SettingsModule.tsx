import { ShieldCheck } from "lucide-react";
import { PasswordChangeCard } from "./PasswordChangeCard";

export type AIProjectPolicy = {
  project_id: number;
  mode: "local_only" | "external_allowed" | "redacted" | "metadata_only";
  dlp_enabled: boolean;
  prompt_version: string;
};

export type ProcessingQueue = {
  summary: { active: number; failed: number; dead_letter: number };
  snapshots: Array<{ id: number; status: string; analysis_status: string; retry_count: number; analysis_retry_count: number; error?: string }>;
  sessions: Array<{ id: number; status: string; progress: number; retry_count: number; error_message?: string }>;
};

type Props = {
  collapsed: boolean;
  currentUser: { name: string; email: string; is_admin: boolean } | null;
  activeProjectName?: string;
  members: Array<{ membership_id: number; name: string; email: string; role: string }>;
  aiPolicy: AIProjectPolicy | null;
  processingQueue: ProcessingQueue | null;
  onPolicyChange: (policy: AIProjectPolicy) => void;
  onSavePolicy: () => void;
  onRetrySnapshot: (id: number) => void;
  onRetrySession: (id: number) => void;
  onPasswordChanged: () => void;
};

export function SettingsModule(props: Props) {
  return <section className={`module-overlay ${props.collapsed ? "collapsed" : ""}`}>
    <div className="module-page settings-grid">
      <section className="card profile-settings">
        <span className="eyebrow">ПРОФИЛЬ</span>
        <div className="settings-profile"><div className="avatar">{props.currentUser?.name?.slice(0, 1) || "D"}</div><div><h2>{props.currentUser?.name || "Пользователь"}</h2><p>{props.currentUser?.email}</p></div></div>
        <div className="setting-row"><span>Роль в системе</span><strong>{props.currentUser?.is_admin ? "Администратор" : "Пользователь"}</strong></div>
        <div className="setting-row"><span>Активный проект</span><strong>{props.activeProjectName}</strong></div>
      </section>
      <section className="card">
        <div className="card-head"><div><h2>Участники проекта</h2><p>Доступ к выбранному проекту</p></div></div>
        <div className="member-list">{props.members.map((member) => <article key={member.membership_id}><div className="avatar">{member.name.slice(0, 1)}</div><div><strong>{member.name}</strong><p>{member.email}</p></div><span>{member.role}</span></article>)}</div>
      </section>
      <PasswordChangeCard onChanged={props.onPasswordChanged} />
      <section className="card span-settings">
        <div className="card-head"><div><h2>AI и защита данных</h2><p>Что разрешено передавать внешней модели для выбранного проекта</p></div></div>
        {props.aiPolicy && <div className="form-grid">
          <label>Режим обработки<select value={props.aiPolicy.mode} onChange={(event) => props.onPolicyChange({ ...props.aiPolicy!, mode: event.target.value as AIProjectPolicy["mode"] })}><option value="local_only">Только локально — внешний AI запрещён</option><option value="redacted">С обезличиванием — рекомендовано</option><option value="metadata_only">Только метаданные</option><option value="external_allowed">Полный текст разрешён</option></select></label>
          <label className="setting-row"><span>Проверять персональные данные перед отправкой</span><input type="checkbox" checked={props.aiPolicy.dlp_enabled} onChange={(event) => props.onPolicyChange({ ...props.aiPolicy!, dlp_enabled: event.target.checked })} /></label>
          <button onClick={props.onSavePolicy}>Сохранить политику</button><p>Версия правил и промпта: {props.aiPolicy.prompt_version}</p>
        </div>}
      </section>
      <section className="card span-settings"><h2>Принципы безопасности</h2><div className="safety-list"><p><ShieldCheck /> Оригиналы файлов никогда не перемещаются и не изменяются.</p><p><ShieldCheck /> Черновики ответов не отправляются без подтверждения.</p><p><ShieldCheck /> Изменения проекта фиксируются в журнале.</p></div></section>
      <section className="card span-settings">
        <div className="card-head"><div><h2>Очередь массовой обработки</h2><p>Прогресс, ошибки и операции, требующие диагностики</p></div></div>
        {props.processingQueue && <div className="safety-list">
          <p>Активно: <strong>{props.processingQueue.summary.active}</strong> · Ошибок: <strong>{props.processingQueue.summary.failed}</strong> · Dead-letter: <strong>{props.processingQueue.summary.dead_letter}</strong></p>
          {props.processingQueue.snapshots.filter((item) => item.status === "failed").map((item) => <p key={`snapshot-${item.id}`}>Снимок №{item.id}: {item.error || "ошибка без описания"}<button onClick={() => props.onRetrySnapshot(item.id)}>Повторить</button></p>)}
          {props.processingQueue.sessions.filter((item) => item.status === "failed").map((item) => <p key={`session-${item.id}`}>Обработка №{item.id}: {item.error_message || "ошибка без описания"}<button onClick={() => props.onRetrySession(item.id)}>Повторить</button></p>)}
        </div>}
      </section>
    </div>
  </section>;
}
