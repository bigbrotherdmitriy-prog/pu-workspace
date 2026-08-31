import { useEffect, useState } from "react";
import { Building2, Check, Circle, FileCheck2, FolderPlus, FolderSearch2, Import, Mail, Route, WalletCards } from "lucide-react";
import { api } from "../../api/client";
import "./project-launch.css";
import { useProjectLaunchReadiness } from "./useProjectLaunchReadiness";

type LaunchTarget = "source" | "contacts";
type Props = { projectId: number; openSection: (section: string, target?: LaunchTarget) => void };
type Step = {
  title: string; description: string; section: string; target?: LaunchTarget;
  complete: boolean; status: string; nextAction: string; icon: typeof Circle;
};

export function ProjectLaunchWizard({ projectId, openSection }: Props) {
  const { state, error, reload } = useProjectLaunchReadiness(projectId);
  const [selectedMode, setSelectedMode] = useState<"managed" | "imported" | null>(() => {
    const saved = localStorage.getItem(`pu-project-start-mode:${projectId}`);
    return saved === "managed" || saved === "imported" ? saved : null;
  });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  useEffect(() => {
    const saved = localStorage.getItem(`pu-project-start-mode:${projectId}`);
    setSelectedMode(saved === "managed" || saved === "imported" ? saved : null);
  }, [projectId]);
  if (error) return <section className="card launch-load-error"><h2>Не удалось проверить запуск проекта</h2><p>{error}</p><button onClick={reload}>Повторить</button></section>;
  if (!state) return <section className="card"><p>Проверяем готовность проекта…</p></section>;
  const mode = state.workspaceMode || selectedMode;
  async function createManagedWorkspace() {
    setCreating(true); setCreateError("");
    try {
      await api(`/projects/${projectId}/managed-workspace`, { method: "POST", body: JSON.stringify({ parent_folder_id: "root" }) });
      localStorage.setItem(`pu-project-start-mode:${projectId}`, "managed");
      setSelectedMode("managed"); reload();
    } catch (reason) { setCreateError((reason as Error).message); }
    finally { setCreating(false); }
  }
  if (!mode && !state.sourceReady) return <section className="project-launch-page">
    <div className="launch-hero card"><div><span className="launch-eyebrow">КАК НАЧИНАЕМ ПРОЕКТ</span><h2>{state.projectName || "Новый проект"}</h2><p>Выберите один из двух разных сценариев. Его можно дополнить источниками позже.</p></div></div>
    <div className="launch-mode-grid">
      <article className="card launch-mode-card"><Import /><span className="launch-eyebrow">ПРОЕКТ УЖЕ ВЕДЁТСЯ</span><h2>Разобрать существующую папку</h2><p>Подключим выбранную папку, сохраним исходную иерархию, создадим безопасную копию и проанализируем все доступные файлы.</p><ul><li>Оригиналы не изменяются</li><li>Вложенные папки сохраняются</li><li>Договоры и документы распознаются</li></ul><button onClick={() => { localStorage.setItem(`pu-project-start-mode:${projectId}`, "imported"); setSelectedMode("imported"); openSection("Рабочий центр", "source"); }}>Выбрать существующую папку</button></article>
      <article className="card launch-mode-card managed"><FolderPlus /><span className="launch-eyebrow">ПРОЕКТ НАЧИНАЕТСЯ С НУЛЯ</span><h2>Создать постоянную структуру</h2><p>Создадим постоянную папку проекта и стандартные разделы для договоров, ГПР, финансов, переписки, исполнения и архива.</p><ul><li>Единая структура с первого дня</li><li>Папка остаётся рабочей</li><li>Новые файлы можно анализировать по мере появления</li></ul><button disabled={creating} onClick={createManagedWorkspace}>{creating ? "Создаю структуру…" : "Создать папку проекта"}</button></article>
    </div>{createError && <p className="launch-create-error">{createError}</p>}
  </section>;
  const financeNextAction = !state.scheduleRows
    ? "Создать или импортировать ГПР для договора"
    : !state.budgetRows
      ? "Добавить бюджет и связать его с договором"
      : !state.cashFlowRows
        ? "Добавить плановую запись ДДС"
        : "Проверить финансовую цепочку";
  const steps: Step[] = [
    { title: "Проект и рабочая папка", description: mode === "managed" ? "Постоянная типовая структура создана. Добавляйте в неё документы по мере работы." : "Подключите существующую папку. Система сохранит структуру, создаст безопасную копию и проанализирует файлы.", section: "Рабочий центр", target: "source", complete: state.sourceReady && (mode === "managed" || state.documents > 0), status: state.sourceReady ? (mode === "managed" ? "постоянная структура создана" : `${state.documents} документов обнаружено`) : "папка ещё не проанализирована", nextAction: mode === "managed" ? "Открыть постоянную папку" : "Подключить и проанализировать папку", icon: mode === "managed" ? FolderPlus : FolderSearch2 },
    { title: "Документы", description: "Проверьте состав, результаты анализа и предложения по единому стандарту имён.", section: "Документы", complete: state.analyzedDocuments > 0, status: state.analyzedDocuments ? `${state.analyzedDocuments} документов обработано` : "ожидается анализ", nextAction: "Проверить анализ документов", icon: FileCheck2 },
    { title: "Договор", description: "Создайте карточку договора и привяжите документ-источник из каталога проекта.", section: "Договоры", complete: state.contracts > 0 && state.linkedContracts > 0, status: !state.contracts ? "договор не создан" : state.linkedContracts < state.contracts ? `привязано документов: ${state.linkedContracts} из ${state.contracts}` : `${state.contracts} договоров с источником`, nextAction: !state.contracts ? "Создать карточку договора" : "Привязать документ-источник", icon: Building2 },
    { title: "ГПР, бюджет и ДДС", description: "Свяжите этапы работ, бюджет и движение денег с выбранным договором.", section: "Исполнение и финансы", complete: state.scheduleRows > 0 && state.budgetRows > 0 && state.cashFlowRows > 0, status: `ГПР: ${state.scheduleRows} · бюджет: ${state.budgetRows} · ДДС: ${state.cashFlowRows}`, nextAction: financeNextAction, icon: WalletCards },
    { title: "Компании и контакты", description: "Закрепите email клиента за проектом. Неподтверждённые контакты не маршрутизируют почту автоматически.", section: "Письма", target: "contacts", complete: state.confirmedContacts > 0, status: state.contacts ? `подтверждено: ${state.confirmedContacts} из ${state.contacts}` : "контакты не добавлены", nextAction: state.contacts ? "Подтвердить контакт проекта" : "Добавить контакт клиента", icon: Mail },
  ];
  const completed = steps.filter((step) => step.complete).length;
  const progress = Math.round((completed / steps.length) * 100);
  const next = steps.find((step) => !step.complete);
  return <section className="project-launch-page">
    <div className="launch-hero card"><div><span className="launch-eyebrow">МАСТЕР ЗАПУСКА · {mode === "managed" ? "НОВЫЙ ПРОЕКТ" : "СУЩЕСТВУЮЩИЙ АРХИВ"}</span><h2>{state.projectName || "Выбранный проект"}</h2><p>{mode === "managed" ? "Постоянная структура проекта → договоры → ГПР → ДДС → ежедневная работа." : "Исходная папка → безопасная копия → анализ → договоры → ГПР и ДДС."}</p></div><div className="launch-progress" aria-label={`Готовность проекта ${progress}%`}><strong>{progress}%</strong><span>{completed} из {steps.length} этапов</span></div></div>
    <div className="launch-progress-bar"><span style={{ width: `${progress}%` }} /></div>
    <div className="launch-steps">{steps.map((step, index) => { const Icon = step.icon; return <article className={`launch-step card ${step.complete ? "complete" : "pending"}`} key={step.title}><div className="launch-step-number">{step.complete ? <Check /> : index + 1}</div><div className="launch-step-icon"><Icon /></div><div className="launch-step-copy"><h3>{step.title}</h3><p>{step.description}</p><small>{step.complete ? "Готово: " : "Состояние: "}{step.status}</small>{!step.complete && <strong className="launch-step-action">Дальше: {step.nextAction}</strong>}</div><button onClick={() => openSection(step.section, step.target)}>{step.complete ? "Проверить" : step.nextAction}</button></article> })}</div>
    <section className="launch-next card"><Route /><div><h3>{next ? `Сейчас: ${next.nextAction}` : "Проект готов к ежедневной работе"}</h3><p>{next ? `${next.title}. ${next.description}` : `Подключено входящих писем: ${state.inboxMessages}. Контроль задач и исходящих ответов работает через подтверждения пользователя.`}</p></div>{next && <button onClick={() => openSection(next.section, next.target)}>Продолжить запуск</button>}</section>
  </section>;
}
