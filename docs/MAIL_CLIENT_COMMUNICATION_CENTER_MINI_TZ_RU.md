# PU Workspace — Mini-ТЗ «Почтовый клиент / Communication Center»

Статус: проект требований, не свидетельство реализации. Версия: 1.0 draft.
Базовый аудит: `022313bc03a42e3157ee38433b5745b725c147b2`.

## 1. Цель и продуктовый результат

В PU Workspace должен появиться единый рабочий почтовый центр для проектной
переписки: пользователь читает письма, ведёт цепочки, создаёт и редактирует
черновики, отвечает, отвечает всем, пересылает и отправляет письма из явно
выбранного корпоративного ящика. Каждое письмо можно связать с проектом и
договором, проверить средствами AI Secretary и использовать как доказуемый
источник задач, сроков, рисков и решений.

Первый рабочий provider — Gmail. Архитектура не должна требовать изменения Core
для подключения Яндекс 360 Mail или Microsoft Exchange в следующих срезах.
Цель не означает копирование всех функций Outlook: продукт фокусируется на
проектной коммуникации, доказательствах и управляемых действиях.

## 2. Исходное состояние: existing → reuse → gap

### 2.1. Есть в базовом коммите

- provider-neutral `ChannelAdapter` с базовыми `receive` и `send`;
- Google Workspace credential facade и Gmail OAuth scopes на чтение/отправку;
- получение Gmail-сообщений, сохранение `Message`, sender/thread/source links;
- AI-summary, предложения задач/рисков и `ResponseDraft`;
- ручная связь входящего сообщения с проектом и договором;
- просмотр и импорт вложения входящего Gmail-сообщения;
- редактирование, отклонение и подтверждение черновика;
- отправка подтверждённого ответа через Gmail и сохранение внешнего ID;
- простая защита от повторной отправки уже отмеченного отправленным черновика;
- RBAC проекта и события существующего `AuditLog`.

Это полезный вертикальный фрагмент, но не полноценный почтовый клиент.

### 2.2. Подготовлено в соседних ветках, но не входит в базу этого документа

Read-only проверены следующие независимые результаты. До merge их нельзя считать
частью текущего продукта или production contract:

| Направление | Read-only источник | Что следует переиспользовать после интеграции |
|---|---|---|
| Mailbox cutover | `5416224f6f1be45dcff7cffa7dcb8ec0b2768e45` | стабильная mailbox identity, mailbox-scoped dedup, origin history |
| Authoritative mailbox identity | `aa222ca4428874e3645336302c8afb35cfcdb0a5` | verified account/namespace, credential generation, запрет fallback |
| Rollout controls | `e6e05c5b4527dec4fbf96c582a1619b5a6bc1d1d` | fail-closed flags shadow/pilot/primary/actions и CAS |
| Provider action runtime | `9d22ed3ce384e9df47c616c1b5fefe8fb32c7086` | outbox, attempt, `APPLIED/NOT_APPLIED/UNKNOWN`, reconciliation |
| Email compensation | `9c676d117a7a25baf5af64f653c85872e287d075` | corrective follow-up вместо фиктивного undo отправленного письма |
| v5.4 Source/Evidence, Context и Action Trust | integration contract на базе `4db9d51496e25d7916ecc75a5dfdf61a930c8637` | ObjectRef/VersionPin, Evidence, ContextRelation, approval exact revision, receipt/ledger |

Новый почтовый модуль не создаёт собственные аналоги этих сущностей. Если
соседний контракт не интегрирован, зависимость остаётся явным blocker или
временным compatibility bridge с планом удаления.

### 2.3. Подтверждённые пробелы базового коммита

- нет mailbox/folder navigation: Inbox, Sent, Drafts, Archive и provider labels;
- нет полноценного списка цепочек и просмотра всей переписки;
- нет создания нового произвольного письма в общем mail UI;
- нет reply-all, forward, `Cc`/`Bcc` и исходящих вложений;
- нет mailbox-scoped identity: raw provider ID глобально дедуплицируется;
- email/token/project не дают стабильную identity почтового ящика;
- нет RFC `Message-ID`, `In-Reply-To`, `References` как сохранённых атрибутов;
- нет локального и provider search с явным указанием области поиска;
- изменяемый `ResponseDraft` не имеет immutable revision и content hash;
- approval не закрепляет точные адреса, вложения, mailbox generation и revision;
- отправка выполняется синхронно в API-процессе без durable outbox/attempt;
- нет надёжного `UNKNOWN`/reconciliation и доказательства «отправлено ровно один раз»;
- audit содержит provider ID и не является минимизированным Action Ledger receipt;
- нет component/browser E2E полного пользовательского сценария.

## 3. Архитектурная граница

```text
Mail UI / Communication Center
          |
Communication application service
  |       |        |          |
Context  Evidence  Approval   Durable action/outbox
                              |
                  Email-capable ChannelAdapter
                    |          |          |
                  Gmail   Yandex Mail   Exchange
```

Core не импортирует SDK провайдера. `ChannelAdapter` остаётся общей границей
каналов, а почтовые возможности добавляются как capability-интерфейсы, не ломая
Telegram и другие простые каналы. Минимальные capabilities:

- `MailboxDiscovery`: доступные ящики и их состояние;
- `MailFolderReader`: папки/labels, counters и provider cursor;
- `MailThreadReader`: список цепочек и сообщения одной цепочки;
- `MailSearch`: поиск с описанным scope и pagination cursor;
- `MailDraftTransport`: provider draft при наличии такой возможности;
- `MailDispatch`: отправка sealed MIME/message revision;
- `MailOutcomeLookup`: reconciliation неоднозначного результата.

Capability registry сервера определяет доступные операции. UI не показывает
действие как доступное, если точное подключение или provider capability его не
поддерживает. Отсутствие capability не заменяется молчаливым выбором другого
ящика или провайдера.

## 4. Идентификаторы и область данных

Для каждой операции сервер использует явные ссылки:

- `organization_id` и аутентифицированный actor;
- стабильный `connection_identity_ref` и `mail_connection_ref`;
- provider namespace и exact credential generation;
- `mailbox_ref`, `folder_ref`, mailbox-scoped `thread_ref` и `message_ref`;
- project/contract context как отдельная подтверждаемая связь;
- exact draft/action revision и version pins.

Email-адрес, display name, OAuth token row и активный проект не являются identity
почтового ящика. Одинаковые provider message ID двух аккаунтов не совпадают.
Thread ID имеет смысл только внутри exact mailbox/provider namespace. Тема письма
не используется как доказательство принадлежности к цепочке.

Перенос письма в другой проект меняет ContextRelation, но не источник, mailbox,
provider message ID и историю origin. Legacy-сообщение с неизвестным mailbox
помечается unresolved; для него запрещены attachment download и отправка до
явного reconcile. Нельзя угадывать ящик по активному проекту.

## 5. Функциональный состав

### 5.1. Ящики, папки и списки

- явный переключатель подключённого ящика с provider и состоянием доступа;
- нормализованные системные представления: Входящие, Отправленные, Черновики,
  Архив, Корзина, Спам; provider labels/folders доступны отдельно;
- unread/important/attachment/project/contract/status filters;
- pagination/cursor без загрузки всей почты в память;
- состояние синхронизации: current, syncing, degraded, revoked, unavailable;
- ручное обновление и понятная дата последней успешной синхронизации;
- локальный список не выдаётся за актуальный provider state при stale/offline.

Перемещение между provider folders и удаление не входят в первый срез: сначала
нужны отдельные action types, permissions, compensation и retention решения.

### 5.2. Цепочка и письмо

- сообщения показываются в хронологической цепочке с from/to/cc, временем,
  состоянием, вложениями и явным источником;
- HTML sanitization обязательна; remote images и tracking content по умолчанию
  блокируются, их загрузка требует отдельного действия;
- quoted history сворачивается, но доступна пользователю;
- оригинал/headers доступны только при соответствующем праве;
- provider source version/freshness отображаются отдельно от AI confidence;
- поздний ответ не возвращает UI к другому проекту или ящику.

### 5.3. Поиск

Поддерживаются локальный индекс и provider search. Перед запуском пользователь
видит область: текущий ящик, папка, проект или договор. В результате указываются
источник и актуальность. Provider query не хранится в content-free audit целиком.
Cross-tenant, cross-mailbox и revoked connection результаты исключаются server-side.

### 5.4. Compose, reply, reply-all и forward

Любая операция сначала создаёт внутренний версионируемый черновик:

- новое письмо: пользователь явно выбирает From/mailbox и адресатов;
- reply: recipient и RFC thread headers вычисляются сервером из exact source;
- reply-all: сервер строит To/Cc, исключает собственные адреса и дубликаты, но
  пользователь обязательно просматривает итоговый список;
- forward: создаёт новый адресатный контекст; вложения включаются только после
  явного выбора и проверки прав;
- signature/template — versioned input, а не скрытая модификация при отправке.

Поля To/Cc/Bcc, тема, body, reply/thread headers и набор вложений валидируются
сервером. CR/LF header injection, недопустимые адреса, oversized message,
запрещённые MIME и недоступные вложения блокируются до approval.

Автосохранение создаёт новые draft revisions либо обновляет только mutable
working copy до freeze. Provider draft может быть дополнительной проекцией;
внутренний draft/revision остаётся источником решения PU Workspace.

### 5.5. Вложения

- входящее вложение имеет собственный SourceReference/SourceVersion;
- просмотр, скачивание, OCR/анализ и импорт — разные permissions;
- исходящее вложение закрепляется exact source/version либо за загруженным
  staging object с policy/retention pin;
- замена bytes, имени, representation или порядка вложений создаёт новую revision;
- данные не передаются внешнему AI без разрешающей ProjectAIPolicy;
- временные plaintext-файлы, URL с credentials и содержимое вложений запрещены
  в queue payload, audit и logs.

Первый Gmail-срез должен поддержать безопасные вложения в пределах заданного
лимита. Cloud-link вместо attachment является отдельным действием и не включается
неявно.

### 5.6. Проект и договор

AI может предложить проект/договор и показать evidence/confidence, но связь
подтверждает пользователь. Выбор активного проекта не является подтверждением.
Неопределённый контекст остаётся в общем Inbox и не прикрепляется к первому или
Persistent Project автоматически.

Context confirmation, проверка извлечённого срока и approval отправки — три
разных решения. Изменение связи после анализа делает связанные выводы stale и
требует пересчёта; оно не меняет origin письма.

### 5.7. AI Secretary

Разрешённые функции:

- краткая сводка цепочки и последнего сообщения;
- выделение задач, сроков, рисков, решений и вопросов;
- предложение проекта/договора с evidence;
- один или несколько вариантов ответа и тональности;
- проверка адресатов, вложений, обещаний, сумм и сроков перед approval;
- показ различий между утверждаемой revision и предыдущей.

Каждый вывод AI содержит provider/model/prompt version и confidence там, где он
калиброван. Извлечённый факт, AI-вывод и человеческое решение показываются
раздельно. Инструкции внутри письма или вложения — недоверенные данные: они не
назначают проект, роль, permission, policy, получателя или действие.

`local_only`, `metadata_only`, `redacted`, `external_allowed` соблюдаются до
вызова AIProviderAdapter. При запрете внешней передачи интерфейс предлагает
локальный анализ или сообщает ограничение; документ нельзя отправить внешней
модели под видом диагностики.

## 6. Approval точной версии и отправка

### 6.1. Freeze и approval

Перед подтверждением draft freeze создаёт immutable revision и canonical hash.
Approval закрепляет минимум:

- organization/project/contract context и их версии;
- actor/approver, authority epoch, policy version и срок действия;
- exact mailbox identity, connection, credential generation и provider;
- operation: compose/reply/reply-all/forward;
- To/Cc/Bcc, subject и canonical body hash;
- exact attachment refs/versions/digests;
- reply/thread headers и source message/version;
- полный перечень ожидаемых эффектов и idempotency key.

Подтверждение не передаётся boolean-полем `approved=true`. Сервер хранит decision,
revision и binding. Любое изменение адресатов, темы, текста, подписи, вложения,
mailbox, context, policy или исполняемого metadata создаёт новую revision; старое
approval становится неприменимым. UI показывает diff и кнопку
«Подтвердить и отправить версию N».

Роль проверяется при approval и непосредственно перед provider dispatch. AI,
worker и service principal не могут подтвердить письмо. Global admin не получает
право отправки из tenant mailbox без отдельного tenant mandate.

### 6.2. Durable execution

Исполнение использует существующий `BackgroundJob`, единый action/outbox и
provider runtime после их интеграции. API фиксирует outbox до enqueue. Worker
резервирует attempt до provider I/O. Queue payload содержит только opaque refs;
body, адреса, filenames, OAuth tokens и MIME не включаются.

Idempotency scope: `(organization, mail_connection, draft_id, revision,
operation)`. Повтор клика, HTTP retry, рестарт API или два worker не создают
вторую отправку.

Business outcome не смешивается с job status:

| Business state | Значение | Допустимое действие |
|---|---|---|
| `AWAITING_APPROVAL` | точная revision не подтверждена | проверить/изменить/подтвердить |
| `READY` | approval и preconditions действительны | поставить на исполнение |
| `DISPATCHING` | attempt зарезервирован | ждать/reconcile, не отправлять второй раз |
| `APPLIED` / `SENT` | provider receipt доказывает эффект | показать receipt; нового dispatch нет |
| `NOT_APPLIED` | доказано отсутствие эффекта | явный безопасный retry новой попыткой |
| `UNKNOWN` | эффект мог произойти | provider lookup/reconciliation; blind retry запрещён |
| `BLOCKED` | stale/revoked/policy/permission conflict | устранить причину и подтвердить новую revision |

`BackgroundJob.completed` означает, что orchestration завершила текущий шаг; это
не доказательство `SENT`. `cancelled` не доказывает, что запрос не дошёл до
провайдера. После `UNKNOWN` таймер или потеря lease не превращают результат в
`NOT_APPLIED`.

### 6.3. Receipt, audit и компенсация

Receipt append-only связывает action/revision, attempt, exact mailbox, provider
outcome, безопасный timestamp и opaque provider IDs. Content-free Action Ledger
не содержит адресов, темы, body, filename, provider raw response или token.
Детальные поля доступны только в защищённом mail store по отдельному ACL.

Audit фиксирует draft created/edited/frozen, context confirmed/corrected,
approval granted/revoked/expired, dispatch authorized, outcome observed,
reconciliation и corrective action. Rollback транзакции не оставляет одинокий
receipt или ложный success event.

Отправленное письмо необратимо. UI не обещает delete/undo. Исправление создаёт
новый compensating/corrective follow-up с новой revision, approval,
idempotency key, receipt и audit; исходное письмо и outcome не переписываются.

## 7. Ошибки и восстановление

- `401`: сессия истекла — сохранить локальную несекретную working copy, войти;
- `403`: нет mailbox/project/attachment permission — deny без утечки объекта;
- `404`: объект недоступен в разрешённой области;
- `409 revision_conflict`: draft/source/context изменён — показать diff и создать
  новую revision, не отправлять автоматически;
- `409 approval_stale|revoked|expired`: требуется новое подтверждение;
- `409 connection_generation_changed`: переподключить/перепроверить exact mailbox;
- `422`: адрес, capability, размер, attachment или context невалиден;
- `429/502/503`: до provider effect — bounded retry с backoff; после возможного
  effect — `UNKNOWN` и reconciliation;
- `dead_letter`: оператор видит безопасный код и correlation/job/action ID;
- reload/late response: UI восстанавливает exact mailbox/project/thread/action,
  старый ответ не переключает контекст.

Операторский retry разрешён только для `NOT_APPLIED` и проверяет текущие права,
policy, approval, source/version и credential generation. Restore/reconcile не
обходит эти проверки.

## 8. Security, privacy и data policy

- OAuth scopes разделяются на read/draft/send и выдаются по минимуму;
- токены шифруются и разрешаются adapter внутри server-side credential boundary;
- production `.env` и credentials не передаются в frontend, jobs и artifacts;
- auth actor определяется серверной сессией; payload не может назначить actor;
- все list/read/search/draft/send операции проверяют tenant + mailbox + project;
- sender/recipient/subject/body/HTML/attachments считаются untrusted content;
- HTML sanitization, remote-content blocking, MIME/size limits и malware/DLP hook
  выполняются до показа или отправки согласно политике;
- логи и ошибки не содержат body, subject, адреса, вложения, токены, signed URL,
  provider raw response или полный filesystem path;
- retention задаётся отдельно для original metadata, body, quote, AI prompt,
  embedding, attachment/cache/staging, receipt, audit и backup;
- запрет локальной копии проверяется до download/extraction/staging;
- отзыв connection немедленно закрывает новые provider reads/actions; история
  сохраняется только в разрешённой минимизированной форме;
- data residency и внешний AI — независимые policy gates;
- Bcc не раскрывается другим получателям, в UI цепочки или content-free audit.

Default — `CONFIRM`. Внешняя отправка в `AUTO` не входит в этот Mini-ТЗ.

## 9. API-контракт: требования, не готовые endpoints

Параллельный backend-поток зафиксировал предварительную provider-neutral группу
`/mail`: capabilities, folders, messages, threads и thread/message detail. Это
интеграционный контракт, а не утверждение, что маршруты присутствуют в базовом
коммите. Интегратор сверяет точные URI и OpenAPI перед соединением веток.

Минимальный read contract:

```text
GET /mail/capabilities
GET /mail/folders
GET /mail/messages
GET /mail/threads
GET /mail/threads/{thread_ref}  # detail; exact route name сверяется при интеграции
```

Все read-запросы несут или выводят server-side exact mail connection и cursors;
ни один из них не выбирает mailbox по активному проекту. Compose contract должен
поддерживать `To/Cc/Bcc`, metadata вложений и следующие операции:

```text
POST   /mail/drafts                 # create working draft
PATCH  /mail/drafts/{draft_ref}     # expected revision required
POST   /mail/drafts/{draft_ref}/approve
POST   /mail/drafts/{draft_ref}/send  # exact approved revision + idempotency_key
POST   /mail/actions/{action_ref}/retry
POST   /mail/actions/{action_ref}/reconcile
```

Названия mutation routes также provisional. Семантика обязательна: approve точной
revision отделён от send; отправлять может только actor с server-confirmed
`manager`/mailbox-send authority; retry разрешён только после доказанного
`NOT_APPLIED`; `UNKNOWN` блокирует новый dispatch до reconciliation.

Mutations используют caller-owned transaction; domain helpers не делают скрытых
`commit/rollback/close`, provider calls или enqueue. Ответы несут opaque refs,
record versions, safe status/reason и correlation ID. Provider IDs, содержимое и
credentials не возвращаются там, где они не нужны UI.

## 10. UX-поток

Нормативные экраны, тексты, состояния и accessibility описаны в
[отдельной UX-спецификации](ux/MAIL_CLIENT_COMMUNICATION_CENTER_UX_RU.md).
Минимальный happy path:

```text
Выбрать ящик → открыть Inbox/цепочку → подтвердить проект и договор
→ Ответить/Ответить всем/Переслать → отредактировать черновик
→ проверить адресатов, вложения, evidence и последствия
→ Подтвердить версию N → Отправить
→ увидеть SENT receipt либо явный NOT_APPLIED/UNKNOWN
```

Подтверждение контекста, проверка AI-фактов и отправка не объединяются одной
кнопкой «Подтвердить всё».

## 11. Критерии приёмки

### 11.1. Функциональные

- [ ] Два Gmail-ящика с одинаковым provider message ID создают разные sources.
- [ ] Переподключение того же аккаунта сохраняет identity; другой аккаунт не
  перезаписывает прежнюю identity.
- [ ] Inbox/Sent/Drafts/Archive, pagination и thread view работают для exact mailbox.
- [ ] Compose, reply, reply-all и forward создают корректные отдельные revisions.
- [ ] Reply-all показывает итоговые To/Cc и исключает собственный адрес/дубликаты.
- [ ] Входящие и исходящие вложения привязаны к exact source/version.
- [ ] Поиск показывает область и freshness, не выдаёт cross-mailbox результаты.
- [ ] Проект/договор выбираются явно и сохраняются после reload.
- [ ] AI summary/draft соблюдает ProjectAIPolicy и показывает provenance/confidence.
- [ ] Изменение любого исполняемого поля требует нового approval.
- [ ] Пользователь без manager/mailbox-send authority не может подтвердить или
  отправить письмо, даже зная draft/action ID.
- [ ] Успешная отправка имеет один receipt и один outcome при повторных кликах.
- [ ] Отправленное письмо предлагает только новый corrective follow-up.

### 11.2. Сбой, конкуренция и безопасность

- [ ] Два worker не выполняют одну revision одновременно.
- [ ] API restart после outbox commit не теряет отправку.
- [ ] Crash до provider effect даёт доказуемый `NOT_APPLIED` и безопасный retry.
- [ ] Crash/timeout после возможного effect даёт `UNKNOWN`, lookup и не второй send.
- [ ] Provider receipt после reload переводит exact action в `SENT`.
- [ ] Отзыв роли, approval, policy или connection до dispatch блокирует effect.
- [ ] Late response после смены проекта/ящика не меняет активный контекст.
- [ ] Cross-tenant/project/mailbox/source/version/attachment запросы fail closed.
- [ ] Logs, audit, queue payload и errors проходят тест на отсутствие content/secrets.
- [ ] HTML, tracking content, header injection и oversized attachments блокируются.

### 11.3. Проверки релизного кандидата

- contract/unit tests на fake adapters;
- PostgreSQL tests для CAS, outbox, двух workers и rollback;
- process-fault test до/после provider effect;
- настоящий browser E2E с синтетическими network fixtures;
- sandbox provider acceptance на отдельном тестовом Gmail-аккаунте;
- существующий backend/frontend regression, build и migrations single-head;
- observability: queue/action/unknown/dead-letter counters без content.

Mock/SQLite не считаются доказательством provider или PostgreSQL concurrency.
Live Gmail acceptance не доказывает готовность Яндекс/Exchange.

## 12. Этапы реализации

1. **Контрактная интеграция.** Свести mailbox identity, Source/Evidence, Context,
   immutable action/approval/receipt и migration head; закрыть owner decisions.
2. **Gmail read-only mailbox.** Ящики, папки/labels, threads, pagination, search,
   freshness и новый UI без provider mutations.
3. **Версионируемый compose.** Working draft, compose/reply/reply-all/forward,
   recipients validation, attachment pins и diff.
4. **Управляемая Gmail-отправка.** Exact approval, durable outbox, idempotency,
   receipt, `UNKNOWN` reconciliation и corrective follow-up.
5. **AI и проектный контекст.** Evidence-backed suggestions, policy gating,
   project/contract workflow и контроль promises/deadlines.
6. **Hardening.** PostgreSQL/process-fault/browser E2E, sandbox acceptance,
   metrics/runbook, staged rollout и rollback controls.
7. **Второй provider.** После Gmail acceptance — Яндекс 360 Mail; Exchange далее
   по клиентскому приоритету. Второй adapter обязан пройти общий contract suite.

Этапы 2–6 образуют Gmail-first вертикальный срез. Их нельзя объявлять готовыми по
наличию UI или единичной успешной отправке.

### 12.1. Расширение пользовательского почтового клиента

В Gmail-first срез также входят базовые привычные функции рабочего клиента:

- форматированный редактор письма: шрифт, размер, цвет, жирный, курсив,
  подчёркивание, зачёркивание, списки, цитата, ссылка и выравнивание;
- персональные настройки отображаемого имени, стиля текста и HTML-подписи;
- подпись отдельно для нового письма и ответа;
- Gemini-помощник для нового письма, ответа, сокращения и изменения тона;
- AI возвращает только редактируемый черновик, не подтверждает и не отправляет его;
- архивирование, перенос в спам и корзину Gmail, а также возврат во входящие;
- destructive provider-действия требуют явного действия пользователя, а неизвестный
  результат не повторяется автоматически.

Удаление в интерфейсе означает перенос в корзину провайдера, а не безвозвратное
удаление. Полная очистка корзины и администрирование антиспам-правил не входят в срез.

## 13. Не входит в текущий срез

- включение `AUTO` для внешних писем;
- полная копия Outlook, Calendar и Contacts;
- почтовый сервер, SMTP/IMAP gateway общего назначения;
- spam/antivirus engine, DKIM/DMARC administration и mail routing;
- shared mailbox/delegation enterprise без отдельного authority design;
- массовые рассылки, marketing automation и tracking;
- offline desktop cache/PST/OST импорт;
- произвольные provider rules, безвозвратное удаление писем и auto-forward;
- одновременная реализация Gmail, Яндекс и Exchange;
- отправка реальных писем в тестах или использование production credentials.

## 14. Интеграционные решения и блокеры

До coding/production acceptance владельцы должны подтвердить:

1. какой интеграционный SHA содержит authoritative mailbox identity и единственную
   Alembic head;
2. роли approve/send/reconcile, self-approval и tenant mandate администратора;
3. retention/data residency для bodies, attachments, AI derivatives и receipts;
4. Gmail scopes и модель provider draft: внутренняя запись, Gmail Draft либо обе;
5. правила reply-all, aliases, group addresses, shared/delegated mailbox;
6. максимальный размер/типы вложений и malware/DLP provider;
7. допустимый freshness TTL и поведение при offline/provider unavailable;
8. telemetry/alerts для `UNKNOWN`, dead-letter и credential revoke;
9. порядок переноса legacy Message/ResponseDraft без угадывания mailbox;
10. sandbox accounts и отдельное разрешение на live-provider acceptance.

Пока решения не приняты, соответствующие операции fail closed. Этот документ не
разрешает merge, миграцию production-данных, provider calls или отправку писем.
