# MVP-2 (AI Secretary) — аудит текущего состояния `main`

Дата: 2026-09-11

Ветка: `codex/mvp2-audit` (worktree `../pu-workspace-mvp2-audit`, от `main`
после мержа [PR #20](https://github.com/bigbrotherdmitriy-prog/pu-workspace/pull/20))

Base HEAD: `5c3a04265a3b35d80bb98e784e2a9bbb04ad4fe4` (merge commit PR #20 →
`main`, включает все шесть областей MVP-1 интеграции: OAuth, Snapshots, OCR,
Storage adapters, App composition, Alembic/`contract_versions`)

Это read-only анализ. **Код не менялся, коммит не делался.**

## 1. Метод

Сверял фактическое состояние `main` против восьми пунктов Acceptance
Criteria MVP-2 из задания (ТЗ v5.6, раздел 25, пп. 34-43 — сам файл ТЗ в
репозитории не найден, сверка велась по формулировкам из задания). Нашёл
существующий предшествующий документ
[`docs/audits/mvp2-completion.md`](mvp2-completion.md) (дата 2026-09-04,
offline-приёмка более ранней версии реализации) — использовал как отправную
точку, но каждый пункт проверил заново по текущему коду и тестам, поскольку
после той даты в `main` попало ещё 6+ коммитов `feat(mvp2): ...` (durable
provider-action outbox, Gmail history checkpoints, provider reconciliation).

Основные файлы, задействованные в реализации: `backend/app/api/ai_secretary.py`
(726 строк, ядро pipeline), `backend/app/api/gmail.py` (639 строк, Gmail
ingestion), `backend/app/api/mail.py` (787 строк, mail-client/draft
approval/send), `backend/app/task_engine.py`, `backend/app/response_engine.py`,
`backend/app/governance_engine.py`, `backend/app/summary_engine.py` (локальная
экстракция), `backend/app/models/ai_secretary.py` (модель `Message`).

## 2. Статус по восьми пунктам

### П.1. Разрешённое письмо/сообщение принимается, ссылка на первоисточник сохраняется

**Статус: IMPLEMENTED.**

`backend/app/api/ai_secretary.py::ingest_message()` (строки 406-501) — общий
вход для manual/email/telegram/document источников. Сохраняет
`source_type`, `source_external_id`, `source_name`, `source_url`,
`source_sender`, `source_thread_id`; для писем из подключённого почтового
ящика — ещё и `mail_connection_id`/`provider_message_id`/`source_reference_id`
(FK на `v54_sources`, версионированный источник v5.4-контракта).
Дедупликация по `(mail_connection_id, provider_message_id)` либо
`(source_type, source_external_id)` — при повторном приёме возвращается уже
существующая запись (idempotent).

"Разрешённое" (в смысле "не отфильтрованное") реализовано на уровне Gmail-
ingestion: `backend/app/api/gmail.py::_bulk_email_reason()`,
`_automated_sender_reason()`, `_stored_automated_sender_reason()` (строки
205-262) — массовые/рекламные письма получают `automation_suppressed`,
письма от no-reply-адресов — `response_suppressed`; сообщение всё равно
**принимается и сохраняется** (`status="filtered"`), просто без автоматических
действий (задачи/черновики/риски не создаются) — источник не теряется в
любом случае.

Тесты: `test_gmail_adapter.py::test_marketing_email_is_suppressed_by_strong_provider_evidence`,
`test_business_offer_is_not_suppressed_from_words_alone`,
`test_machine_sender_suppresses_reply_draft_without_filtering_message`,
`test_existing_bulk_message_is_safely_reclassified_on_resync`.

### П.2. Краткая справка + вероятная связь с проектом/договором

**Статус: IMPLEMENTED.**

Связь с проектом: `project_candidate()` (строки 120-154) — ищет явные
упоминания названия проекта/номера договора в тексте письма (regex на
точное, не подстроковое совпадение — `_explicit_mail_reference`), возвращает
`(project_id, confidence, evidence_text)`. Связь с договором:
`_contract_candidate()` (строки 110-117) — аналогично по номеру договора
внутри уже выбранного проекта. Оба явно возвращают **evidence-текст**
("Найден номер договора: …", "Проект определён по содержанию: …") — это и
есть "краткая справка" с основанием, не просто голая уверенность.

Полная краткая сводка сообщения — `backend/app/summary_engine.py::brief_summary()`
— локально, без вызова AI (см. архитектурную заметку в п. 3.1), формирует
структурированную выжимку (документ/основание/сумма/стороны/даты/требуемое
действие) на основе тех же regex-эвристик.

Тесты: `test_gmail_sync_routes_new_messages_through_semantic_project_matching`,
`test_project_candidate_is_available_for_cross_project_routing`.

### П.3. Извлечены поручение/срок/ответственный/сумма с основанием

**Статус: PARTIAL.** Разбор по под-пунктам — они реализованы неравномерно:

| Под-пункт | Статус | Где |
|---|---|---|
| Поручение (задача) | Implemented | `task_engine.py::extract_task_candidates()` — regex по глаголам долженствования (`OBLIGATION_RE`), с исходной цитатой (`excerpt`) как основанием |
| Срок | Implemented | `task_engine.py::extract_explicit_due_date()` — распознаёт явные дедлайн-маркеры (`не позднее`/`до`/`к`/`срок:`), **намеренно не** трактует произвольную дату в предложении как срок (комментарий в коде объясняет: раньше это создавало ложные просроченные задачи) |
| Ответственный | **Missing** как text-извлечение | `task_engine.py::_default_assignee()` (строки 170-180) — назначает исполнителя **по роли в проекте** (owner → manager → editor → …), а не по имени/упоминанию в тексте письма. Ни одного regex/паттерна на "ответственный"/"исполнитель" в коде нет |
| Сумма | **Partial** | Есть только внутри `summary_engine.py::brief_summary()` как строка в человекочитаемой сводке (`r"\b(?:общая стоимость|стоимость работ|сумма|ндс)\b"` — совпадающее предложение целиком, не вычлененное число). **Нет** структурного поля `amount`/`сумма` ни на `Task`, ни на `Obligation` — в отличие от полей `due_date`/`confidence`/`source_excerpt`, которые там есть |

"С основанием" — да, для реализованных под-пунктов: каждая `Task`/`Obligation`
хранит `source_excerpt`, `source_excerpt_hash`, `confidence`, `needs_review=True`
(всегда, без исключений) — сознательная эвристика, не выдаётся за точность
(докстринг `TaskCandidate`: "Candidate score is a heuristic review signal, not
a calibrated probability").

Тесты: `test_task_engine.py` (8 тестов) — не нашёл среди них теста именно на
извлечение ответственного или суммы (что согласуется с их отсутствием как
структурной фичи).

### П.4. Низкая уверенность → подтверждение, а не автозакрепление связи

**Статус: IMPLEMENTED**, чётко и последовательно.

Порог: `context_confirmed = confidence >= 0.90` (строка 444). Ключевое:
`_analyze_confirmed_message()` (строки 282-332) в самом начале —
`if not row.context_confirmed or not row.analysis_required: return [], [], [], []`
— **ни одна задача/риск/черновик не материализуется**, пока проект/договор
не подтверждён (либо автоматически, при уверенности ≥0.90, либо вручную,
через `confirm_context`/`confirm_context_bulk`). Именно так же на связь с
договором: `if payload.routing_confidence is not None: ... if confidence <
0.90: contract = None` — низкая уверенность **явно обнуляет** уже
предполагавшуюся связь с договором, а не оставляет её "на всякий случай".

Тесты: `test_ai_secretary_api.py::test_nonactionable_machine_message_is_filtered_but_retained`,
плюс вся цепочка `confirm_context`/`confirm_context_bulk` эндпоинтов
покрыта в `test_ai_secretary_api.py`.

### П.5. Редактируемый проект ответа готовится, но не отправляется автоматически

**Статус: IMPLEMENTED.**

`response_engine.py::create_response_drafts()` создаёт `ResponseDraft` со
статусом `draft` (не `sent`). Правка: `backend/app/api/mail.py::update_mail_draft`
(строка 638) — subject/body/один канонический recipient редактируемы;
**любая правка утверждённого черновика откатывает approval**
(`draft.approved_revision = None; draft.approved_by_user_id = None;
draft.approved_at = None`, строки 670-672) — то есть нельзя незаметно
подменить текст уже одобренного письма. Разделены роли: `approve_mail_draft`
требует `"editor"`, `send_mail_draft` требует `"manager"` (строки 688, 713) —
двухуровневый барьер перед реальной отправкой.

Тесты: `test_response_drafts_api.py::test_editing_an_approved_draft_invalidates_previous_approval`,
`test_recipient_is_editable_but_change_requires_fresh_confirmation`,
`test_editor_cannot_approve_external_email_envelope`,
`test_mail_client_api.py::test_edit_invalidates_approval_and_revision_conflicts`.

### П.6. Задача/событие создаются только после подтверждения, один раз, с внешним ID

**Статус: IMPLEMENTED.**

"Только после подтверждения" — см. п.4 (`_analyze_confirmed_message`'s early
return). "Один раз" — три независимых механизма: (а) дедуп по
`(project_id, source_file_id, source_excerpt_hash)` перед созданием `Task`
(`task_engine.py` строки 192-199); (б) `send_mail_draft`'s
`send_idempotency_key = sha256(draft_id:revision:key)` (`api/mail.py:704-718`)
— повторный вызов с тем же ключом возвращает тот же результат
(`replay=True`), не дублирует отправку; (в) внешний ID —
`backend/app/integrations/external_resources.py::ExternalResourceLink` —
провайдер-нейтральная таблица `(entity_type, entity_id, provider,
resource_type) → external_id`, читается через `external_id_for()`
(используется в `_message_payload` для `google_task_id`/`google_calendar_event_id`).

Реальное создание во внешнем провайдере (Google Tasks/Calendar) идёт не
напрямую синхронно, а через durable provider-action контур
(`app/pilot_task_mutation.py`, `app/action_trust/validation.py`,
модели `v54_provider_action.py` — approval-gated, с идемпотентным outbox).
Прошлый `mvp2-completion.md` (2026-09-04) отмечал этот контур как
"NOT connected, legacy routes still call providers synchronously" — коммиты
после той даты (`feat(mvp2): add provider action control center`,
`feat(mvp2): enable approved product outbox policy`,
`feat(mvp2): persist fenced Gmail history checkpoints`) выглядят как раз
закрывающими этот gap; **точный текущий охват (все task/calendar-мутации
или только часть) не проверял на уровне построчного code review каждого
провайдер-адаптера** — см. п. 4 (ограничения).

Тесты: `test_mail_client_api.py::test_send_requires_current_approval_is_idempotent_and_preserves_headers`,
`test_v54_gmail_a05_wiring.py`, `test_v54_mailbox_identity.py`.

### П.7. Цепочка Message → Project/Contract → Task/Event → Source сохраняется

**Статус: IMPLEMENTED.**

`Message.project_id`/`Message.contract_id` (FK) → `Task.message_id`/
`ResponseDraft.message_id` (проставляются в `_analyze_confirmed_message`,
строки 314-319) → `ExternalResourceLink(entity_type="task", ...)` для
внешнего Task/Calendar ID → `Message.source_reference_id` (FK на
`v54_sources`) для почтовых сообщений. Вся цепочка читаема одним запросом —
`_message_payload()` (строки 157-260) собирает tasks/drafts/risks/
completion_suggestions/evidence_refs по `message_id` в едином payload,
отдаваемом в UI.

### П.8. Все действия в аудите; недоступность AI не блокирует ручную обработку

**Статус: IMPLEMENTED**, с важной архитектурной оговоркой (см. п. 3.1).

Аудит: `AuditLog` пишется на каждое значимое действие —
`message_processed`, `message_analysis_materialized`,
`message_context_confirmed`, `message_context_bulk_confirmed`,
`message_status_updated`, `outgoing_completion_reviewed`,
`automation_rule_created/state_changed/run_prepared`, `mail_draft_approved`
и другие в `api/mail.py`. Не нашёл ни одного мутирующего эндпоинта в
`api/ai_secretary.py`/`api/mail.py` без соответствующей записи в `AuditLog`.

"Недоступность AI не блокирует ручную обработку" — верно, но **не потому,
что предусмотрен fallback при отказе AI**, а потому что **основной pipeline
вообще не вызывает AI** (см. 3.1). Единственное место, где AI реально
используется — `api/mail.py::assist_mail_draft` (AI-ассистированное
составление письма, отдельная опциональная кнопка) — явно проверяет
`provider.health().ready` и возвращает `503 ai_provider_not_configured`,
не блокируя ничего другого (черновики создаются/редактируются/
утверждаются/отправляются локальным pipeline'ом независимо).

Тест: `test_mail_client_api.py::test_gemini_mail_assist_uses_policy_and_never_sends`.

## 3. Архитектурные особенности main, отличающиеся от буквального прочтения ТЗ

### 3.1. "AI Secretary" не использует AI/LLM в основном pipeline

Самое существенное расхождение с тем, что можно было бы предположить по
названию фичи и формулировке пунктов 3/8. Вся экстракция и маршрутизация —
`task_engine.py`, `response_engine.py`, `governance_engine.py`,
`summary_engine.py` — **полностью локальные, детерминированные, regex-based
эвристики**, ни одна из них не импортирует `app.integrations.ai` и не
делает сетевой вызов к какому-либо AI/LLM-провайдеру. `configured_ai_provider`
используется только в двух местах, оба — вне ядра pipeline:
`api/mail.py::assist_mail_draft` (опциональная AI-помощь при составлении
письма) и аналогично в `api/telegram.py`.

Это не хуже буквального прочтения ТЗ — пункт 8 ("недоступность AI не
блокирует ручную обработку") выполняется тривиально и надёжно именно
потому, что у основного pipeline нет AI-зависимости, которая могла бы стать
недоступной. Но если ТЗ подразумевало LLM-based извлечение (не просто
устойчивость к сбоям AI, а собственно интеллектуальный анализ через модель),
то это архитектурное решение — точно **не то**, а другое, работающее решение
той же задачи через эвристики с explicit confidence/evidence на каждый
кандидат.

### 3.2. Разделение "AI Secretary inbox" и "Mail client" на два разных модуля/API

`api/ai_secretary.py` (`/ai-secretary/inbox`) и `api/mail.py`
(`/mail/projects/{id}/messages`, `/mail/drafts/...`) — два разных роутера,
частично пересекающихся по модели (`Message`, `ResponseDraft`), но с разными
ответственностями: `ai_secretary.py` — маршрутизация/подтверждение/
материализация предложений; `mail.py` — полноценный почтовый клиент
(папки, треды, составление, approve/send с CAS-проверкой ревизии). ТЗ,
судя по формулировке пунктов, описывает это как единый vertical slice —
main решил как два взаимодействующих, но архитектурно разделённых модуля
(с отдельными frontend-модулями `ai-secretary/`/`inbox/` и `mail/`).

### 3.3. Отдельная durable "provider action" подсистема для внешних побочных эффектов

Задачи/черновики создаются как **локальные предложения** сразу
(`external_action_status="proposed"`), а реальное создание во внешнем
провайдере (Google Tasks/Calendar/Gmail send) идёт через отдельный,
явно подтверждаемый и durable provider-action контур
(`app/action_trust/`, `app/models/v54_provider_action.py`,
`app/pilot_task_mutation.py`), а не напрямую в момент подтверждения
сообщения. Это архитектурно более строгое решение п. 6 ("один раз, с
внешним ID"), чем можно было бы прочитать буквально из ТЗ — вместо
"создать один раз" main выстроил целый approval/outbox-механизм с
idempotency-ключами и recovery после сбоя (Gmail history checkpoints,
provider reconciliation — судя по именам недавних `feat(mvp2)`-коммитов).

## 4. Ограничения этого анализа

- Не проверял построчно **каждый** провайдер-адаптер (Google Tasks,
  Calendar, Gmail send) на предмет того, действительно ли 100% путей
  создания внешних сущностей идёт через durable outbox, а не какие-то
  всё ещё синхронные "legacy routes" (см. п. 6) — `mvp2-completion.md`
  зафиксировал это как открытый gap на 2026-09-04, коммитов после этой
  даты достаточно, чтобы предположить закрытие, но не подтверждено
  прямым чтением каждого файла.
- Файл самого ТЗ v5.6 (раздел 25) не найден в репозитории — сверка велась
  по формулировкам пунктов из задания пользователя, не по оригинальному
  тексту раздела. Если в ТЗ есть более узкие/иные формулировки конкретных
  требований (например, точный порог confidence, конкретный список
  извлекаемых полей), их стоит сверить с оригиналом отдельно.
- Frontend-модули (`ai-secretary/`, `inbox/`, `mail/`) проверены только на
  уровне существования файлов и test-покрытия по названиям — не читал их
  реализацию построчно.
- Не проверял PostgreSQL-специфичное поведение (concurrency, real Gmail
  API) — это read-only offline-анализ, тем же методом, что и
  `mvp2-completion.md` 2026-09-04.

## 5. Итоговая сводка

| № | Пункт | Статус |
|---|---|---|
| 1 | Разрешённое письмо принимается, источник сохраняется | Implemented |
| 2 | Краткая справка + вероятная связь с проектом/договором | Implemented |
| 3 | Поручение/срок/ответственный/сумма с основанием | **Partial** (ответственный — не извлекается из текста, только default по роли; сумма — только в тексте сводки, не структурное поле) |
| 4 | Низкая уверенность → подтверждение, не автозакрепление | Implemented |
| 5 | Редактируемый проект ответа, без автоотправки | Implemented |
| 6 | Задача/событие только после подтверждения, один раз, с внешним ID | Implemented |
| 7 | Цепочка Message → Project/Contract → Task/Event → Source | Implemented |
| 8 | Все действия в аудите; недоступность AI не блокирует | Implemented (AI не является зависимостью основного pipeline вообще) |

7 из 8 пунктов — implemented, с хорошим тестовым покрытием и осмысленными
архитектурными решениями (местами строже буквального ТЗ, не слабее). Один
пункт (3) — partial, конкретно и по существу: "ответственный" не извлекается
из текста письма (только эвристический default по роли в проекте), "сумма"
не выделяется как структурное поле (только как часть свободного текста
сводки).
