export const ERROR_CODE_LABELS: Record<string, string> = {
  ARTICLE_AMBIGUOUS: "Статья определена неоднозначно",
  ARTICLE_BUDGET_PREVIEW_REQUIRED: "Сначала обновите предпросмотр бюджета",
  ARTICLE_NAME_TOO_LONG: "Слишком длинное название статьи",
  BUDGET_ALLOCATION_EXCEEDS_LINE: "Распределение превышает сумму строки бюджета",
  BUDGET_LINE_REQUIRED: "Укажите строку бюджета",
  BUDGET_REVISION_REQUIRED: "Укажите ревизию бюджета",
  BUDGET_SCOPE_UNKNOWN: "Границы бюджета не определены",
  BUDGET_SOURCE_STALE: "Данные бюджета устарели — обновите их",
  BUDGET_STATUS_UNSUPPORTED: "Недопустимый статус бюджета",
  CASH_FLOW_NOT_FOUND: "Строка ДДС не найдена",
  CASH_FLOW_VERSION_MISMATCH: "Строка ДДС изменилась — обновите данные",
  CASH_FLOW_BATCH_OUTCOME_UNKNOWN: "Результат массовой операции по ДДС неизвестен — повторите с тем же ключом",
  CASH_FLOW_BATCH_REJECTED: "Массовая операция по ДДС отклонена",
  CASH_FLOW_BATCH_WRITE_FAILED: "Не удалось сохранить массовую операцию по ДДС",
  CATEGORY_CONFIRMATION_REQUIRED: "Подтвердите категорию затрат для статьи",
  CATEGORY_UNAVAILABLE: "Категория недоступна",
  CONCURRENT_IMPORT_CONFLICT: "Одновременно выполняется импорт — повторите позже",
  CONFIRMED: "Подтверждено",
  CONTRACT_AMOUNT_UNSPECIFIED: "Не указана сумма договора",
  CONTRACT_REQUIRED: "Укажите договор",
  CONTRACT_SCOPE_MISMATCH: "Договор не соответствует области операции",
  CONTRACT_VAT_UNSPECIFIED: "Не указан НДС по договору",
  CONTRACT_VERSION_MISMATCH: "Договор изменился — обновите данные",
  CONTROL_CHAIN_REQUIRED: "Требуется контрольная цепочка",
  EXISTING_FORECAST_ROWS: "Уже есть строки прогноза",
  FORECAST_CONTROL_REQUIRED: "Требуется контроль прогноза",
  FORECAST_CONVERSION_REQUIRED: "Требуется преобразование прогноза",
  IDEMPOTENCY_CONFLICT: "Конфликт повторной отправки — повторите с новым ключом",
  INVOICE_COPY_FORBIDDEN: "Копирование счёта запрещено",
  MATRIX_INVALID: "Матрица содержит ошибки",
  MATRIX_PROVENANCE_REQUIRED: "Не указан источник данных матрицы",
  MATRIX_REQUIRED: "Требуется матрица",
  NO_EXPENSE_ARTICLES: "Нет статей расходов",
  NOT_APPLIED: "Не применено",
  PAYMENT_EVENT_MISMATCH: "Платёжное событие не совпадает",
  PLAN_HAS_ACTUAL: "По плану уже есть фактические данные",
  PREVIEW_STALE: "Предпросмотр устарел — обновите его",
  PREVIEW_CONFLICTS: "В предпросмотре есть конфликты",
  RESULT_UNKNOWN: "Результат операции неизвестен",
  SCHEDULE_BUDGET_LINK_CONTRACT_MISMATCH: "Связь графика и бюджета не соответствует договору",
  SOURCE_VERSION_MISMATCH: "Документ изменился после предпросмотра — обновите предпросмотр",
  STATUS_NOT_PROPOSED: "Статус не «предложено»",
  TARGET_BUDGET_LINE_MISSING: "Не найдена целевая строка бюджета",
  UNDO_DEPENDENCY_CONFLICT: "Отмена невозможна: есть зависимые изменения",
  UNKNOWN_ARTICLE: "Неизвестная статья",
  UNKNOWN_BUDGET_TYPE: "Неизвестный тип бюджета",
  UNKNOWN_CASH_TYPE: "Неизвестный тип ДДС",
  VAT_REFRESH_STATE_MISMATCH: "Состояние пересчёта НДС изменилось — обновите данные",
  VAT_SNAPSHOT_STALE: "Снимок НДС устарел — обновите данные",
};

export function humanizeErrorText(text: string): string {
  if (typeof text !== "string" || !text) return text;
  const match = /^([A-Z][A-Z0-9_]{4,})(?::\s*(.*))?$/s.exec(text.trim());
  if (!match) return text;
  const [, code, remainder] = match;
  const label = ERROR_CODE_LABELS[code] ?? "Операция не выполнена";
  if (remainder && /[А-Яа-яЁё]/.test(remainder)) return `${label}: ${remainder}`;
  return label;
}

export const STATUS_LABELS: Record<string, string> = {
  proposed: "Предложено",
  approved: "Одобрено",
  rejected: "Отклонено",
  applied: "Применено",
  pending: "В ожидании",
  waiting_confirmation: "Ждёт подтверждения",
  needs_review: "Нужна проверка",
  confirmed: "Подтверждено",
  draft: "Черновик",
  active: "Активно",
  completed: "Выполнено",
  done: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
  canceled: "Отменено",
  queued: "В очереди",
  running: "Выполняется",
  succeeded: "Успешно",
  ready_to_apply_to_copy: "Готово к применению на копии",
  analyzed: "Проанализирован",
  discovered: "Найден",
  open: "Открыто",
  closed: "Закрыто",
  sent: "Отправлено",
  read: "Прочитано",
  unread: "Не прочитано",
  superseded: "Заменено",
  expired: "Истекло",
  blocked: "Заблокировано",
  archived: "В архиве",
};

export function humanizeStatus(value?: string | null): string {
  if (!value) return "";
  return STATUS_LABELS[value] ?? value.replace(/_/g, " ");
}

export const ENTITY_LABELS: Record<string, string> = {
  risk: "Риск",
  decision: "Решение",
  task: "Задача",
  obligation: "Обязательство",
  contract: "Договор",
  document: "Документ",
  invoice_extraction_proposal: "Счёт",
  message: "Письмо",
  meeting: "Встреча",
  proposal: "Предложение",
  cash_flow_entry: "Строка ДДС",
  budget_line: "Строка бюджета",
};

export function humanizeEntity(value?: string | null): string {
  if (!value) return "";
  return ENTITY_LABELS[value] ?? value.replace(/_/g, " ");
}
