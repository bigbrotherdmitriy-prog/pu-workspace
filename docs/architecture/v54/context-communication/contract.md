# Контракт v1

Все операции ниже — проектные требования, не существующие endpoints.
MUST означает условие приёмки пилота. Технические ID не являются разрешением.

## 1. ContextRelation

Связь — направленное утверждение с самостоятельным жизненным циклом. Реляционная
таблица и индексированные запросы достаточны; обход графа пилота ограничен двумя
переходами и пагинацией (до 100 связей на страницу). Обратное отображение вычисляется,
а не создаёт вторую независимую связь.

| Поле | Тип / обязательность | Семантика |
|---|---|---|
| relation_id | UUID, обязательно | ID конкретного утверждения; не ID объекта |
| lineage_id, revision | UUID + positive int | Цепочка ручных исправлений; unique(lineage_id, revision) |
| organization_id | существующий ID, обязательно | Один tenant; выводится сервером |
| source, target | `{type,id}` | Тип из allowlist; внутренний ID как opaque string; не provider ID |
| relation_type | versioned string | Семантика и допустимые пары типов из реестра ниже |
| scope | `{kind,id}` | project или mailbox; ограничивает действие, не предоставляет доступ |
| state | hypothesis / confirmed / rejected / superseded | Текущее состояние с audited transition |
| confidence | decimal [0,1] или null | Оценка extractor; подтверждение человека не подменяет её числом 1 |
| evidence_refs | непустой список versioned Evidence refs | Доказательства утверждения; без копий текста |
| origin | `{kind,provider,model,prompt_version,rule_ref,analysis_ref}` | kind: human/model/rule/import; ненужные поля null |
| initiated_by, created_at | principal ref + UTC | Кто запросил операцию; отдельно от тех. service/worker |
| confirmed_by, confirmed_at | principal ref + UTC, nullable | Только authorized human или разрешённый policy actor |
| supersedes_relation_id | UUID, nullable | Предыдущая ревизия; того же tenant/lineage |
| expected_source_version, expected_target_version | opaque version refs | Версии для проверки применимости |
| applicability | current / stale / source_unavailable / target_deleted / evidence_unavailable / legacy_unverified | Не смешивать с подтверждением |
| record_version | increasing int | CAS для состояния/актуальности |
| correlation_id, ledger_event_ref | opaque refs | Причина/аудит, журнал принадлежит общему action-потоку |

Новые relation_type добавляются реестром с source/target types, cardinality,
ACL и invalidation rules; String вместо PostgreSQL enum. Это изменение
контракта/валидации, не миграция всех бизнес-таблиц. Неизвестный тип отклоняется.

Минимальный реестр:

| relation_type | source → target | Правило пилота |
|---|---|---|
| communication.project | Message → Project | Несколько hypotheses, не более одного current confirmed primary |
| communication.contract | Message → Contract | Один primary confirmed; Contract.project_id совпадает с подтверждённым проектом |
| communication.participant | Message → ProjectContact identity | Много; точный нормализованный email, не один домен |
| contact.project | ProjectContact identity → Project | Много подтверждённых проектов; связь не правило назначения всех писем |
| communication.attachment | Message → DocumentVersion/SourceReference ref | Attachment identity из Evidence owner; владение байтами здесь не возникает |
| communication.reply_to | Message → Message | Только один mailbox и надёжные references; связь не завершает Task |
| communication.task | Message → Task | Создаётся после результата общего create-internal-task |
| communication.draft | Message → ResponseDraft | Ссылка на draft/proposal, не разрешение отправки |

Договорный срок — claim Evidence owner, на него ссылается proposal задачи.
Не создавать здесь вторую сущность Deadline/Clause/Evidence. Готовая связь
Project–Contract из Contract.project_id читается через resolver, не backfill
всех договоров в граф. Company identity/справочник контрагентов переиспользуется
только при доказуемой идентичности, без объединения по похожим названиям.

### Состояния, исправление, история

- Извлечение формирует hypothesis. Высокий confidence сам по себе не выдаёт
  права confirmed. Для пилота primary project/contract подтверждает человек;
  будущий rule-confirm возможен только с policy decision другого владельца.
- Confirm принимает relation_id + expected record_version + Evidence refs.
  Сервер повторно проверяет ACL, версии и принадлежность договора проекту.
  Общий audit append и изменение состояния должны быть crash-safe вместе.
- Reject сохраняет исходное утверждение, причину и audit; повтор analysis
  не воскрешает rejected при том же source/claim/analysis signature.
- Correct создаёт новый relation_id в той же lineage, revision+1; старый
  confirmed становится superseded. В одной транзакции фиксируются CAS,
  текущая primary projection и audit через согласованный owner-механизм.
  Два concurrent confirm одного Message сериализуются lock на Message/context_version;
  loser получает context_version_conflict, а не last-write-wins.
  При смене проекта прежняя contract relation также superseded в этой транзакции;
  новый contract либо принадлежит новому проекту, либо остаётся null до выбора.
- Утверждение (source/target/type/evidence/origin) после создания не редактируется;
  state/applicability обновляются CAS с append-событием общего журнала.
  Поэтому история не требует второго ContextLedger. Terminal assertion может
  стать основой только новой ревизии, никогда скрытого переписывания.
- Ручная правка применяется к этому Message/claim. Она не переносит ProjectContact,
  не меняет другие письма thread и не создаёт Rule. Создание правила — отдельное
  явное действие вне пилота. Массовая коррекция имеет явный список message IDs,
  preview и проверки каждого источника/цели.

### Доступ и жизненный цикл объектов

View relation требует доступа одновременно к tenant, source, target, scope и
каждому раскрываемому Evidence. Предложение проекта не расширяет доступ к ящику;
проектный editor не получает чужое письмо по угаданному relation_id. Для Message
обязательно mailbox ACL. Для контактной identity раскрываются только связи
разрешённых проектов. Сервисный principal действует от bounded actor/scope,
не получает право confirm от факта запуска worker.

Контекст выбирается SQL/ACL-фильтрами **до** ранжирования и отправки в AI.
Скрытые объекты не попадают в candidates, excerpt, count, pagination cursor,
имя компании, error details или объяснение «есть ещё один секретный проект».
Адресный запрос недоступного ID даёт одинаковый not_found. Cursor непрозрачен,
привязан к principal/scope/filter; перед каждой страницей ACL проверяется заново.
Ревокация прав между чтением, approval и исполнением блокирует следующий шаг.

Удаление в provider не удаляет relation/evidence историю: applicability меняется,
locator становится unavailable. Изменение существенной source version делает
зависимые claims/proposals stale; прежнее подтверждение сохраняется в истории,
но не допускает исполнение. Rename с той же подтверждённой content version может
обновить display locator только по решению SourceReference owner. Перенос Contract
в другой Project инвалидирует зависимую связь, не переносит её автоматически.

Hard-delete target блокируется для объектов пилота до tombstone/retention-процедуры;
никакого ON DELETE CASCADE для истории связей. После разрешённого удаления остаётся
нечувствительный tombstone ref; названия/фрагменты не копируются в relation.
Если retention требует удаления Evidence, остаётся evidence_unavailable и
технический факт audit без содержимого; новый EXECUTE запрещён. Подтверждённые
исполненные Task не отменяются удалением источника: follow-up только отдельным action.

## 2. Идентичность сообщения и контакта

`MailConnection` — стабильная связь tenant ↔ provider mailbox. Логическая identity:
`(organization_id, provider, provider_account_key)`. Не использовать текущий
project_id, display email, OAuth token ciphertext или threadId как identity.
Credential reference/epoch может меняться при обновлении того же account;
смена самого account создаёт новое connection_id и никогда не переименовывает старое.
Если provider не даёт устойчивый account key, адаптер возвращает unsupported/
unverified identity: автоматический cross-project pilot не запускается.

Message identity: unique(mail_connection_id, provider_message_id), направление
inbound/outbound хранится отдельно. Один Gmail объект с SENT+INBOX остаётся одним
объектом с provider labels/direction metadata. RFC Message-ID — индексируемое
свидетельство, **не уникальный PK**: возможны дубли/подделка/отсутствие.
Message immutable origin содержит connection_id, source_ref и provider_message_id;
`project_id/contract_id` — подтверждённая бизнес-проекция, изменяемая только через
context correction. Неподтверждённое сообщение доступно mailbox intake reviewer,
не привязывается к active project. Cross-tenant transfer запрещён.

Храним разобранные From/To/Cc, In-Reply-To, упорядоченные References, RFC Message-ID,
provider thread ID и received_at с версиями нормализации. Bcc доступен только там,
где разрешён source ACL; не дополняется догадками. Email — parse + trim +
casefold по текущему правилу; не удалять `+tag`, точки или приводить доменные алиасы.
Некорректные/многозначные адреса не становятся одним «первым контактом».

Один контакт может иметь несколько `contact.project` relations. Подтверждение
участия в проекте не означает exclusivity. Domain/company label — слабая
подсказка, не доказательство назначения письма. Legal company выбирается по
проверенному identity, в пилоте — существующая вручную подтверждённая запись.

## 3. Context resolution и thread/RFC

Порядок рассмотрения (при противоречиях не выбирать победителя молча):

1. Current human-confirmed связи конкретного Message: не заменять анализом.
2. Same-mailbox reply с точным RFC reference на известное исходящее сообщение
   и совместимыми участниками/confirmed context. Получить hypothesis проекта;
   если source недоступен или найдено несколько разных проектов — manual review.
3. provider threadId в том же mailbox — corroborating evidence, не единственное
   доказательство. Один thread может обсуждать несколько проектов.
4. Отдельный номер договора и имя проекта из source+attachment Evidence;
   сравнение по границам, не `12 in 3124`. Два договора/проекта → candidates.
5. Подтверждённые contact.project relations и участники письма помогают ранжировать,
   но один email/домен не обходит конфликтующее содержание.

Результат: `analysis_ref`, `context_version`, полный доступный список candidate
relation refs с confidence/reason_code/evidence_refs, `requires_review` и
`unverified_claim_refs`. Недостаток evidence не скрывает письмо: остаётся inbox
и ручное уточнение, но executable proposal не готов. Если attachment unavailable,
не выдумывать срок из темы. AI видит недоверенный документ как данные, не команду
для смены policy/получателя/проекта. Provider/model меняются через AIProviderAdapter;
pilot mock, по умолчанию local_only, внешняя передача не подразумевается.

Manual confirm выбирает конкретные project/contract, assignee и дату, ссылаясь
на source evidence или отдельное Evidence записи человека (выдаёт другой поток).
Изменение срока человеком не заменяет срок, написанный в документе: сохраняются
source claim и override reason. Неясное «до пятницы» без timezone/anchor остаётся
unverified. Пилот поддерживает date-only с явной timezone и принятой организацией
границей рабочего дня; не конвертирует date в произвольное UTC midnight.

## 4. Приём, повтор, pagination и BackgroundJob

At-least-once delivery допускается; exactly-once внешней отправки не обещается.
Для connection/scope фильтра хранится checkpoint revision, page token или
history watermark, last_success_at. Scope/version включает INBOX/SENT/архив,
временное окно и query hash. Смена query создаёт новый scope, не использует чужой cursor.
Пилот читает один явно разрешённый synthetic mailbox, входящие и исходящие;
не наследует ограничение `is:inbox newer_than:7d` как полноту истории.

Обработка страницы:

1. Проверить actor/mailbox policy, snapshot checkpoint version.
2. Адаптер читает bounded page; каждую provider ref upsert по mailbox identity.
   В той же DB-транзакции записать accepted ref и `analysis_required=true`.
   Дубликат не изменяет human context и не создаёт новые intents.
3. Продвинуть checkpoint CAS только после durable регистрации **всех** refs
   страницы. Ошибку отдельного fetch допускается перенести в processing record
   только с явным retry_required/terminal reason; она не считается analyzed.
   Чтение полного тела может произойти после записи ref, по SourceReference policy.
4. После commit поставить существующий BackgroundJob. Текущий `enqueue()` сам
   делает commit/rollback — не считать его частью внешней общей транзакции.
   Recovery scan по analysis_required находит commit-before-enqueue и повторяет
   enqueue с тем же key. Это выборка состояния домена, не новая очередь.
5. Worker проверяет live lease/fencing **и** analysis/context version до каждой
   фиксации. Анализ с устаревшим context не публикует результаты поверх ручной правки.
   Если enqueue найденного terminal job не запускает его, явный retry/redrive
   существующего API очереди после проверки разрешения; новый idempotency key
   ради обхода dead-letter запрещён.

Job payload только IDs: `{organization_id, mail_connection_id, message_id,
analysis_run_id, actor_id, expected_context_version, correlation_id}`.
Для polling — connection_id + sync_scope_id; cursor хранится в checkpoint,
не превращается в доступный пользователю секрет. Никаких тел, base64, MIME,
attachment bytes, credentials, signed download URLs или фрагментов Evidence
в payload/result/errors/технических логах.

SourceReference owner разрешает read вложения из исходного connection; bytes
живут только в разрешённом transient/cache режиме с TTL, не в этой схеме.
Если доступ запрещает обработку, processing=blocked; не создавать копию/staging
как обход. Письмо с вложением и пустым body регистрируется, не пропускается.

Ошибки/повторы нормированы в [таблице](pilot-acceptance.md#ошибки-и-повторы).
Просроченный cursor → bounded rescan последнего подтверждённого окна с overlap,
dedup и видимым gap marker; нельзя объявлять полную синхронизацию при пропуске
истории. Два poller используют checkpoint CAS: проигравший не перезаписывает cursor.

## 5. Повтор анализа и граница общего action contract

`CommunicationAnalysisRun` хранит message_id, source version refs, extractor/model/
prompt/schema version, input_fingerprint, context_version, generation, статус и
ссылки на results. Unique(message_id, input_fingerprint, context_version, generation).
Retry того же запуска не меняет generation. Reanalyse — явная новая generation,
но не новая бизнес-задача по умолчанию.

Proposal dedup отделён от job dedup: `intent_key=(organization,message,claim_anchor,
action_type)` стабилен при изменении формулировки AI. claim_anchor выдаёт Evidence
owner либо сопоставляет подтверждающий человек; hash текста/срока/порядковый
номер AI-пункта не является стабильной business identity. Если новое выделение
нельзя однозначно сопоставить старому claim, оно требует reconcile, а не create.
Ревизия намерения хранит новый payload, но тот же intent_key. Подтверждённый или
исполненный task/draft не перезаписывается анализом; изменение → отдельное update
intent с precondition на существующий объект. Rejected revision не возрождается
без нового решения человека. Concurrent analysis упирается в unique intent mapping.

Этот поток передаёт **intent**, не исполняет его:

| Вход общего владельца | Требуемая семантика |
|---|---|
| contract_version, intent_key, revision, action_type | Стабильная идентичность и тип: create-internal-task / prepare-response-draft / send-external-message |
| actor_ref, organization_id, scope_project_id | Серверный tenant/scope, не доверять полям AI |
| source_ref, evidence_refs, relation_refs + versions | Версия оснований, актуальность/ACL |
| payload_ref + version | Хранилище общего Proposal owner; он вычисляет canonical hash |
| preconditions | expected_context_version, source versions, target versions, pinned connection |
| correlation_id, analysis_ref | Сквозная трассировка |

Общий владелец возвращает proposal_ref/version/hash, policy decision ref
(ASSIST/CONFIRM/AUTO/BLOCK), approval_ref, execution_ref/outcome и ledger_event_refs.
Названия статусов согласуются с его контрактом; здесь не создаётся Approval table,
подпись approval, policy evaluator, executor или второй журнал.

Для pilot create-internal-task=CONFIRM, send-external-message=CONFIRM,
prepare-response-draft=DRAFT в пределах read/write policy. Здесь DRAFT —
состояние проекции, не четвёртый режим policy: подготовка
черновика выполняется в ASSIST, без внешнего эффекта. Отдельный acceptance
может включить AUTO **только** для low-risk internal task решением общей policy.
Подтверждение ContextRelation не равно approval действия. Правка получателя,
срока, assignee, проекта, evidence или body создаёт новую payload revision;
старый approval не действует. EXECUTE повторно проверяет права и preconditions.

Существующий Task появляется как исполнившийся внутренний action, а не как
побочный эффект analysis. До этого UI может показывать общую Proposal projection.
ResponseDraft допустим как редактируемая DRAFT-проекция, но `approved` не является
самостоятельным пропуском мимо общего gate. Существующие handlers task/create,
draft/send переиспользуются владельцем Execution через facade. Для pilot объектов
старые HTTP write routes также обязаны входить в этот gate — иначе пилот закрыт.

Успешный create-task receipt привязывается `communication.task`, а результат send —
к immutable outgoing identity и draft. При сбое между side effect и receipt
повтор решает общий Execution/Ledger через idempotency/reconciliation. Поток
communication не запускает Gmail send заново после тайм-аута. Internal task
reversal и corrective follow-up письма — новые actions общего владельца, не
удаление audit. External email не имеет надёжного undo.

## 6. Ожидание ответа и эскалация

`ResponseExpectation` ссылается на successful outbound execution/message и
confirmed Task (если есть), mailbox, expected participants, due_at/timezone,
policy_ref, revision. Состояния: waiting / response_received / overdue /
cancelled / blocked_context. До успешной отправки expectation не активируется.
Срок ожидания ответа задаётся человеком/подтверждённой policy отдельно от срока
задачи; совпадение дат в synthetic fixture явно принято оператором, не выведено
автоматически из наличия deadline в документе.
Поздний ответ переводит overdue→response_received, не стирает факт просрочки.
Auto-response/bounce/list mail не закрывает waiting; конфликтные references
оставляют candidate reply relation и требуют человека.

Получение коррелированного ответа меняет expectation, **не** Task.status.
Задача completed только через явное подтверждение результата/action. Новый
анализ ответа может предложить изменение задачи; время ответа не evidence выполнения.

Эскалация только для confirmed tasks/expectations, не для hypothesis. Используются
существующие scheduled jobs и notification/digest domain, не отдельный scheduler.
Перед due tick проверяются актуальные task state, deadline revision, ACL,
context/evidence и policy. Один intent escalation на (expectation/task, deadline
revision, escalation level, scheduled window); повтор tick не создаёт дубль.
В MVP5 — одно внутреннее уведомление ответственному/руководителю и digest.
Внешнее напоминание только новым send intent с CONFIRM. Перенос срока отменяет
старый pending escalation, не удаляет историю; cancelled/completed task не эскалируется.
