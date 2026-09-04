# UI → необходимые поля/команды контракта

Ниже **нет списка существующих HTTP endpoints**. Foundation предоставляет
типы/models/protocols; A/B/C — DB-only facades в отдельных commits, не в этой базе.
Transport, projection и safe error mapping остаются запросами интегратору.
Названия M6/S13/E16/T7 в HTML — человекочитаемые **demo labels**, не wire IDs.
В wire использовать неизменённые ObjectRef/TaggedId/VersionPin из foundation.

| Элемент UI | Нужные поля / действие | Подтверждённая основа | Gap / запрос |
|---|---|---|---|
| Sender, subject, attachment | Message identity, разрешённые display fields, attachment refs | Existing Message; B register не копирует body/bytes | IR-01 authorized read projection; нельзя подменять происхождение current project |
| Provider/account/namespace | ConnectionIdentity ref/provider; MailConnection identity/namespace/state | Foundation/A identity и B extension | IR-01 разрешённые labels; account_key не token и не автоматически публичное поле |
| Origin / project candidate | SourceReference.origin_project_id отдельно ContextRelation target/state/applicability/confidence/provenance | Foundation/B propose/confirm | IR-03 candidates/read API; происхождение не human confirm |
| Contract candidate | Contract belongs to выбранному project; pin, relation revision и record_version | B confirm проверяет принадлежность | IR-03 не совместить unrelated contract |
| Подтвердить контекст | ContextConfirmation: message, project_relation, contract_relation, expected_context_version, expected_*_relation_record_version | Shared DTO; B confirm | IR-03 transport/idempotent result/safe conflict; actor/tenant только auth server |
| Исправить контекст | Старые обе primary + CAS; новые project/contract/evidence pins | B correct; старые superseded | IR-03 UI selection/candidate lifecycle; exact assertions, не direct Message.project_id patch |
| Evidence provenance | Evidence id/revision/source_id/source_version_id/locator/extractor/confidence/confidence_kind | Foundation; A create_evidence сейчас fixed whole_object/fixture | IR-02 richer authorized projection, model/prompt unavailable → «не предоставлено» |
| Показать фрагмент | Fragment read capability + exact source/version/locator + retention/residency | A resolve operation=fragment возвращает deny | IR-02 reader ОБЯЗАТЕЛЕН; mock quote не свидетельство реализации |
| Проверить evidence | ReviewCommand(evidence pin, expected_record_version, decision) | A review / EvidenceAssessment | IR-02 роли/receipt decision projection; не повышать доверие по confidence |
| Статус evidence | Resolution: pin/actor/project/operation, version/freshness/availability/verification, policy_known/retention_known/residency_allowed/valid_until/epochs | Shared resolver contract / A | IR-02 safe reason distinctions; authority data не отдавать как права редактирования |
| Проверить срок | DeadlineClaim pin, due_date/timezone, evidence pins, verification/reviewer; ReviewCommand | C extract/review | IR-04 claim read/correction/override provenance; без неявного time-of-day |
| Preview задачи | ActionEnvelope payload.title/assignee_ref/due_date/timezone/contract_ref + project_ref/target/pins/effects/risk/autonomy/reversal | Shared ActionEnvelope/CreateTaskPayload | IR-05 trusted renderer/version; record lookup labels под ACL |
| Разрешить задачу | exact action pin + envelope hash + approver + approval command key + expires_at | C freeze/approve | IR-05 HTTP capability, policy-derived expiry, live validation; не client defaults |
| Отозвать разрешение | approval ref, exact ownership/epoch | C revoke | IR-05 safe replay/result; не означает cancel receipt |
| Ожидание | PendingDispatch exact revision/approval/job + business_state | Foundation/C request_dispatch | IR-06 связанная read projection, отсутствие job не failure |
| Выполнение | action/reservation/current execution + transport job status | Shared DispatchBinding и C T2 | IR-06 polling/subscription sequence; не доказывать action по одному job |
| Успех | APPLIED ActionReceipt exact action/approval/seal + Task current pin/status + audit refs | C execute возвращает receipt ref, B project_receipt | IR-07 atomic outcome read / projection lag; нет автоповтора Task |
| Ошибка без эффекта | Authoritative rollback/absence outcome + safe reason | C rollback не создаёт attempt receipt | IR-07 нельзя фабриковать NOT_APPLIED receipt/label из timeout |
| Неизвестный результат | Отсутствие authoritative outcome, reconciliation state/capability | C блокирует UNKNOWN, external reconcile не реализован | IR-07 UX projection, не новый enum/endpoint в продукте |
| Отменить задачу | New task.internal.cancel envelope + compensates_action_ref + Task expected record_version/status + new approval | C cancel guards / CancelTaskPayload | IR-08 capability/domain read; external/finance dependencies → deny |
| История | Actor/time/subject+pin/event/approval/receipt/correlation + Context supersession | Existing append_audit / AuditExtension | IR-09 reader ACL/pagination; current enum не отличает каждый evidence review decision |
| Поздний ответ/reload | actor/tenant/project/message/action/pin/request epoch + server current projection | RequestScope/Resolution/pins, context CAS | IR-06 browser request generation и transport binding; не менять selection из ответа |

## Запросы интегратору

### IR-01 — read model входящего

Версионированный authorized read snapshot: source origin, sender/subject/attachment,
доступные действия и metadata. Legacy unresolved / required origin project из B
не скрывать за «уже привязано». Read-only projection не новое хранилище.

### IR-02 — evidence и fragment

A whole_object/fixture и deny fragment не дают право показать цитату.
Нужны server fragment authorization/reader/locator granularity, safe statuses,
реальные extractor/model/prompt versions при наличии, assessment CAS и reviewer
capabilities. При недоступности metadata не раскрывать даже existence/title.
Политики TTL/retention/residency утверждает владелец, не UX.

### IR-03 — context selection / CAS

Нужны список разрешённых candidates и contract↔project validation; projection
обеих relation revisions/record_versions и Message.context_version; атомарный
confirm/correct и безопасный 409. UI не угадывает revision=1 из fixture.
Начальная source-origin projection B не равна user confirmation.

### IR-04 — claim review / correction

Выдать exact claim pin/verification/source evidence/precision и доступную команду
ReviewCommand. Correction — новый claim revision, explicit reason/provenance,
не input date в approval modal. Time-specific extraction без target support
блокировать. Отдельно решить отклонение claim и запрос новой extraction.

### IR-05 — freeze/approval/capabilities

Нужны trusted preview exact envelope/hash, actor permissions, account/source/policy
binding, server clock, policy expiry и безопасная выдача command keys. UI не
создаёт approver identity, не назначает постоянный TTL и не считает fingerprint
из demo валидным seal. После semantic change старый grant invalidated.
Неподдерживаемое действие → видимое disabled + причина, а не обход gate.

### IR-06 — статус, повтор, reload и смена проекта

Нужен read по стабильному action/command identity с actor/project bindings,
монотонной версией ответа и допустимым polling/subscription. До первого чтения
после reload никакой mutation и никакой уверенной success projection.
Поздний response не меняет active selection, actor или права новой карточки.
При сетевом timeout повторяется чтение/тот же авторизованный command, не новый key.
Замыкать кнопки на immutable request snapshot, не на текущий global project ref.

### IR-07 — бизнес-результат и безопасные ошибки

Server truth должен различать applied receipt, committed effect при задержанной
projection, доказанный no-effect и пока unknown. Job completed/failed недостаточны.
Не добавлять NOT_APPLIED/UNKNOWN receipt в существующую single-receipt схему ради UI.
Для unknown не предлагать кнопку «Создать ещё раз»; reconciliation — отдельная
capability, пока отображать информационный стоп. IR не обещает новый executor.

### IR-08 — compensation

Read capability отмены: существующая Task и expected pin/status, create receipt,
отсутствие external/finance зависимостей. Новые action/grant/receipt.
Если Task changed — перечитать и построить новый cancel preview; не заменять
target pin на latest в уже открытом dialog.

### IR-09 — безопасная история

Нужен authorized audit projection с allowlisted event labels, refs и timestamps;
без source body/quote/secrets. SOURCE_OBSERVED не переводить как «человек проверил».
В A EVIDENCE_REVIEWED не различает confirm/reject в истории: запрос enum/decision
metadata foundation owner. Не выводить историю решений из текущего assessment.

## Не являются запросом на реализацию

Нет расширения scope providers, enterprise policy editor, Company Memory,
автономного исполнения, storage/cache/staging или новой очереди.
Эти IR нужны для последующей интеграции UI, но не разрешают менять контракты
в данной ветке. Доменными writers остаются A/B/C; frontend только клиент.
