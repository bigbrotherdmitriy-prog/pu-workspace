import { ListTodo } from "lucide-react";

export type TaskRow = {
  id: number; title: string; status: string; priority: string; due_date?: string;
  assignee_user_id: number; assignee_name: string; source_file_name: string;
  source_excerpt: string; confidence: number; needs_review: boolean; message_id?: number;
  external_action_status: string; google_task_id?: string; google_calendar_event_id?: string;
  result_note?: string; completion_document_id?: number; completion_document_name?: string;
};

export type TaskHistoryRow = {
  action: string; old_status?: string; new_status?: string; result_note?: string;
  completion_document_name?: string; details?: string; changed_by: string; changed_at: string;
};

type Props = {
  tasks: TaskRow[];
  filter: string;
  members: Array<{ user_id: number; name: string; role: string }>;
  documents: Array<{ id: number; name: string }>;
  completionTaskId: number;
  completionNote: string;
  completionDocumentId: number;
  historyTaskId: number;
  history: TaskHistoryRow[];
  onFilterChange: (filter: string) => void;
  onAssign: (task: TaskRow, userId: number) => void;
  onApproveExternal: (task: TaskRow) => void;
  onUpdate: (task: TaskRow, status: string) => void;
  onStartCompletion: (task: TaskRow) => void;
  onCancelCompletion: () => void;
  onCompletionNoteChange: (value: string) => void;
  onCompletionDocumentChange: (id: number) => void;
  onLoadHistory: (task: TaskRow) => void;
};

export function TasksModule(props: Props) {
  const today = new Date().toISOString().slice(0, 10);
  const visibleTasks = props.tasks.filter((task) =>
    props.filter === "all" ? true
      : props.filter === "overdue" ? Boolean(task.due_date && task.due_date < today && task.status !== "completed")
        : props.filter === "review" ? task.needs_review
          : task.status === "assigned" || task.status === "in_progress",
  );

  return <section className="card task-register">
    <div className="card-head"><div><h2>Реестр задач</h2><p>Автоматически выделенные поручения с проверяемым источником</p></div><div className="task-filters">{[["open", "Открытые"], ["overdue", "Просроченные"], ["review", "На проверку"], ["all", "Все"]].map(([id, label]) => <button className={props.filter === id ? "selected" : ""} onClick={() => props.onFilterChange(id)} key={id}>{label}</button>)}</div></div>
    <div className="task-list">{visibleTasks.map((task) => <article key={task.id}>
      <div className={`task-priority ${task.priority}`} />
      <div className="task-body"><strong>{task.title}</strong><p>{task.source_file_name} · {task.assignee_name} · уверенность {Math.round(task.confidence * 100)}%</p><small>{task.source_excerpt}</small></div>
      <div className="task-meta"><span className={task.due_date && task.due_date < today && task.status !== "completed" ? "overdue" : ""}>{task.due_date || "Без срока"}</span><span>{task.google_task_id ? "Google Tasks ✓" : task.external_action_status === "proposed" ? "Предложение" : "Локальная"}{task.google_calendar_event_id ? " · Calendar ✓" : ""}</span></div>
      <div className="task-actions">
        <label className="task-assignee"><span>Исполнитель</span><select aria-label={`Исполнитель задачи ${task.title}`} value={task.assignee_user_id} onChange={(event) => props.onAssign(task, Number(event.target.value))}>{props.members.map((member) => <option value={member.user_id} key={member.user_id}>{member.name} · {member.role}</option>)}</select></label>
        {task.external_action_status !== "executed" && <button onClick={() => props.onApproveExternal(task)}>Поставить задачу</button>}
        {task.status === "assigned" && <button onClick={() => props.onUpdate(task, "in_progress")}>В работу</button>}
        {task.status !== "completed" && <button className="complete" onClick={() => props.onStartCompletion(task)}>Завершить</button>}
        <button className="secondary" onClick={() => props.onLoadHistory(task)}>История</button>
      </div>
      {props.completionTaskId === task.id && <div className="task-completion"><strong>Подтверждение выполнения</strong><textarea value={props.completionNote} onChange={(event) => props.onCompletionNoteChange(event.target.value)} placeholder="Что выполнено и какой результат получен *" /><select value={props.completionDocumentId} onChange={(event) => props.onCompletionDocumentChange(Number(event.target.value))}><option value={0}>Без вложения — это допустимо</option>{props.documents.map((document) => <option value={document.id} key={document.id}>{document.name}</option>)}</select><small>Необязательно: выберите акт, письмо, счёт, фото или другой документ проекта.</small><div className="task-completion-actions"><button className="secondary" onClick={props.onCancelCompletion}>Отмена</button><button className="complete" disabled={!props.completionNote.trim()} onClick={() => props.onUpdate(task, "completed")}>Подтвердить завершение</button></div></div>}
      {props.historyTaskId === task.id && <div className="task-history"><strong>История задачи и решений</strong>{props.history.map((item, index) => <div className="task-history-row" key={`${item.changed_at}-${index}`}><time>{new Date(item.changed_at).toLocaleString("ru-RU")}</time><span>{item.changed_by}</span><b>{item.action === "created" ? "Создана" : item.action === "completed" ? "Завершена" : "Изменена"}</b>{item.old_status !== item.new_status && <small>{item.old_status || "—"} → {item.new_status || "—"}</small>}{item.result_note && <p>{item.result_note}</p>}{item.completion_document_name && <p>Подтверждение: {item.completion_document_name}</p>}{item.details && <p>{item.details}</p>}</div>)}</div>}
    </article>)}{!visibleTasks.length && <div className="empty"><ListTodo /><p>Задач в этом фильтре нет</p></div>}</div>
  </section>;
}
