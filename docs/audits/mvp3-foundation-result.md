# MVP3 management foundation — result

Дата: 2026-09-05

Ветка: `codex/mvp3-foundation`

База: `a19fffde54e51aee0b42220c83f6c19b1d3b9055`

## Аудит до изменений

| Контур | Reuse | Gap до среза |
|---|---|---|
| Obligation | `Obligation`, Project/Contract/Task links | Нет CAS, истории, точного Evidence pin, time/timezone и явного review state |
| Task | `Task`, `TaskHistory`, `ExternalResourceLink`, provider-neutral action adapter | Не было канонического state mapping и идемпотентной связи с обязательством |
| Deadline | `due_date`, notifications, `DeadlineClaim` | Obligation теряло точное время/timezone; quiet hours и reminder policy не были частью записи |
| Risk/Decision | Существующие реестры и extraction | Нет CAS/history, Evidence pins и связей с Obligation/Task/Risk |
| «Требует внимания» | Dashboard и daily briefing | Нет стабильной серверной пагинации и объяснимого evidence-backed read model |
| Безопасность | ProjectMember ACL, v5.4 Source/Evidence | Legacy mutations не имели строгого fail-closed evidence/current-version gate |

Второй graph, ledger и queue не создавались. BackgroundJob, Authority, Trust,
Drive/Gmail и финансовая логика не изменялись.

## Реализованный вертикальный срез

- `Obligation`: `record_version`, CAS transitions, ORM-protected append-only history, exact
  Evidence pins, owner/project/contract/source/task links, review state,
  date/time/timezone и versioned deadline policy.
- Низкая или неизвестная confidence всегда создаёт `needs_review`; подтвердить
  такую запись может только manager/owner.
- Exact evidence проверяется по tenant/project, SourceReference,
  SourceVersion, SourceCurrent и EvidenceAssessment. Unknown, stale,
  unavailable, cross-tenant и cross-project состояния закрываются отказом.
- Идемпотентное создание внутренней Task из подтверждённого обязательства.
  Внешнее действие не создаётся: `external_action_status=proposed`, provider IDs
  отсутствуют.
- Явный state mapping: `assigned→OPEN`, `in_progress→IN_PROGRESS`,
  `completed→COMPLETED`, `cancelled→CANCELLED`.
- Risk и Decision получили CAS/history, evidence pins, review state и связи с
  Obligation/Task; Decision также может ссылаться на Risk.
- Новый read-only `/management/v2/attention`: stable sorting, offset/limit,
  фильтр по видам, объяснение причины и exact evidence pins.
- Истории доступны отдельными read-only endpoints. Legacy API сохранён для
  обратной совместимости.

## Покрытие критериев ТЗ

| ID | Статус после среза | Что доказано | Что осталось |
|---|---|---|---|
| M3-01 | FOUNDATION PASS | Evidence, owner, due, status, CAS/history и Project/Contract/Source/Task links | Перевести legacy extraction на v2 contract; PostgreSQL concurrency; UI/E2E |
| M3-02 | FOUNDATION PASS | Internal state mapping, idempotent obligation→Task, manual history, отсутствие auto external effect | Общий inbound/outbound correction workflow и live provider acceptance |
| M3-03 | PARTIAL | time/timezone, reminder offsets, quiet-hours policy и overdue read model | Scheduler/digest execution, user preferences, escalation workflow |
| M3-04 | FOUNDATION PASS | Evidence-backed Risk/Decision, severity/owner/mitigation/state/history/relations | Extraction integration, browser UI, PostgreSQL concurrent CAS |
| M3-05 | NOT CHANGED | Legacy meeting extraction сохранён | Перевести на evidence-backed proposed workflow и human confirmation |
| M3-06 | PARTIAL | Paginated/filterable explainable attention read model | Approvals/conflicts projection и независимый frontend module |
| M3-07..M3-11 | NOT CLOSED | — | Следующие последовательные срезы и итоговая acceptance |

## Схема и handoff интегратору

Новая последовательная миграция: `a54f001c0a10`, down-revision
`a54f001c0a09`. В этой ветке одна head. При интеграции параллельных потоков
нельзя cherry-pick выбирать целые schema/runtime файлы: интегратор должен
сериализовать эту миграцию после фактической head и обновить
`CURRENT_SCHEMA_REVISION`, docker smoke, durable harness и v5.4 runtime pin.

Новые таблицы:

- `obligation_history`;
- `governance_history`.

Расширены `obligations`, `risks`, `decisions`. Existing rows получают
совместимые server defaults; отсутствие `evidence_pins` у legacy-записи не
считается доказанным происхождением.

## Проверки

- Foundation lifecycle and migration: `18 passed`.
- Foundation/schema/v5.4 targeted: `108 passed, 1 skipped`.
- Existing MVP3 regression: `12 passed`.
- Первый full backend: `931 passed, 15 skipped`, 227 setup errors из-за
  `PermissionError` системного `%TEMP%`; это не product failure.
- Повтор с изолированным `--basetemp`: сначала `1154 passed, 19 skipped` и
  одна stale schema expectation; после её исправления финальный полный backend:
  `1155 passed, 19 skipped`.
- PostgreSQL runtime не запускался: `TEST_POSTGRES_DSN` не предоставлен.

## Остаточные риски

1. SQLite не доказывает реальную concurrent CAS семантику PostgreSQL.
2. Legacy mutation endpoints остаются без обязательного expected version;
   новый v2 contract строгий, миграция клиентов должна быть отдельной.
3. Deadline policy пока хранится и читается, но не создаёт scheduler jobs.
4. Evidence availability раскрывается только единым безопасным кодом ошибки;
   текст/содержимое источника в историю и read model не копируются.
5. Meeting extraction, digest/notifications, contracts, contacts/search и MVP3
   acceptance остаются следующими срезами.
