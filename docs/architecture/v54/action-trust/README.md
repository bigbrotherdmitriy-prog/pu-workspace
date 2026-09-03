# Action trust v5.4 — проект контракта

Статус: **PROPOSED / NOT IMPLEMENTED / OWNER DECISION REQUIRED**.
База аудита: `66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`.
Дата: 2026-09-03. Этот пакет не меняет действующие права, политику или roadmap.

Цепочка: ActionProposal → Policy → Approval → Execution → Ledger → Compensation.
Предлагается тонкий trust facade перед существующими исполнителями и единый
формат событий аудита. Новый scheduler, очередь, action engine, Company Memory
и агенты не создаются. Domain state остаётся в Task, ResponseDraft,
OrganizerProposal/Operation и CashFlowEntry.

Документы:

- [Контракт, состояния, инварианты и логическая схема](contract.md).
- [Матрица policy и минимальный пилот](policy-pilot.md).
- [Синтетические примеры](examples.json).
- [Негативные сценарии для реализации](negative-scenarios.md).
- [Совместимое внедрение и вопросы интегратора](rollout.md).
- [Аудит и границы доказательства](../../../audits/v54-action-trust-contract.md).

## Existing → reuse → gap

Все ссылки ниже относятся к проверенной базе, не к будущей реализации.

| Existing / точка входа | Reuse | Gap / необходимая адаптация |
|---|---|---|
| [OrganizerProposal/Action/Operation](../../../../backend/app/models/organizer.py) | Пакет, решения по элементам, before/after, idempotency keys | Отдельной модели ChangeBatch нет; facade отображает пакет на OrganizerProposal, элемент на OrganizerAction. Нет sealed revision и hash-bound Approval |
| [OrganizerRepository](../../../../backend/app/organizer_engine/repository.py): decide, edit_item, mark_prepared | Текущие решения и CAS перед подготовкой | Approval — mutable status. reconcile_operation перезаписывает before/after; это projection, не неизменяемый ledger |
| [OrganizerExecutor](../../../../backend/app/organizer_engine/executor.py): apply, _preflight, _recheck_sources, rollback | Проверки источника, existing rename/move, журнал операций и восстановление | Проверки batch не равны provider conditional-write. Rollback проверяет область, но не гарантирует, что текущее состояние совпадает с after; нужен per-operation guard, без обещания универсального undo |
| [organizer.py](../../../../backend/app/organizer.py): decide/apply/apply-source-* | Manager/owner RBAC, подтверждение источника, safe copy | apply_auto_policy и ORGANIZER_AUTO_APPLY — узкая legacy auto-copy политика; не организационная AUTO policy v5.4 |
| [Task engine](../../../../backend/app/task_engine.py): create_tasks_from_files | Извлечение кандидатов, дедуп source/excerpt, Task + Obligation | Task с assigned/needs_review уже создаётся до подтверждения; creator берётся из assignee. Пилот должен отличать proposal от бизнес-создания и не создавать финансовые обязательства |
| [tasks.py](../../../../backend/app/api/tasks.py): approve_external, update_task | Проверки роли, membership, TaskHistory, результат завершения | approved не привязан к версии. update_task после локального commit может вызвать publish_actions для ранее executed задачи; cancel pilot нельзя делегировать этому endpoint без изоляции внешнего эффекта |
| [responses.py](../../../../backend/app/api/responses.py): update_draft | Черновик и действующий review UX | Изменение body/subject без status сохраняет approved; отсутствуют ревизия, expiry, revoke. Старый approval нельзя считать v5.4 approval |
| [gmail.py](../../../../backend/app/api/gmail.py): send_gmail | Manager, approved, sent_external_id и текущий Gmail transport через Google Workspace service | Нет атомарной action reservation. Send до DB commit оставляет окно неизвестного исхода; повтор после timeout не доказан безопасным |
| [AI Secretary](../../../../backend/app/api/ai_secretary.py): ingest_message, review_completion_suggestion | Evidence hints, контекст, explicit review и TaskHistory | Confidence и подтверждение контекста не разрешают execution. Время/источник approver и версия proposal должны быть отдельными |
| [AutomationRule/Run](../../../../backend/app/models/automation_rule.py), [automation_engine](../../../../backend/app/automation_engine.py) | Периодический запуск и unique(rule_id, scheduled_for) | Подготовленный run не является разрешением отправки. Назначенный _actor не должен имитировать решение человека |
| [ProjectAIPolicy](../../../../backend/app/models/ai_policy.py), [ai_policy.py](../../../../backend/app/ai_policy.py) | Ограничения передачи данных внешнему AI | local_only/redacted/metadata_only/external_allowed — ось privacy, не ASSIST/CONFIRM/AUTO; нет action allowlist/risk/approval epochs |
| [core/auth.py](../../../../backend/app/core/auth.py): require_project_role | Серверный RBAC | is_admin глобален; нет granular action permission и role epoch. Глобальный admin не становится AUTO-approver по умолчанию |
| [execution_finance.py](../../../../backend/app/api/execution_finance.py): create_invoice_proposal, confirm_payment | proposed, связи договор/ГПР/бюджет, manager-confirmed fact, идемпотентный повтор | Отдельный hash-bound approval планового обязательства и факта; значения по умолчанию actual_amount/date должны фиксироваться до подтверждения |
| [organizations_contracts.py](../../../../backend/app/api/organizations_contracts.py): update_organization | requisites_status и существующий аудит | confirmed requisites не подтверждает оплату; изменение реквизитов требует новой версии отдельного approval |
| [AuditLog](../../../../backend/app/models/audit_log.py) | Единая существующая точка бизнес-аудита | Нет типизированных tenant/actor/proposal/revision/correlation/outcome; details произвольный Text. Нет доказанной append-only защиты |
| [BackgroundJob](../../../../backend/app/models/job.py), [queue.py](../../../../backend/app/jobs/queue.py) | Durable enqueue, claim, lease, heartbeat, retry/cancel, owner fencing | Unique job key и lease не обеспечивают единственное бизнес-действие у провайдера. enqueue делает commit; нельзя притворяться, что произвольная обёртка создаст одну транзакцию |

## Решение о MVP5 / MVP6 — только предложение

В разделе ТЗ «Strategic Trust… / 8» организационные autonomy policies отнесены
к MVP6, а «9 / сценарий B» требует в MVP5 create-internal-task=AUTO и
send-external-message=CONFIRM.

Предлагается ADR: MVP5 содержит **минимальную версионированную серверную
настройку одной организации/проекта для одного allowlisted действия**
`task.internal.create`, выключенную по умолчанию. Включение — отдельное явное
действие уполномоченного человека с аудитом, scope и сроком. Нельзя включить
AUTO командой модели, содержимым письма или переменной канала. Остальные типы
остаются CONFIRM/ASSIST либо DENY.

MVP6: иерархия организационных правил, delegation, UI-конструктор, сложные
пороговые условия, массовые политики. Не разрешает high-risk AUTO автоматически.
Таким образом MVP5 проверяет сам механизм policy-bound AUTO, не получает полный
enterprise policy engine. **Владелец ещё не утвердил это разделение.** До решения
реализация ограничивается CONFIRM; приёмка AUTO-сценария остаётся незакрытой.
