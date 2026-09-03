# Gmail project validation

## Аудит до изменений

База: `814ff77b79bd3a6d1382c345783946a7b9b7898e`.
Ветка: `codex/gmail-project-validation`, отдельная чистая worktree.
Применимых AGENTS.md нет. Исходная worktree содержит семь пользовательских
изменений; они не переносились. Baseline: 31 тест passed.

Специализированные пути: `backend/app/api/gmail.py` (sync/send/import),
`backend/app/api/project_contacts.py` (email normalization/contact routing),
`backend/app/api/ai_secretary.py` (semantic routing/ingest/context/completion).
Отдельного mail-routing модуля нет. `backend/app/automations/gmail.py` вызывает
sync с `is:inbox newer_than:7d`, max_results=25; прочитан, изменения вне scope.
Jobs handlers/scheduler только вызывают эту автоматизацию; не изменяются.
GoogleWorkspaceAdapter и модели прочитаны для проверки контрактов, не меняются.

Подтверждено чтением кода до исправления:

1. Ручной query: `newer_than:7d`, 25 сообщений по умолчанию (до 100).
   Берётся одна страница, nextPageToken игнорируется. Автоматизация исключает
   SENT и архивированные письма, а большой поток вытесняет старые сообщения.
2. Сервис выбирается по project_id через GoogleOAuthToken, но Message не
   хранит connection/account ID. source_external_id — сырой Gmail ID,
   unique(source_type, source_external_id) глобален. Поиск дубля в Gmail ещё
   шире: только external_id, без source_type/tenant/доступа.
3. При дубле возможны изменение attachments/contact/drafts чужого проекта;
   ingest также возвращает существующее письмо без проверки прав на него.
4. contact_for_sender имеет приоритет над явным другим проектом в содержании.
   project_candidate возвращает confidence/evidence, но sync их отбрасывает.
   ingest заново вычисляет confidence по договору только fallback-проекта.
5. ProjectContact unique(organization_id, normalized_email) допускает только
   один проект на email. Discovery может переносить неподтверждённый контакт
   между проектами и включать ранее отключённый контакт.
6. email нормализуется parseaddr/strip/casefold. Company — строковая подсказка
   из домена, не создание юридического лица. Слияния компаний по имени нет.
7. threadId сохраняется, но для назначения проекта не используется.
   In-Reply-To/References/Message-ID не сохраняются. send передаёт threadId
   Gmail, но не RFC threading headers. Без account scope безопасный thread
   matching нельзя добавлять автоматически.
8. Отправка меняет только draft.status=sent. Completion — предложение с
   ручным review, но review не сверяет текущий проект письма/задачи после
   ручного переноса. Unconfirmed исходящие тоже порождают предложения.
9. Контекст письма при повторном sync не переназначается — это сохраняет
   ручную корректировку. Неоднозначное письмо хранится на fallback-проекте
   и видно в inbox доступных проектов организации как неподтверждённое.
10. Структурированного списка кандидатов, waiting_for_reply и FK контакта/
    компании у Message нет. Полное исправление требует интеграционного контракта.

План минимальных изменений: regression-тесты реального ingest/sync с fake Gmail;
проверка доступа на дублях, сохранение semantic confidence/evidence, ручной
review при конфликте контакта и содержания, сохранение контактов при resync,
проверки актуального проекта в completion. Модели, миграции, jobs, внешние
адаптеры и алгоритмы анализа не изменять.

## Исправления и воспроизведение

До исправлений новые восемь regression-тестов: **8 failed** по заявленным
assertions, не по setup. После первого исправления: 39 passed вместе с baseline.
Затем добавлены тесты подстроки номера и домена: **2 failed, 9 passed** в новом
файле. Исправлены границы совпадения номера и использование компании/домена
как самостоятельного доказательства проекта. Существующие тесты не изменялись.

- Gmail передаёт `routing_confidence` в существующий IncomingMessage.
  Низкая уверенность не повышается найденным в fallback-проекте договором.
  Уверенный проект из темы теперь сохраняется вместе с evidence.
- Контакт и явное содержание рассматриваются совместно. Конфликт или несколько
  проектов дают `needs_context_confirmation`, без contract_id. ID кандидатов
  сохраняются в существующем `context_evidence` (не в новой модели).
- Номер должен совпадать как отдельное значение, а не подстрока другого номера
  или адреса. Компания/домен без проекта или номера не подтверждают контекст.
  Архивные проекты исключены из semantic-кандидатов.
- Sync проверяет editor-доступ к source project и существующему письму до
  backfill. Чужая организация блокируется даже для глобального admin.
  Дедуп Gmail больше не захватывает manual/telegram с таким же external_id.
  Ingest также проверяет права перед возвратом существующего письма.
- Discovery не переносит неподтверждённый контакт и не отменяет active=false.
  Неуверенные письма не создают контакт на основании временного intake-проекта.
  Подтверждённые contact-поля не перезаписываются. Ручной перенос контакта
  сбрасывает старый contract_id; явный null теперь также очищает связь.
- Completion-кандидаты создаются только для подтверждённого контекста.
  Review сверяет текущие проекты task/message/suggestion и права; устаревшая
  ссылка даёт 409. Выдача inbox скрывает предложения другого проекта.
- Повторный sync сохраняет ручной project_id, contract_id, context_evidence и
  status. Gmail attachments остаются в прежнем механизме; job payload не менялся.

## Правила назначения проекта

| Признак | Решение | Подтверждение |
|---|---|---|
| Ранее сохранённая ручная связь | Не переназначать при sync | Сохраняется |
| Однозначное отдельное название проекта или номер договора | Доступный проект той же организации, confidence 0.95 | Контекст подтверждён автоматически |
| Точный email подтверждённого активного контакта без конфликтующего содержания | Проект контакта, confidence 0.99 | Контекст подтверждён автоматически |
| Контакт и содержание указывают на разные проекты | Не выбирать победителя; сохранить кандидатов в evidence | Обязательно ручное |
| Несколько проектов в содержании | confidence 0.40, contract_id отсутствует | Обязательно ручное |
| Только домен/название компании, неизвестный отправитель | confidence 0.55, без доказательства проекта | Обязательно ручное |
| Несколько адресатов без другого доказательства | Не выбирать первый email как подтверждённый контакт | Обязательно ручное |
| Только threadId/In-Reply-To/References | Автоматическая привязка пока отсутствует | Обязательно ручное |
| Совпадающий Gmail ID чужого/недоступного проекта | Ошибка без backfill/выдачи чужого письма | Разбор идентичности подключения |

Физически Message.project_id остаётся обязательным: при неизвестном контексте
это **временный intake-проект**, а не подтверждённая бизнес-связь. Inbox показывает
такие письма и в других доступных проектах той же организации, с лимитом 200.
У пользователя без доступа к intake-проекту это письмо не появится — ослаблять
права ради видимости нельзя. Для настоящей общей очереди разбора нужна модель
mailbox-access, а не использование active project как назначения.

Точное совпадение email относится к текущему подтверждённому contact-mapping,
не доказывает эксклюзивность работы отправителя в одном проекте. Текущая модель
не умеет хранить несколько таких mapping; конфликтующий текст требует человека.
Группы адресатов, Cc/Bcc и цитаты старых писем не являются полноценным matcher.

## Правила статусов письма и задачи

| Событие | Письмо/черновик | Задача |
|---|---|---|
| Входящее с неясным контекстом | needs_context_confirmation | Анализ может создавать только reviewable предложения; внешнее действие не подтверждается |
| Подтверждённое входящее | ready; ответ остаётся draft | Поручение может быть assigned, needs_review=true, external_action_status=proposed |
| Реклама/массовое письмо | filtered | Автоматические задачи/черновики не создаются |
| Исходящее SENT | email_outgoing; сообщение не равно выполненной задаче | Только completion suggestion, status=proposed, и только при подтверждённом контексте |
| Отправка согласованного ответа | ResponseDraft.status=sent | Не меняет Task.status на completed |
| Человек подтвердил актуальное completion suggestion | Запись reviewer и audit | completed, TaskHistory; повтор review идемпотентен |
| Письмо перенесено после создания suggestion | Старое предложение нельзя применить, 409 | Старая задача не закрывается |
| «Ожидает ответа» | Отдельного состояния/correlation пока нет | Автоматического перехода нет |
| «Требует действия» | Смысловой вывод/needs_context_confirmation, не новый enum | Существующий review/assigned workflow |

Изменение статуса Message вручную не означает изменение статуса Task.
После ручного подтверждения ранее неуверенного исходящего автоматический
пересчёт completion-кандидатов пока не вызывается; повторный sync не перезапускает
анализ. Это отдельная операция для интегратора, не молчаливое повторное выполнение.

## Причины отсутствующих писем, оставшиеся ограничения

1. Query автоматизации `is:inbox newer_than:7d` исключает исходящие, архив и
   историю старше недели. Первая страница max_results=25 без cursor может
   постоянно возвращать уже обработанные письма. Query и scheduler не менялись.
2. Неавторизованный новый проект отсутствует в списке GoogleOAuthToken.
   Организация/контакт не создают OAuth-подключение автоматически.
3. Уверенные письма другого проекта не показываются как письма текущего;
   неизвестные зависят от доступа к intake-проекту и лимита inbox 200.
4. Письмо без извлекаемого текста и snippet пропускается, даже с вложением.
5. Исторически неверно подтверждённые записи не переназначаются автоматически:
   это защищает ручные исправления, но требует ручной сверки старых данных.
6. Gmail IDs не изолированы по ящику. Исправление прав блокирует чужой backfill,
   но не решает коллизии разных ящиков в доступных проектах одной организации.
   Глобальная уникальность может давать failed вместо нового письма. Нет
   доказательства exactly-once при конкурентном ingest.
7. company у контакта — текстовая подсказка, не юридическое лицо. Нет автоматической
   регистрации/объединения Organization и FK контрагента у Message.
8. Не реализованы корректная thread/RFC-корреляция, ожидание ответа и автоматическая
   связь reply с задачей. Не имитировать их общим Gmail threadId без mailbox scope.
9. При ручном переносе Message его source project и почтовое подключение теряются:
   старые import/send используют текущий project_id. До интеграционной миграции
   письмо после переноса нельзя считать проверенным источником download/reply.
10. Глобальный admin в текущем RBAC не ограничен одним tenant. Приватность
    разных ящиков внутри доступного проекта требует mailbox ACL. Это не менялось.

## Точный контракт для интегратора (не реализован в этой ветке)

Нужны согласованные модели/миграции и handlers, без переноса body/base64/token
в BackgroundJob:

- `mail_connections`: id (стабильный PK), organization_id FK, provider,
  provider_account_id (устойчивый ID ящика), credential_reference; unique
  (organization_id, provider, provider_account_id). Отдельный доступ пользователей/
  проектов к подключению. Не идентифицировать ящик по OAuth token ciphertext.
- `messages.mail_connection_id` FK, `provider_message_id`, `rfc_message_id`,
  `in_reply_to`, `references_json`; индекс (mail_connection_id, source_thread_id).
  Gmail dedup: unique(mail_connection_id, provider_message_id), независимо от
  направления письма. Старую глобальную unique(source_type, source_external_id)
  заменить через проверенный backfill; строки с неизвестным ящиком оставить на
  reconciliation, не угадывать по текущему project_id. Это также отделит thread
  identity от переключения UI и ручного переноса проекта.
- В download/reply передавать сохранённые connection_id + provider_message_id;
  отдельно проверять доступ к письму, проекту и ящику. Сохранять RFC Message-ID
  отправленного письма и Gmail threadId/ID из send result, строить reply headers
  только из подтверждённого сообщения того же ящика. Sender/To/Cc хранить как
  нормализованные наборы участников, не как первый адрес parseaddr.
- Контакт как identity: сохранить unique(organization_id, normalized_email),
  добавить `project_contact_links(contact_id, project_id, contract_id nullable,
  confirmed, confirmed_by, confirmed_at, active)` с unique(contact_id, project_id).
  После проверки старых связей убрать требование единственного ProjectContact.project_id.
  Название компании не ключ; юридическое лицо связывать вручную или по проверенному
  реестровому идентификатору с явным подтверждением, без fuzzy-merge.
- `message_project_candidates(message_id, project_id, confidence, evidence_json)`
  с unique(message_id, project_id); `context_assignment_source` enum human/rule,
  `context_confirmed_by/at`. `Message.project_id` nullable для mailbox intake
  либо отдельная intake сущность. Выдача кандидатов только после ACL.
- `mail_sync_state(mail_connection_id, query_scope, page_token/history_id,
  last_success_at)`; bounded batch, продолжение следующей страницы, продвижение
  cursor после commit, сохранение failed refs для retry. Sync API должен отдавать
  has_more/cursor и counts; automation должна включать SENT/архив согласно явно
  выбранному scope. Jobs меняются в отдельном потоке: payload только connection_id,
  actor_id, query/cursor и числовые идентификаторы, без сообщений и вложений.
- `message_task_links` с типом evidence/reply/completion и ручным reviewer;
  waiting_for_reply на уровне переписки/ожидаемого ответа с due_at и resolved_by.
  Reply помечает ответ полученным, но не Task.completed. Перенос контекста должен
  инвалидировать старые suggestions и явно пересчитывать новые с идемпотентностью.

Готовность к полной Gmail-project валидации **не заявляется**: пункты с новой
моделью обязательны для проверки разных ящиков, thread replies и общей очереди
разбора. Эта ветка устраняет воспроизведённые дефекты в существующем контракте.

## Проверки и изменённые файлы

Новые тесты используют только synthetic example.test, fake Gmail, SQLite in-memory,
mock AIProviderAdapter и локальные заглушки анализа/Telegram. Существующий pilot
acceptance отдельно проверяет реальные локальные task/draft engines с synthetic
письмом и mock публикацией. Реальные письма не отправлялись, ящики не читались,
secret-файлы не открывались. Docker/psql и чистая тестовая PostgreSQL недоступны;
PostgreSQL concurrency и реальный Gmail не проверены. Skip не добавлены.

Команда проверки:

Итог: **55 passed, 0 failed, 0 skipped** (23 новых regression/validation +
31 существующий целевой тест + pilot acceptance). Единственное предупреждение —
существующий Alembic DeprecationWarning об отсутствующем path_separator;
конфигурация Alembic не менялась. `git diff --check` без ошибок.

```text
python -m pytest tests/test_gmail_project_validation.py tests/test_gmail_adapter.py tests/test_gmail_automation.py tests/test_project_contacts.py tests/test_outgoing_email_completion.py tests/test_ai_secretary_api.py tests/test_mvp5_pilot_acceptance.py -q -p no:cacheprovider
```

Файлы: `backend/app/api/gmail.py`, `backend/app/api/ai_secretary.py`,
`backend/app/api/project_contacts.py`, `backend/tests/test_gmail_project_validation.py`,
`docs/audits/gmail-project-validation.md`. Остальные области не изменялись.
