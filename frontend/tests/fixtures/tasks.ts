import type { TaskRow, TaskHistoryRow } from "../../src/modules/tasks/TasksModule";

export const longPath = `Проекты/Генподряд/Исполнительная_документация/${"ОченьДлинноеИмяФайлаБезПробелов".repeat(8)}.pdf`;
export const task: TaskRow = {
  id: 1, title: "Согласовать исправленный комплект исполнительной документации по этапу строительства и передать заказчику подтверждение устранения замечаний",
  status: "assigned", priority: "high", due_date: "2000-01-01", assignee_user_id: 1,
  assignee_name: "Александра Константиновна ОтветственнаяЗаИсполнительнуюДокументацию",
  source_file_name: longPath, source_excerpt: "Подготовить документы и проверить замечания. " + longPath,
  confidence: 0.42, needs_review: true, external_action_status: "proposed",
  description: "Требуется ручная проверка. Причины: в синтетическом фрагменте есть символы замены; сверьте текст с оригиналом. " + longPath,
};
export const members = [
  { user_id: 1, name: task.assignee_name, role: "Руководитель проектного подразделения" },
  { user_id: 2, name: "Синтетический исполнитель", role: "Инженер" },
];
export const documents = [{ id: 7, name: longPath }];
export const history: TaskHistoryRow[] = [{
  action: "updated", old_status: "assigned", new_status: "in_progress",
  changed_at: "2026-09-03T10:00:00Z", changed_by: task.assignee_name,
  result_note: "Результат проверки: " + longPath,
  completion_document_name: longPath, details: "Синтетическая история без реальных данных",
}];
