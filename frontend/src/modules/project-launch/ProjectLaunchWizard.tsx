import { Building2, Check, Circle, FileCheck2, FolderSearch2, Mail, Route, WalletCards } from "lucide-react";
import "./project-launch.css";
import { useProjectLaunchReadiness } from "./useProjectLaunchReadiness";

type LaunchTarget = "contacts";
type Props = { projectId: number; openSection: (section: string, target?: LaunchTarget) => void };
type Step = {
  title: string; description: string; section: string; target?: LaunchTarget;
  complete: boolean; status: string; nextAction: string; icon: typeof Circle;
};

export function ProjectLaunchWizard({ projectId, openSection }: Props) {
  const { state, error, reload } = useProjectLaunchReadiness(projectId);
  if (error) return <section className="card launch-load-error"><h2>Не удалось проверить запуск проекта</h2><p>{error}</p><button onClick={reload}>Повторить</button></section>;
  if (!state) return <section className="card"><p>Проверяем готовность проекта…</p></section>;
  const financeNextAction = !state.scheduleRows
    ? "Создать или импортировать ГПР для договора"
    : !state.budgetRows
      ? "Добавить бюджет и связать его с договором"
      : !state.cashFlowRows
        ? "Добавить плановую запись ДДС"
        : "Проверить финансовую цепочку";
  const steps: Step[] = [
    { title: "Проект и рабочая папка", description: "Подключите папку проекта. Система создаст безопасную копию и поставит документы на анализ.", section: "Рабочий центр", complete: state.sourceReady && state.documents > 0, status: state.sourceReady ? `${state.documents} документов обнаружено` : "папка ещё не проанализирована", nextAction: "Подключить и проанализировать папку", icon: FolderSearch2 },
    { title: "Документы", description: "Проверьте состав, результаты анализа и предложения по единому стандарту имён.", section: "Документы", complete: state.analyzedDocuments > 0, status: state.analyzedDocuments ? `${state.analyzedDocuments} документов обработано` : "ожидается анализ", nextAction: "Проверить анализ документов", icon: FileCheck2 },
    { title: "Договор", description: "Создайте карточку договора и привяжите документ-источник из каталога проекта.", section: "Договоры", complete: state.contracts > 0 && state.linkedContracts > 0, status: !state.contracts ? "договор не создан" : state.linkedContracts < state.contracts ? `привязано документов: ${state.linkedContracts} из ${state.contracts}` : `${state.contracts} договоров с источником`, nextAction: !state.contracts ? "Создать карточку договора" : "Привязать документ-источник", icon: Building2 },
    { title: "ГПР, бюджет и ДДС", description: "Свяжите этапы работ, бюджет и движение денег с выбранным договором.", section: "Исполнение и финансы", complete: state.scheduleRows > 0 && state.budgetRows > 0 && state.cashFlowRows > 0, status: `ГПР: ${state.scheduleRows} · бюджет: ${state.budgetRows} · ДДС: ${state.cashFlowRows}`, nextAction: financeNextAction, icon: WalletCards },
    { title: "Компании и контакты", description: "Закрепите email клиента за проектом. Неподтверждённые контакты не маршрутизируют почту автоматически.", section: "Письма", target: "contacts", complete: state.confirmedContacts > 0, status: state.contacts ? `подтверждено: ${state.confirmedContacts} из ${state.contacts}` : "контакты не добавлены", nextAction: state.contacts ? "Подтвердить контакт проекта" : "Добавить контакт клиента", icon: Mail },
  ];
  const completed = steps.filter((step) => step.complete).length;
  const progress = Math.round((completed / steps.length) * 100);
  const next = steps.find((step) => !step.complete);
  return <section className="project-launch-page">
    <div className="launch-hero card"><div><span className="launch-eyebrow">МАСТЕР ЗАПУСКА ПРОЕКТА</span><h2>{state.projectName || "Выбранный проект"}</h2><p>Одна последовательность от исходной папки до управляемого договора, ГПР, ДДС и переписки.</p></div><div className="launch-progress" aria-label={`Готовность проекта ${progress}%`}><strong>{progress}%</strong><span>{completed} из {steps.length} этапов</span></div></div>
    <div className="launch-progress-bar"><span style={{ width: `${progress}%` }} /></div>
    <div className="launch-steps">{steps.map((step, index) => { const Icon = step.icon; return <article className={`launch-step card ${step.complete ? "complete" : "pending"}`} key={step.title}><div className="launch-step-number">{step.complete ? <Check /> : index + 1}</div><div className="launch-step-icon"><Icon /></div><div className="launch-step-copy"><h3>{step.title}</h3><p>{step.description}</p><small>{step.complete ? "Готово: " : "Состояние: "}{step.status}</small>{!step.complete && <strong className="launch-step-action">Дальше: {step.nextAction}</strong>}</div><button onClick={() => openSection(step.section, step.target)}>{step.complete ? "Проверить" : step.nextAction}</button></article> })}</div>
    <section className="launch-next card"><Route /><div><h3>{next ? `Сейчас: ${next.nextAction}` : "Проект готов к ежедневной работе"}</h3><p>{next ? `${next.title}. ${next.description}` : `Подключено входящих писем: ${state.inboxMessages}. Контроль задач и исходящих ответов работает через подтверждения пользователя.`}</p></div>{next && <button onClick={() => openSection(next.section, next.target)}>Продолжить запуск</button>}</section>
  </section>;
}
