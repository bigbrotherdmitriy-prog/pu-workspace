import { AlertTriangle, ArrowRight, Bot, CheckCircle2, FileText, Mail, TimerReset } from "lucide-react";
import type { DailyBriefing } from "../ai-secretary/DailyBriefingPanel";
import "./today.css";

export type TodaySummary = {
  overdue_tasks: number;
  overdue_obligations: number;
  open_risks: number;
  pending_decisions: number;
  drafts: number;
  documents: number;
};

type Props = {
  projectName: string;
  briefing: DailyBriefing | null;
  summary: TodaySummary | null;
  inboxAttention: number;
  onOpen: (section: string) => void;
};

const sectionByKind = {
  overdue_task: "Задачи",
  overdue_obligation: "Обязательства",
  risk: "Риски и решения",
  decision: "Риски и решения",
  draft: "Письма",
  context: "AI Secretary",
  missing_contract_source: "Договоры",
  empty_schedule: "Исполнение и финансы",
  unlinked_budget: "Исполнение и финансы",
  unlinked_cash_flow: "Исполнение и финансы",
  payment_confirmation: "Исполнение и финансы",
} as const;

export function TodayModule({ projectName, briefing, summary, inboxAttention, onOpen }: Props) {
  const priorities = briefing?.attention.slice(0, 3) || [];
  const totalAttention = briefing?.summary.attention ?? 0;

  return <section className="today-page">
    <section className="card today-hero">
      <div><span className="today-eyebrow">СЕГОДНЯ · {new Date().toLocaleDateString("ru-RU")}</span><h2>{projectName || "Текущий проект"}</h2><p>{briefing?.next_step || "Собираем актуальный контекст проекта"}</p></div>
      <div className={totalAttention ? "today-score attention" : "today-score clear"}><strong>{totalAttention}</strong><span>{totalAttention ? "требуют внимания" : "всё под контролем"}</span></div>
    </section>

    <section className="today-metrics">
      <button onClick={() => onOpen("Задачи")}><TimerReset /><span>Просрочено задач<strong>{summary?.overdue_tasks || 0}</strong></span></button>
      <button onClick={() => onOpen("Обязательства")}><CheckCircle2 /><span>Обязательств просрочено<strong>{summary?.overdue_obligations || 0}</strong></span></button>
      <button onClick={() => onOpen("Риски и решения")}><AlertTriangle /><span>Риски и решения<strong>{(summary?.open_risks || 0) + (summary?.pending_decisions || 0)}</strong></span></button>
      <button onClick={() => onOpen("Письма")}><Mail /><span>Входящие требуют разбора<strong>{inboxAttention}</strong></span></button>
      <button onClick={() => onOpen("Документы")}><FileText /><span>Документов в проекте<strong>{summary?.documents || 0}</strong></span></button>
    </section>

    <section className="card today-priorities">
      <div className="today-section-head"><div><span className="today-eyebrow">ГЛАВНОЕ СЕЙЧАС</span><h2>Три следующих действия</h2></div><button onClick={() => onOpen("AI Secretary")}><Bot /> Полный брифинг</button></div>
      {priorities.map((item, index) => <article key={`${item.kind}-${item.entity_id}`} className={`priority-${item.priority}`}>
        <span className="today-priority-number">{index + 1}</span>
        <div><small>{item.due_date ? `Срок ${new Date(`${item.due_date}T00:00:00`).toLocaleDateString("ru-RU")}` : item.source_name || "Контроль проекта"}</small><strong>{item.title}</strong><p>{item.next_step}</p>{item.evidence && <em>Основание: {item.evidence}</em>}</div>
        <button onClick={() => onOpen(sectionByKind[item.kind])}>Открыть <ArrowRight /></button>
      </article>)}
      {!priorities.length && <div className="today-empty"><CheckCircle2 /><div><strong>Критических действий нет</strong><p>Проверьте новые письма и документы либо продолжайте работу по плану.</p></div></div>}
    </section>

    <section className="today-quick">
      <button onClick={() => onOpen("Письма")}><Mail /><span><strong>Разобрать входящие</strong><small>Контекст, задачи и черновики ответов</small></span><ArrowRight /></button>
      <button onClick={() => onOpen("AI Secretary")}><Bot /><span><strong>Спросить AI Secretary</strong><small>Ответ только с проверяемыми источниками</small></span><ArrowRight /></button>
      <button onClick={() => onOpen("Запуск проекта")}><CheckCircle2 /><span><strong>Проверить готовность проекта</strong><small>Папка, договор, ГПР, ДДС и контакты</small></span><ArrowRight /></button>
    </section>
  </section>;
}
