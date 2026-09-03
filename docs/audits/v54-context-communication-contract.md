# Аудит и результат: Context / Communication-to-Action v5.4

## Исходное состояние до изменений

Дата: 2026-09-03. Точная база: `66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`.
Создана новая чистая worktree `pu-workspace-v54-context-communication-contract`,
ветка `codex/v54-context-communication-contract`. Одноимённые ветка и worktree
до создания отсутствовали. Применимых AGENTS.md в репозитории и проверенных
родительских каталогах не найдено. База не заменялась другой веткой.

Основная worktree `pu-workspace-commercial-p2-yandex360`:
ветка `codex/commercial-p2-yandex360`, HEAD
`83774aac726acd4e27b349e9194f30783158bde8`. Незакоммиченные файлы при старте:

```
backend/app/api/auth.py
backend/app/api/local_upload.py
backend/app/api/workspace.py
backend/app/schema.py
backend/app/static/app.js
docker-compose.yml
frontend/index.html
```

Эти изменения не копировались, не редактировались и не включались в commit.
Продуктовый код, модели, миграции, общие документы, production не менялись.

Прочитан заданный DOCX, включая таблицы и дополнения v5.4. Проверен SHA-256:
`AF7BFDE75715345E4F32B9D7CA057812CDBA7B8D8E0B6A1B105DFE20FC0D5DF3`.
Skill documents использован для чтения исходного ТЗ; выход — Markdown/JSON,
поэтому редактирование/рендер DOCX не выполнялись. Исторический титул v5.1
и действующие дополнения v5.4 разобраны в README пакета.

## Проверено в базе

Прочитан [предыдущий Gmail-аудит](gmail-project-validation.md). Его исправления
не выдаются за новые: сравнение специализированных `gmail.py`,
`ai_secretary.py`, `project_contacts.py` с веткой предыдущей валидации не
показало последующих изменений этих файлов в выбранной базе.

| Код / модель | Что уже есть | Пробел для пилота |
|---|---|---|
| api/gmail.py, automations/gmail.py | Sync/send/import, MIME, дедуп и проверки доступа из предыдущей валидации | Ограниченный query/одна страница; нет надёжной connection-scoped identity, RFC metadata и checkpoint |
| models/ai_secretary.py: Message | project/contract, external/thread ID, context confidence/evidence/confirmed, attachments | required project, глобальный unique(source_type, external_id), нет immutable mailbox origin/истории связей |
| api/ai_secretary.py | semantic candidates, конфликт контакта/содержания, сохранение ручной коррекции при resync | Высокий confidence может auto-confirm; ingest напрямую создаёт Task/Draft; нет общего action gate |
| confirm_context / bulk | Проверки проектов, ручное перемещение связанных объектов | Нет CAS/versioned relation; уже исполненную задачу нельзя молча двигать в новом пилоте |
| models/project_contact.py, api/project_contacts.py | normalize_email, unique(org,email), discovery/CRUD, защита подтверждённых и отключённых контактов | Один project_id на identity; company текст, не подтверждённое юрлицо |
| models/task.py и histories | Task, сроки, исполнитель, статус, review, excerpt/hash, история | Не Proposal до исполнения; response ожидания отдельного контракта нет |
| TaskCompletionSuggestion + outgoing completion | Предложения завершения, human review, проверки текущего контекста | Reply/отправка не должны стать completion evidence сами по себе |
| ResponseDraft, api/responses.py | Draft/approved/sent, редактирование, sent_external_id | Нет неизменяемого approval payload hash; старые write routes требуют shared gate для pilot IDs |
| OrganizerProposal/Action/Operation | Согласование документного organizer | Это не универсальный communication/action ledger; не переиспользовать как второй механизм |
| integrations/contracts.py, actions.py | AIProviderAdapter, ChannelAdapter, ActionAdapter и publish_actions | Существующие sync_tasks/calendar не равны Policy/Approval/Execution contract |
| models/audit_log.py, ai_policy.py | Базовый аудит и политика внешнего AI | Не доказывают наличие общего Action Ledger и approval validity |
| jobs/queue.py, models/job.py | Существующий BackgroundJob, idempotency/lease/retry | enqueue сам commit/rollback: требуется durable analysis_required + recovery для окна commit-before-enqueue |

Ограничения получения писем: manual query `newer_than:7d`, automation
`is:inbox newer_than:7d`, по умолчанию 25 результатов; без прохода страниц
письмо может не попасть в синхронизацию. Смена active project не доказывает
принадлежность письма и не должна менять origin mailbox. ThreadId без mailbox
и RFC evidence недостаточен. Домен — не доказательство проекта/компании.

Нынешние механизмы сохранения ручного контекста, ограничения доступа на дублях,
нормализации контактов и ручного completion review сохраняются. Предлагаемые
изменения только для opt-in пилота; автоматического backfill назначений нет.

## Результат проектирования

[Пакет контракта](../architecture/v54/context-communication/README.md):

- `contract.md`: ContextRelation, ACL/lifecycle, identity, resolution, processing,
  action owner I/O, ожидания/эскалация.
- `migration-proposal.md`: additive schema proposal, legacy coexistence,
  staged rollout/rollback, reuse boundaries, решения I-01…I-13.
- `pilot-acceptance.md`: последовательность, таблица сбоев/повторов,
  14 позитивных и 20 негативных сценариев.
- `examples.json`: исключительно синтетические исходники, hypotheses/confirmation,
  owner refs, ID-only job payload, task/draft/send handoff и отрицательные примеры.

| Требование | Где зафиксировано / приёмка |
|---|---|
| ContextRelation, история, org/scope, evidence | contract §1; P-01/P-06, N-01…03/N-18 |
| Mailbox identity, повтор/concurrency, cursor | contract §2/§4; P-02/03/08/14 |
| Multi-project contact, ambiguity, ручной override | contract §2/§3; P-04/06, N-04/05/11 |
| Shared Action/Approval и evidence ownership | contract §5; P-11/12, N-07/08/10/20 |
| Reanalysis без task/send дублей | contract §5; P-07, N-12/15 |
| Reply != task done, ожидания/эскалация | contract §6; P-09/10, N-16/17 |
| Legacy mailbox, no auto reassignment | migration proposal; N-13 |
| ID-only BackgroundJob, без второго queue/staging | contract §4; N-09, recovery tests в runtime gate |

Минимальная сущность связей использует PostgreSQL, а не graph database.
SourceReference/Evidence и общий Action contract описаны только зависимостями.
Компания не создаётся/сливается по домену или похожему названию. Контактные
multi-project relations имеют один write store — ContextRelation, а не
дублирующую таблицу связей из альтернативного предложения прошлого аудита.

## Проверки и ограничения

Локальные проверки пакета: синтаксис JSON; согласованность примера (scope,
relations, intent keys, отсутствие контента в job payload, reply не закрывает
Task); наличие Markdown-ссылок; `git diff --check`; allowlist изменённых файлов.
Это проверки документации, **не runtime regression-тесты нового механизма**.
Результат: PASS для JSON и 7-полевого ID-only payload, ссылок/tenant примера,
трёх уникальных intent keys, инварианта reply != completed, 10 локальных
Markdown-ссылок и наличия всех 14 позитивных / 20 негативных сценариев.
Product tests/PostgreSQL/browser/send не запускались: продуктовая реализация
не входит в задание, сценарии acceptance являются будущей спецификацией.
Исторические числа passed из Gmail-аудита здесь не заявляются новым результатом.

Открытые интеграционные решения I-01…I-13 перечислены в migration proposal.
Критические: Evidence refs/claim_anchor, Source ACL/retention, общий immutable
action/approval и atomic audit, стабильная mailbox identity, nullable intake
project и legacy consumers, защита старых send/write routes от обхода gate.
Без их согласования и фактической приёмки пилот нельзя объявлять готовым.

Никаких реальных писем/аккаунтов, внешнего AI, production data/secrets, push,
merge или deploy. Результат — один документационный commit отдельной ветки;
полный SHA сообщается в итоговом ответе (не включён внутрь самого commit).
