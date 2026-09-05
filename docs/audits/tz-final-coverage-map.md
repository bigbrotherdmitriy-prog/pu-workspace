# Полная матрица ТЗ PU Workspace v5.4: код, испытания и незакрытые границы

Дата: 2026-09-05. Проверенная база: `098df263df3b41f40005cdd82b55b90f5e87614d`.
Рабочая ветка: `codex/tz-final-coverage-map`. Изменение только документационное.

## Источник, метод и значение статусов

Авторитетный источник требований — исходный
`PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`, найденный в Downloads
владельца. SHA-256:
`af7bfde75715345e4f32b9d7ca057812cdba7b8d8e0b6a1b105dfe20fc0d5df3`.
Он совпадает с источником [аудита 2026-09-04](v54-tz-coverage-2026-09-04.md).
DOCX прочитан целиком, без изменения, через `word/document.xml`. Обозначения
`p000`…`p578` ниже — нулевые индексы **всех** `//w:body//w:p`, включая пустые
абзацы и ячейки таблиц; текст абзаца — конкатенация его `w:t`. Это воспроизводимые
локаторы, а не номера страниц. Проверены §§1–40 и все ненумерованные вставки
§31. Более новый файл с названием v6 не подменяет порученный источник v5.4.

В титуле DOCX написано «Версия 5.1», внутри присутствуют дополнения v5.4.
Разрешение этой редакционной неоднозначности относится к владельцу (O01), а не
к автоматической смене требований. Позднейшие поручения открыть MVP2–MVP4 не
трактуются как нарушение исторического запрета расширять первый срез; однако
доказательство технической приёмки V1 всё равно требуется.

| Статус | Точное значение в этой матрице |
| --- | --- |
| `implemented+tested` | Указанный **ограниченный контракт** присутствует в коде и имеет проверенные тестовые evidence в текущей истории. Не означает live/production readiness. |
| `implementation gap` | Полного требуемого поведения нет либо найден конкретный несоответствующий путь. Тест отказа/default-off не закрывает реализацию операции. |
| `runtime gate` | Код/тест определения есть, но выполнение нужной проверки на текущем кандидате, нужной СУБД/процессах/браузере не доказано. |
| `owner/legal/live gate` | Требуется конкретное решение владельца, юридическое заключение, эксплуатационная конфигурация или разрешённый реальный тестовый провайдер. |

У строки может быть несколько статусов: они относятся к разным явно названным
частям, а не усредняются. Идентификаторы ниже — канонические группы требований;
повторы в §§24/25/29/30/34/37 и сценариях §31 ссылаются на те же группы. Никакого
общего процента, суммы «готовых функций» или нового denominator не вводится.
Матрица покрывает всё ТЗ, но не утверждает, что несколько сотен фраз документа
являются независимыми одинаково весомыми критериями.

Аудит использует чтение исходников, определений тестов и сохранённых отчётов.
Новые runtime/тестовые прогоны, live-запросы, публикация или изменение продукта
здесь не выполнялись. Отрицательные выводы об отсутствии функции ограничены
проверенной базой и указанным seam; это не утверждение обо всех других ветках.

## Evidence текущей базы и устаревшие выводы

[Итоговое hardening evidence](mvp1234-final-hardening.md) фиксирует на product
SHA `54b33a1e52ff028fbd47e2b286df555b9576e67c`: backend `1610 passed, 27 skipped`,
frontend `208 passed`, Chromium `30 passed` с synthetic API fixtures, CI contracts
`183 passed`. `098df26` добавляет этот отчёт поверх того кода. Это сохранённые
результаты, не новые запуски данного аудита. Единственная заявленная схема базы
— `a54f001c0a18`.

Старый MVP5 PostgreSQL PASS на `8ccc194` остаётся историческим evidence узкого
пилота; он не доказывает runtime расширений текущего кандидата. Подготовленные
[owned PostgreSQL gates](mvp-runtime-owned-postgres.md) не являются выполненными
gates. Параллельные работы runtime/supply после `098df26` должны быть добавлены
отдельной актуализацией с SHA, протоколом и фактическим результатом.

Следующие прежние формулировки уже нельзя переносить как текущий статус:

- «MVP3/MVP4 — BACKLOG»: есть интегрированные management/finance/supply контуры;
  остаются конкретные пробелы и gates ниже.
- «Gmail full resync максимум 100 сообщений»: теперь 100/page, 100 pages/run,
  10 000 raw references/run; бесконечное восстановление не реализовано.
- «legacy Gmail/Tasks/Calendar routes ещё синхронные»: product UI/API переведены
  на durable ProviderAction; наличие старых helper-функций не означает их
  использование новыми routes.
- «MVP3 browser E2E ещё не запускался»: synthetic Chromium принят позднее;
  реальные API/провайдеры этим не проверены.
- «safe live adapters осталось только включить»: exact provider precondition
  и managed-copy capabilities **не реализованы полностью**, поэтому hard-deny
  содержит implementation gap, а не только разрешение на live-тест.

## Индекс полного текста: ни один раздел не исключён

| Раздел / локаторы | Канонические строки ниже |
| --- | --- |
| Титул p000–005 | C01, O01, A01, A02 |
| 1 p006–009 | C01, C02, M201, A03 |
| 2 p010–012 | C01, C03, M308, F01–F08, A03 |
| 3 p013–024 | M201–M211, P01–P05, A04 |
| 4 p025–044 | MVP1/MVP2/MVP3/MVP4, A01–A11, O02 |
| 5 p045–047 | D01–D12, R01, R03 |
| 6 p048–050 | M201–M210 |
| 7 p051–069 | C02, C04, R04 |
| 8 p070–108 | C01, C03, C05, M303, M308, A03 |
| 9 p109–138 | D02, D03, D08, D11 |
| 10 p139–141 | D01, D04, D07, D09 |
| 11 p142–146 | M201, M206–M210 |
| 12 p147–148 | D05 |
| 13 p149–183 | D06, D10, D12, M309 |
| 14 p184–186 | D13–D15, R06 |
| 15 p187–196 | D06, P01–P03 |
| 16 p197–209 | P04, P05, A02, O03 |
| 17 p210–223 | U01–U04, M304, M309 |
| 18 p224–225 | D07–D09 |
| 19 p226–228 | C05, T04, R04 |
| 20 p229–245 | R01–R03, U03, M210 |
| 21 p246–247 | C02, R04, R05, O03, O04 |
| 22 p248–251 | C04, C06, A01, A04 |
| 23 p252–254 | R01–R07; D/M/T acceptance crosswalk |
| 24 p255–269 | Таблица MVP1 acceptance |
| 25 p270–280 | Таблица MVP2 acceptance |
| 26 p281–282 | A04 |
| 27 p283–292 | F01–F08 |
| 28 p293–309 | R07, C02, C06, P03–P05, O03–O05 |
| 29 p310–312 | D01–D09, R01, R03 |
| 30 p313–315 | M201–M208; отправка не обязательна для этого раннего среза |
| 31 p316–386 | A01–A11, T01–T13, O01–O03 |
| 32 p387–417 | O02; Product Scope не отменён ограничением исторического V1 |
| 33 p418–425 | D01–D15, O02; full physical copy и high-risk AUTO не обязательны |
| 34 p426–441 | Таблица V1 DoD/backlog |
| 35 p442–464 | P01–P05, M202–M208, R06 |
| 36 p465–472 | C06, M201–M208, P03, A01 |
| 37 p473–558 | Таблица V1 DoD/backlog, все V1-01…V1-20 |
| 38 p559–566 | O02, R07; минимальные срезы, audit-first, сохранение работающего кода |
| 39 p567–575 | D07–D09, C05, R03, O02; ADR при архитектурном блокере |
| 40 p576–578 | O02, R07; реальный V1 нельзя заменить synthetic PASS |

## Ядро, сущности, права и API

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| C01 — Project Context, Organization/Project/Contract/Document/Message/Task и самостоятельные связи (§§1/2/8) | `implemented+tested` для имеющихся контуров: [models](../../backend/app/models/), [context service](../../backend/app/context_communication/service.py), [MVP3 acceptance](../../backend/tests/test_mvp3_management_acceptance.py), [finance acceptance](../../backend/tests/test_mvp4_financial_acceptance.py). Полная типизация связей — A03. | Не переписывать ядро; добавить отсутствующие типы связей по A03 отдельными вертикальными срезами. |
| C02 — вся ролевая матрица, org isolation, delegated admin без самоповышения (§7) | `implementation gap`: [auth.ROLE_LEVEL / require_project_role](../../backend/app/core/auth.py) содержит viewer/member/editor/manager/owner и общий `is_admin` bypass; [access.add_project_member](../../backend/app/api/access.py) допускает admin назначать owner. Это не эквивалент отдельным approver/service/delegated-admin scope из ТЗ. Строгий v5.4 контур — [v54_permissions](../../backend/app/core/v54_permissions.py), [authority tests](../../backend/tests/test_v54_authority.py). | Зафиксировать совместимый permission mapping и первым regression доказать, что технический admin без business authority не назначает себе owner/approve; затем по одному endpoint переводить legacy path. |
| C03 — UUID, organization_id, created/updated, record_version, инициатор у каждой бизнес-сущности (p108) | `implementation gap`: [Project](../../backend/app/models/project.py) имеет integer id и не имеет created/updated/creator; [Document](../../backend/app/models/document.py) также integer id, org scope через project и неполный lifecycle; [Task](../../backend/app/models/task.py) сохраняет Google-specific legacy ID. Новые v5.4 UUID не исправляют это глобально. | Инвентаризация схемы + ADR additive public UUID/explicit scope/audit metadata и совместимость старых integer APIs; первый bounded объект Project. Не массовая смена всех PK. |
| C04 — view/download/edit/approve/execute/rule/users/audit/rollback/task/event/send проверяются сервером | `implemented+tested` для точных v5.4 action/finance scopes; `implementation gap` для единой capability matrix всех legacy routes: [auth](../../backend/app/core/auth.py), [API routing](../../backend/app/main.py), [v54 authority tests](../../backend/tests/test_v54_autonomy_authorization.py). | Таблица route × permission × role × scope и негативные тесты сначала download/export/approve/manage_users; не переносить локальный v5.4 PASS на весь API. |
| C05 — полный AuditEvent actor/UTC/org/before-after/source/correlation/result/code (§19) | `implementation gap`: legacy [AuditLog](../../backend/app/models/audit_log.py) хранит action/entity/details/created_at; actor/org/correlation/before-after не обязательны. [Action Ledger](../../backend/app/models/v54_pilot.py) и новые histories покрывают свои объекты. | Совместимый structured audit writer; первым мигрировать legacy role change/export и проверить actor+scope+correlation без PII. Сохранить старые записи, не фабриковать отсутствующие поля. |
| C06 — единый versioned API, весь ресурсный каталог, errors/pagination/filter/sort/idempotency/progress (§22/36) | `implementation gap`: [main](../../backend/app/main.py) смешивает legacy routes и v2/v54; [contracts-and-errors](../contracts-and-errors.md) — описание, не полный executable OpenAPI compatibility contract. Storage exceptions нормализованы частично. | Снять OpenAPI baseline текущих resources, описать явные aliases вместо переименования всего API; один типизированный safe error envelope/correlation middleware с compatibility tests. |

## MVP1: документное ядро

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| D01 — OAuth, account/scopes, Google Drive, одна/несколько папок любой глубины | `implemented+tested` offline: [google_drive API](../../backend/app/api/google_drive.py), [storage binding tests](../../backend/tests/test_storage_binding_validation.py), [report](mvp1-storage-acceptance.md); `owner/legal/live gate` для реального OAuth/picker. | Один разрешённый test account: account/scopes, nested/multi-select, revoke/reconnect, safe receipt; не production credentials. |
| D02 — snapshot source IDs/name/path/parents/MIME/size/time/revision/hash/ACL/URL/status; virtual tree без обязательной копии | `implemented+tested` ограниченного metadata path: [workspace API](../../backend/app/api/workspace.py), [acceptance](../../backend/tests/test_mvp1_acceptance.py). **`implementation gap` полного snapshot envelope**: [VirtualNode](../../backend/app/models/workspace.py) хранит id/parent/name/MIME/type/size/checksum/modified_at, но не отдельные exact provider revision, ACL/availability, original URL/path и per-node analysis state из p110–119. Внешний v5.4 source ledger не доказывает автоматический pin каждого snapshot node. | Additive snapshot metadata manifest для одного provider: каждое p110–119 поле mapped/explicit unknown, immutable provider version/ACL reference; binary/native/shortcut fixtures и затем live validation. |
| D03 — immutable refresh, incremental changed-only, persistent progress/restart | `implemented+tested`: [storage report](mvp1-storage-acceptance.md), [wave2](mvp1234-wave2-integration.md), [performance contract](../../backend/tests/test_mvp1_storage_performance_contract.py); `runtime gate` process/storage tests R01/R03. | На PostgreSQL перезапустить API/worker между snapshot и continuation, проверить неизменность старого snapshot и отсутствие повторного extraction неизменных объектов. |
| D04 — Shared Drives, shortcuts, paging/retry/rate limit и native metadata (§10) | `implemented+tested` части Drive wrapper, [DriveClient](../../backend/app/organizer_engine/drive.py); `owner/legal/live gate` всей provider matrix. | Test account с shared drive/shortcut: readonly bounded tree, deny/revoke/429, no cycle; зафиксировать неподдерживаемую capability до эффекта. |
| D05 — native Docs→DOCX/PDF, Sheets→XLSX/CSV, Slides→PPTX/PDF, cache revision/hash/TTL (p148) | `implementation gap`: [DriveClient.GOOGLE_EXPORTS](../../backend/app/organizer_engine/drive.py) использует text/plain, text/csv, text/plain; не доказывает весь native document/multisheet cache contract. [DocumentVersion](../../backend/app/models/document_version.py) не заменяет provider export manifest. | Первым Sheets→XLSX: immutable source revision/export MIME/hash/time/TTL record, все листы, explicit cache invalidation и stale source test. Далее Docs/Slides форматы. |
| D06 — deterministic классификация, шаблон 00–09/99, standard name, rules precedence и ручные правки (§13/15) | `implemented+tested` текущего naming/classifier: [config](../../backend/app/organizer_engine/config.py), [naming](../../backend/app/organizer_engine/naming.py), [classifier](../../backend/app/organizer_engine/classifier.py), [MVP1 acceptance](../../backend/tests/test_mvp1_acceptance.py). `implementation gap` точного полного precedence policy→project rule→org rule→manual→metadata→AI→default. Naming сохраняет дополнительный original stem; это не буквальный p183. | Один versioned precedence evaluator и table-driven conflict tests. Явно согласовать расширение naming с исходным stem, не удалять пользовательские имена молча. |
| D07 — preview→dry-run→exact approval→source recheck→rename/move | `implemented+tested` synthetic saga; **`implementation gap` live path**: [storage_mutation_live](../../backend/app/integrations/storage_mutation_live.py), [DriveClient](../../backend/app/organizer_engine/drive.py) `supports_exact_mutation_preconditions=False`, [readiness report](mvp1-storage-live-adapter-ready.md). | ADR допустимого provider concurrency/precondition contract и один доказанный client implementation. Пока невозможна требуемая атомарность — сохранить denial и честный unsupported, не включать flag ради теста. Затем отдельный live gate. |
| D08 — operation idempotency, durable outcomes, partial failure/retry, conflict-name без overwrite | `implemented+tested` synthetic: [runtime](../../backend/app/organizer_engine/storage_mutation_runtime.py), [repository](../../backend/app/organizer_engine/storage_mutation_repository.py), [runtime tests](../../backend/tests/test_mvp1_storage_mutation_runtime.py); `runtime gate` реальных процессов/БД, `owner/legal/live gate` provider reconciliation. | Kill after provider effect/before receipt на permitted sandbox; UNKNOWN→lookup, только незавершённые operations, без blind retry. |
| D09 — compensating rollback, conflict_source_changed, immutable history (§18/24/34) | `implemented+tested` synthetic: [mutations](../../backend/app/organizer_engine/storage_mutations.py), [acceptance](../../backend/tests/test_mvp1_storage_mutation_acceptance.py); `implementation gap` D07 live adapter. | После D07 один rename→rollback и concurrent source-change rejection на реальном test object, с отдельным approval и receipt. |
| D10 — серверная пагинация, виртуализация, bulk selection/filter/conflicts/errors на 10k | `implemented+tested` части picker/search/progress; `runtime gate` полного 10k UX, [performance contract](../../backend/tests/test_mvp1_storage_performance_contract.py), [DocumentsModule](../../frontend/src/modules/documents/DocumentsModule.tsx). | Измеримый 10k tree/browser scenario: bounded DOM/response, multi-select, per-object status, changed-only filter; при отсутствии virtualized renderer — отдельный UI implementation, не тестовый лимит. |
| D11 — optional sandbox/full-copy; безопасные managed copies и cleanup | `implemented+tested` worker fencing; **`implementation gap` live ownership/idempotent copy/reconciliation**: [managed_copies](../../backend/app/organizer_engine/managed_copies.py), [cleanup report](mvp1-cleanup-worker-fencing.md), [copy lifecycle](mvp1-managed-copy-lifecycle.md). | Provider receipt/ownership tree manifest и descendant-original exclusion; crash/reconcile одного managed subtree, затем cleanup. Full tree copy не обязательна для V1, но доступная UI-команда не должна обещать неработающую операцию. |
| D12 — metadata/DocumentVersion, dedup/version comparison/source retention | `implemented+tested`: [document engine](../../backend/app/document_engine.py), [versions](../../backend/tests/test_document_version_comparison.py), [document engine tests](../../backend/tests/test_document_engine.py); полная universal identity — C03. | Сохранить existing source links; расширенный provider-native revision test относится к D05, а не к изменению бизнес-ID. |
| D13 — PDF/DOCX/TXT/MD/JPG/PNG/WEBP; signature/size/unzip limits, no macro/link execution | `implemented+tested` bounded extraction/staging: [content](../../backend/app/organizer_engine/content.py), [local upload staging](../../backend/app/staging/local_upload.py), [staging hardening](v54-staging-safety-hardening.md); `runtime gate` проверка всех production ingress путей R04/R06. | Одна общая negative corpus matrix для extension≠signature, zip bomb, truncated file, malicious external references; явно доказать тот же validator на Drive/Gmail/local входах. |
| D14 — XLSX все листы, формула отдельно от cached value, точные sheet/cell links (p186) | **`implementation gap`**: [content._xlsx_text](../../backend/app/organizer_engine/content.py) обходит sheets, читает `<v>`, игнорирует `<f>`, складывает текст строк без отдельной durable formula/value модели. [Fragment locator](../../backend/app/source_evidence/fragment_reader.py) уже различает `formula/cached_value/displayed_value`, но locator не сохраняет потерянную при извлечении формулу. | Typed cell extraction `{sheet, cell, formula, cached_value}` + multi-sheet/formula/missing-cache fixtures; никакого вычисления макросов/внешних ссылок. Использовать существующий extraction/Evidence seam. |
| D15 — PDF text-first/OCR-policy, images vision/OCR/both, incomplete reason | `implemented+tested` локального Tesseract/OCR review: [content](../../backend/app/organizer_engine/content.py), [OCR benchmark](v54-ocr-benchmark-gate.md); `implementation gap` полного policy-routed alternative vision/both через общий AI adapter. | Сначала честно опубликовать поддерживаемые modes/capabilities; второй vision adapter только после P04/A02 и отдельной corpus acceptance. |

## MVP2: AI Secretary и внешние действия

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| M201 — permitted message/document ingress, raw/source identifiers, mailbox dedup | `implemented+tested`: [gmail API](../../backend/app/api/gmail.py), [mailbox runtime](../../backend/app/mailbox_identity/runtime.py), [history acceptance](../../backend/tests/test_mvp2_gmail_history_acceptance.py); `owner/legal/live gate`. | Test mailbox duplicate delivery→one Message/origin, current generation and actor scope, attachment source links. |
| M202 — project/contract/party candidates, confidence, human correction, no durable low-confidence links | `implemented+tested`: [AI Secretary](../../backend/app/api/ai_secretary.py), [contacts](../../backend/app/api/project_contacts.py), [MVP2 report](mvp2-completion.md), [contact tests](../../backend/tests/test_mvp3_contact_resolution.py). | Runtime acceptance на реальной роли/БД по R01; отдельная Company сущность — M310. |
| M203 — summary, fact/request/obligation/date/amount/risk/assignee extraction with evidence | `implemented+tested` ограниченных extractors: [task engine](../../backend/app/task_engine.py), [summary](../../backend/app/summary_engine.py), [governance](../../backend/app/governance_engine.py), [corpus](../acceptance/v54-corpus/manifest.json). **`implementation gap` единых обязательных exact Evidence и model/prompt lineage во всех legacy результатах**, P01/P03. | Взять один legacy task/deadline extractor и пропустить результат через canonical claim/Evidence validation; raw excerpt сам по себе не exact version pin. |
| M204 — editable draft; subject/body/recipient edit invalidates approval | `implemented+tested`: [responses API](../../backend/app/api/responses.py), [MVP2 report](mvp2-completion.md), [provider E2E](../../backend/tests/test_mvp2_provider_e2e_acceptance.py). | Реальная authenticated browser→API проверка по U02; отправка отдельно M206. |
| M205 — task/event proposals, default human confirmation, strict exact payload | `implemented+tested`: [provider product](../../backend/app/provider_actions/product.py), [outbox report](mvp2-provider-outbox.md), [E2E tests](../../backend/tests/test_mvp2_provider_e2e_acceptance.py). | Не повышать автономность; доказать real timeout/reconcile отдельно M206/T05. |
| M206 — Gmail send / Tasks create+update / Calendar create+update once, external ID/audit | `implemented+tested` durable outbox/receipt/UNKNOWN: [product adapter](../../backend/app/provider_actions/product.py), [controls](mvp2-provider-controls.md); **`owner/legal/live gate` S10**. | Один selected provider sandbox: timeout-after-effect, lookup, exactly one observed effect, revoke token/approval between enqueue and execute, sanitized receipt. |
| M207 — external Task status / Calendar changes обратно в core с audit и timezone (§11/36) | **`implementation gap`**: [google_calendar.event_payload](../../backend/app/google_calendar.py) формирует all-day `date`; [product](../../backend/app/provider_actions/product.py) upsert/lookup, а не general inbound delta sync. [google_tasks](../../backend/app/google_tasks.py) outbound helper; reconciliation lookup не реализует inbound lifecycle. | Сначала Calendar read-delta→proposal correction with external version/ETag and timezone; никакой молчаливой перезаписи internal human change. Затем Task status mapping + pagination/replay. |
| M208 — source→Project/Contract→Task/Event links; AI outage не теряет Message | `implemented+tested` offline: [AI Secretary ingestion](../../backend/app/api/ai_secretary.py), [external resources](../../backend/app/integrations/external_resources.py), [MVP2 completion](mvp2-completion.md); `runtime gate` полная outage/manual UX. | API/worker fault test отключения AI после сохранения source: durable Message остаётся, ручной edit/review доступен, retry не дублирует proposals. |
| M209 — attachment staging/current mailbox authority/recovery | `implemented+tested`: [Gmail staging](../../backend/app/staging/gmail.py), [attachment tests](../../backend/tests/test_v54_gmail_attachment_staging.py); `runtime gate` PostgreSQL/process recovery и `owner/legal/live gate` real attachment. | Один benign attachment, kill/reclaim/lease+credential rotation, exact source/version, encrypted temp and terminal cleanup proof. |
| M210 — Gmail checkpoint/history/404 full resync >100 | `implemented+tested`: [gmail_history](../../backend/app/gmail_history.py), [paged tests](../../backend/tests/test_mvp2_gmail_paged_resync.py), [report](mvp2-gmail-paged-resync.md). 100/page, 100 pages, 10k refs/run; partial retry starts at page one. `runtime gate` cursor PG; `owner/legal/live gate` Gmail history. | Реальный cursor expiry/new arrivals/replay на test mailbox. Если обязательна mailbox scope выше 10k или не укладывающаяся в lease — отдельный partitioned durable recovery design; повтор на том же лимите проблему не закрывает. |
| M211 — вопросы по проекту с источниками (p022), missing-doc/contradiction/version checks (p021) | `implementation gap` полноценного Q&A: [analytics API](../../backend/app/api/analytics.py) aggregates, [search](../../backend/app/mvp3/search.py) выдача, [knowledge UI](../../frontend/src/modules/documents/DocumentsModule.tsx) document search. Contract/version checks имеются, но это не evidence-cited question-answer API. | Один readonly question→bounded authorized sources→answer/citations/unverified refusal endpoint; negative cross-project/stale/no-evidence corpus. Никаких новых execution rights. |

## AI quality, правила и privacy

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| P01 — no invented date/numbers, meaningful claims evidence-backed, low confidence review (§35) | `implemented+tested` пилот: [v54 corpus](../acceptance/v54-corpus/manifest.json), [product acceptance](../../backend/tests/test_v54_product_acceptance.py); `implementation gap` универсального ingress enforcement, M203. | Применить одни exact Evidence/number/date validators к одному legacy ingress и показать отсутствие materialized action без достаточного evidence. |
| P02 — факты отдельно от рекомендаций, confidence/features/reason/type; human edit не global rule | `implemented+tested` parts: [summary](../../backend/app/summary_engine.py), [classifier](../../backend/app/organizer_engine/classifier.py), [AI Secretary](../../backend/app/api/ai_secretary.py); `runtime gate` размеченный quality acceptance, `implementation gap` единой output schema. | Один structured proposal schema: fact/interpretation/recommendation, evidence/unknown; corpus на смешанные факты/догадки и подтверждаемое создание rule. |
| P03 — provider/model/prompt version/time/mode у каждого AI результата; versioned rollback/evals | `implementation gap`: [ProjectAIPolicy](../../backend/app/models/ai_policy.py), [Gemini adapter](../../backend/app/integrations/ai.py), [contracts doc](../contracts-and-errors.md) не обеспечивают обязательный lineage каждого legacy extractor result. | Immutable analysis-run manifest + versioned prompt registry для одного workflow, structured-output failure metrics и сравнение corpus до/после. |
| P04 — org-wide local_only/external_allowed/redacted/metadata_only и запрет egress | **`implementation gap`**: [ai_policy](../../backend/app/ai_policy.py) действует по project, default `external_allowed`; [ProjectAIPolicy](../../backend/app/models/ai_policy.py) не organization policy. Local block одного helper не доказывает all-call-site egress barrier. | Versioned organization policy с запретом переопределения project/channel/worker; один outbound AI boundary, fail-closed default/negative tests всех вызовов. Existing user choices мигрировать явно. |
| P05 — DLP ФИО/phone/email/bank/INN/amount/contract/address, stable tokens, no secret/content logs | **`implementation gap`**: [SENSITIVE](../../backend/app/ai_policy.py) только EMAIL/PHONE/INN, `dlp_enabled` не полноценный DLP policy engine. Тексты и технические логи требуют R04. | Один typed data-class policy/redaction manifest, добавить contract/bank/address/name/amount corpus с false-positive cases; явно сообщать unsupported policy и не отправлять её content. |

## MVP3: управленческий контур

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| M301 — Obligation exact Evidence, owner/deadline/status/CAS/history | `implemented+tested`: [lifecycle](../../backend/app/mvp3/lifecycle.py), [foundation](mvp3-foundation-result.md), [acceptance](../../backend/tests/test_mvp3_management_acceptance.py); `runtime gate` текущей PG CAS. | Выполнить mandatory isolated PG transaction test, сохранить exact SHA/zero skips. Legacy mutations — M311. |
| M302 — Task из подтверждённого obligation, mapping, ручная history | `implemented+tested`: [lifecycle](../../backend/app/mvp3/lifecycle.py), [management acceptance](../../backend/tests/test_mvp3_management_acceptance.py); inbound provider lifecycle — M207. | Соединить provider-neutral external status proposal с существующим internal CAS после M207, не создать второй Task registry. |
| M303 — deadlines/timezone/reminders/quiet hours и escalation | `implemented+tested` date/time/policy/digest; **`implementation gap` durable escalation**: [Obligation](../../backend/app/models/management.py) поля `escalation_level/last_escalated_at`, [attention](../../backend/app/mvp3/attention.py) derived severity; [limitations](mvp3-management-acceptance.md). | Idempotent scheduler transition одной подтверждённой просрочки с escalation policy version/history, quiet-hours/role recheck; display-critical не считать escalated fact. |
| M304 — Risk/Decision confirmed evidence/status/owner/mitigation/relations | `implemented+tested`: [lifecycle](../../backend/app/mvp3/lifecycle.py), [governance models](../../backend/app/models/governance.py), [management acceptance](../../backend/tests/test_mvp3_management_acceptance.py); `runtime gate` PG+real API UI. | R01/U02 на этих v2 endpoints; legacy extraction integration M311. |
| M305 — meeting/message → proposals → human confirmation | `implemented+tested` structured candidates; **`implementation gap` meeting source provenance**: [MeetingProposalService](../../backend/app/mvp3/meeting_digest.py), [digest limitations](mvp3-digest-preferences.md): у Meeting нет durable SourceReference протокола, project Evidence не доказывает происхождение из этой встречи. | Связать Meeting с exact source/version, затем валидировать каждый candidate pin against origin; arbitrary same-project Evidence должно отклоняться. |
| M306 — attention dashboard/filter/pagination/explainability | `implemented+tested`: [attention](../../backend/app/mvp3/attention.py), [management UI report](mvp3-management-ui.md), [browser report](mvp3-management-browser-e2e.md); `runtime gate` real API/a11y. | U02; отдельно projection pending approvals/conflicts, если их нет в выбранном attention query, с bounded mixed-kind page test. |
| M307 — notifications/digest/user preferences/scheduler | `implemented+tested` `in_app/disabled`, daily/weekdays/quiet-hours/CAS: [meeting_digest](../../backend/app/mvp3/meeting_digest.py), [preferences](mvp3-digest-preferences.md); `runtime gate` PG scheduler race. `implementation gap` выбранного внешнего delivery adapter. | Сначала PG recovery; если owner выбирает внешний канал — one delivery action with idempotent receipt, no raw content queue, then live gate. |
| M308 — Contract versions/amendments/history/sources/archive/relations | `implemented+tested`: [organization contracts API](../../backend/app/api/organizations_contracts.py), [versions tests](../../backend/tests/test_mvp3_contract_versions.py), [report](mvp3-contract-versions.md); `runtime gate` migration/CAS/lifecycle restore. | Verify current migration on PG and concurrent edits; project deletion/retention history semantics принять отдельно O04. |
| M309 — shared search/saved filters: name/type/date/project/contract/counterparty | `implemented+tested`: [search](../../backend/app/mvp3/search.py), [scope performance tests](../../backend/tests/test_mvp3_scope_performance_acceptance.py). `runtime gate` 10k performance, bounded search truncation. | Зафиксировать видимое `incomplete` при budget overflow и измерить real DB plans/latency; FTS только если измерен bottleneck, не новый search service по умолчанию. |
| M310 — Company/Person lifecycle и reusable confirmed contact resolution | `implemented+tested` ProjectContact/email/mailbox history; **`implementation gap` самостоятельной Company модели**: [ProjectContact.company](../../backend/app/models/project_contact.py) — строка, не Company lifecycle. | ADR business Company/Party identifiers, первый lifecycle create/confirm/correct/merge с provenance; подтверждённые contacts не терять. |
| M311 — единый v2 evidence/CAS путь для старых management ingress/mutations | **`implementation gap`**: [management API](../../backend/app/api/management.py) содержит legacy и v2; [foundation limitations](mvp3-foundation-result.md) прямо сохраняют legacy mutations без mandatory expected version. | Перевести один legacy Obligation write/extractor на v2 lifecycle, regression stale version/unknown Evidence; затем последовательно Risk/Decision. |

## MVP4: исполнение, финансы и снабжение

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| F01 — contract clause/payment/stage evidence и approved immutable GPR baseline | `implemented+tested`: [contract evidence](../../backend/app/contract_evidence.py), [GPR baseline tests](../../backend/tests/test_mvp4_gpr_baseline.py), [evidence bridge](mvp4-evidence-ingestion-bridge.md); `runtime gate` PG integration. | Выполнить same-baseline competing approval/edit test и source-change validation на migrated schema; не засчитывать approve fake object. |
| F02 — plan/fact progress, baseline version, schedule history | `implemented+tested` scoped: [execution finance API](../../backend/app/api/execution_finance.py), [GPR report](mvp4-gpr-baseline.md), [financial acceptance](../../backend/tests/test_mvp4_financial_acceptance.py); `runtime gate`. | Concurrent fact update + baseline immutability + restore fact/history, не переписывать baseline при пересчёте. |
| F03 — budget/DDS/calendar links/revenue/outgoing plan/fact | `implemented+tested` текущего implicit-RUB контура: [finance API](../../backend/app/api/execution_finance.py), [budget tests](../../backend/tests/test_mvp4_budget_dds.py); **`owner/legal/live gate` currency/VAT/retention**, F08. | Подтвердить policy текущего назначения регистра, PG runtime/restore; multi-currency не «починить» сложением разных валют. |
| F04 — invoice ≠ paid; explicit human payment fact/correction/audit | `implemented+tested`: [finance acceptance](../../backend/tests/test_mvp4_financial_acceptance.py), [payment foundation](mvp4-payment-safety-foundation.md); `runtime gate`: [PG tests](../../backend/tests/test_mvp4_finance_postgres_runtime.py). | Реальные competing confirm/correction transactions, one fact/history, zero skipped; bank/API не требуется и не даётся этому внутреннему тесту. Legal meaning — O06. |
| F05 — request/order/approval/supplier/delivery/quantities/discrepancies/acts | `implemented+tested` внутреннего workflow: [supply service](../../backend/app/mvp4/supply/service.py), [supply tests](../../backend/tests/test_mvp4_supply_acts.py), [forms](mvp4-supply-verified-forms.md); **`runtime gate` supply concurrency**, не покрытая finance confirmation test. | Первый PG competing delivery/act approval и duplicate supply command на migrated schema; one fact/history и unchanged financial fact. Параллельный supply поток не считать принятым до его SHA/evidence. |
| F06 — source-verified supply→DDS proposal, contract/stage/document links | `implemented+tested` поздней интеграцией: [supply DDS proposal](mvp4-supply-dds-proposal.md), [final hardening](mvp1234-final-hardening.md), [supply service](../../backend/app/mvp4/supply/service.py). Proposal не payment/posting; `runtime gate`. | PG duplicate proposal and currency/source-version conflict; approval act не превращать в автоматическую оплату. |
| F07 — explainable schedule/cash-gap forecast, actuals/evidence/model version | `implemented+tested` advisory: [forecast engine](../../backend/app/execution_forecast/engine.py), [tests](../../backend/tests/test_mvp4_explainable_forecast.py); `owner/legal/live gate` валидации assumptions/model для реального бизнеса. | Владелец принимает один anonymous historical benchmark/assumption policy; прогноз остаётся `can_trigger_actions=false`, не создаёт платежа. |
| F08 — валюты/НДС/удержание/partial/overpayment/retention-release | `owner/legal/live gate` решения; **`implementation gap` versioned policy/multi-currency storage**: [finance guards](mvp4-finance-decision-guards.md), [finance models](../../backend/app/models/execution_finance.py). Non-RUB proposals blocked/excluded, это безопасный отказ. | После owner/accounting policy — один additive currency/rounding/evidence slice; до решения сохранить `decision_required`, no conversion/payment. |

## MVP5: пилот Trust Loop и все приёмочные сценарии §31

Здесь `implemented+tested` относится к contract/synthetic реализации. Исторический
PG PASS 13 критериев на `8ccc194` описан в [старой матрице](v54-tz-coverage-2026-09-04.md).
Для текущей базы каждому пункту также принадлежит **`runtime gate` R01**;
live-effect сценарии дополнительно требуют M206/D07/D11. Таким образом ни один
test double не называется завершённым live workflow.

| ID / исходный критерий | Код/тест evidence | Следующий bounded gate |
| --- | --- | --- |
| T01 — p380 Communication-to-Action end-to-end; p357 A | `implemented+tested`: [context service](../../backend/app/context_communication/service.py), [product acceptance](../../backend/tests/test_v54_product_acceptance.py), [corpus C01](../acceptance/v54-corpus/manifest.json). | R01 exact-current-SHA runtime, затем M209+M206 test mailbox attachment→confirmed task/reply. |
| T02 — p380 Context Graph pilot; p358 correction C | `implemented+tested`: [context service](../../backend/app/context_communication/service.py), [context contract](../architecture/v54/context-communication/contract.md). | R01: confirmed correction wins over late hypothesis, stale CAS cannot recreate old context. |
| T03 — p380 critical CONFIRM; p358 D/E | `implemented+tested`: [authority](../../backend/app/core/v54_permissions.py), [authorization tests](../../backend/tests/test_v54_autonomy_authorization.py). | R01: role/generation revoke between approval and T2, no global-admin/service-worker bypass. |
| T04 — p378/p380 full Action Ledger | `implemented+tested`: [v54 models](../../backend/app/models/v54_pilot.py), [product acceptance](../../backend/tests/test_v54_product_acceptance.py). | R01: one success audit plus source/analysis/proposal/approval/receipt links. Не закрывает legacy AuditLog C05. |
| T05 — p380 idempotency/dedup; p358 B | `implemented+tested`: [provider runtime](../../backend/app/provider_actions/runtime.py), [product acceptance](../../backend/tests/test_v54_product_acceptance.py), [fault report](v54-wave3-fault-gaps.md). | R01 process S07/S08, затем live S10 timeout-after-effect lookup (M206). |
| T06 — p380 reversible/compensatable/irreversible | `implemented+tested`: [action trust contract](../architecture/v54/action-trust/contract.md), [product acceptance](../../backend/tests/test_v54_product_acceptance.py). | R01 классификация sealed в payload, compensation новое action; типизация всех future actions при их добавлении. |
| T07 — p383 A deadline exact source/version or unverified | `implemented+tested`: [Evidence product](../../backend/app/source_evidence/product.py), [corpus C01/C07](../acceptance/v54-corpus/manifest.json). | R01 no-evidence/changed source/date ambiguity; legacy universal enforcement P01 отдельно. |
| T08 — p383 B internal Task AUTO / external message CONFIRM | `implemented+tested`: [autonomy policy](../../backend/app/autonomy_policy.py), [policy tests](../../backend/tests/test_v54_autonomy_policy.py). | R01 allowlisted SERVER_POLICY only; organization-wide broader policy A06 не засчитывать. |
| T09 — p383 C changed payload revokes old approval | `implemented+tested`: [provider contracts](../../backend/app/provider_actions/contracts.py), [trust tests](../../backend/tests/test_v54_action_trust_external_contract.py). | R01 exact hash/revision/envelope negative cases. |
| T10 — p383 D reversible Task cancel separate audited action | `implemented+tested`: [product acceptance](../../backend/tests/test_v54_product_acceptance.py), [compensation architecture](../architecture/v54/email-compensation-a08-handoff.md). | R01 new permission/approval/receipt, original result immutable. |
| T11 — p383 E external email no fake undo, corrective follow-up | `implemented+tested`: [responses](../../backend/app/api/responses.py), [Gmail](../../backend/app/api/gmail.py), [product acceptance](../../backend/tests/test_v54_product_acceptance.py). | R01 frozen follow-up with new CONFIRM; live send отдельно M206. |
| T12 — p383 F source remains at client, no forbidden local copy | `implemented+tested` staged/materialization policy: [materialization](../../backend/app/source_evidence/materialization.py), [staging hardening](v54-staging-safety-hardening.md). | R01 dedicated materialization/local-upload fixtures; O03/R05 actual KEK/residency/retention, then live provider source. |
| T13 — p383 G stale/unavailable source explicitly marked | `implemented+tested`: [evidence API](../../backend/app/api/evidence.py), [fragment reader](../../backend/app/source_evidence/fragment_reader.py), [evidence UI report](v54-evidence-product-api-ui.md). | R01 stale/revoked/purged same safe failure; M206/D04 real provider outage. |

## MVP6, 1.0+ и обязательный архитектурный задел

Это Product Scope самого DOCX (p319–380), а не дополнительно придуманные
обязательные интеграции. Конкретный VK/Sber/ERP/etc. определяется редакцией и
roadmap: отсутствие каждого названного примера не отдельный дефект MVP1.

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| A01 — capability contract read/write/search/delta/webhook/attachments/version/ACL/rate/audit/health | **`implementation gap`**: [integration protocols](../../backend/app/integrations/contracts.py) содержат отдельные methods/health; [catalog](../../backend/app/integrations/catalog.py) capability family, а не полный versioned manifest p329. | Versioned capability DTO + conformance fixtures для Google/Yandex read path; core выбирает allowed fallback/deny, не имя провайдера. |
| A02 — заменяемый AI/organization provider or corporate endpoint | **`implementation gap`**: [configured_ai_provider](../../backend/app/integrations/ai.py) всегда Gemini; [AIProviderAdapter](../../backend/app/integrations/contracts.py) seam есть. | Один local/corporate adapter через общий P04 policy/lineage, structured validation/timeout/fallback conformance; deployment O03. |
| A03 — полный Context Graph Project↔Contract↔DocumentVersion↔Clause↔Party↔Task↔Deadline↔Decision↔Change↔Invoice/Payment↔Audit | `implemented+tested` pilot relation model; **`implementation gap` полного graph query/type coverage**: [context service](../../backend/app/context_communication/service.py), [contract](../architecture/v54/context-communication/contract.md), C01/M310/F01. | Добавить один relation type Invoice→Payment with evidence/provenance/confirmation/history и bounded authorized explanation query. Отдельный graph engine не требуется. |
| A04 — Telegram ChannelAdapter без отдельной business logic/прав | `implemented+tested` transport seam [TelegramChannelAdapter](../../backend/app/integrations/telegram.py); **`implementation gap` полной унификации**: [telegram webhook](../../backend/app/api/telegram.py) меняет Task/status/due внутри command branches. | Один Telegram `/done` через тот же core command/permission/audit, что web; parity tests low role/stale/version/replay. Затем file ingress/summaries/links и live gate по выбранному каналу. |
| A05 — federation beyond reference: Yandex family, selected mailbox/calendar/task/enterprise storage | `implemented+tested` Yandex Disk storage; **`implementation gap` выбранных остальных families**: [registry](../../backend/app/integrations/registry.py), [storage](../../backend/app/integrations/storage.py), [catalog](../../backend/app/integrations/catalog.py); O02/O03 выбирают scope. | После A01 один Yandex/VK/Microsoft read adapter с exact identity/versions/ACL; один и тот же core acceptance на двух providers. Не обещать весь каталог до реализации. |
| A06 — organization autonomy matrix action/risk/role/channel/data/confidence/thresholds | `implemented+tested` узкая policy [autonomy_policy](../../backend/app/autonomy_policy.py); **`implementation gap` enterprise simulation/staged rollout and full dimensions**, [policy architecture](../architecture/v54/autonomy-policy-backend.md). | Versioned org policy dry-run/explanation одного нового low-risk action; conflict/rollback tests. High-risk AUTO не требуется и не открывается моделью. |
| A07 — federated source canonical locator/version/sync/last_seen/hash/permissions/residency | `implemented+tested` v5.4 [source models](../../backend/app/models/v54_pilot.py); **`implementation gap` единого sync/conflict handling всех external objects**. | Один beyond-reference source type: delta/poll, unavailable/deleted/stale, exact Evidence resolution/no forbidden copy; затем capability conformance A01. |
| A08 — Cloud/Russian DC/private/on-prem, customer data stays local | `owner/legal/live gate` deployment decisions; **`implementation gap` validated provider-independent/offline enterprise profile**, [provider architecture](../architecture-v5.2-provider-agnostic.md), [deployment files](../../docker-compose.yml), [legal installation](../legal/08_INSTALLATION_RECOVERY_RU.md). | Зафиксировать edition dependency map, local AI/storage profile, clean offline install/upgrade/restore drill; отсутствие Google не ломает разрешённые core operations. |
| A09 — Company Memory provenance/scope/owner/evidence/confirmation/expiry/forgetting | **`implementation gap`**: [context model](../../backend/app/models/v54_pilot.py) и [ProjectContact](../../backend/app/models/project_contact.py) — задел; отдельного CompanyMemory lifecycle/retrieval контракта в base не найдено. | Один подтверждённый memory object type на existing provenance/context, expiry/revoke/tenant-purpose checks и human correction priority. Не обучение на каждой правке. |
| A10 — future Agents use one Trust Loop/ledger, no opaque parallel workflow | `implemented+tested` архитектурный contract [action trust](../architecture/v54/action-trust/contract.md); **`implementation gap` выбранного specialized agent acceptance**, [automation engine](../../backend/app/automation_engine.py) не универсальный agent framework. | Только после business case один bounded agent с purpose/capability/cost/time/evidence/kill-switch; existing BackgroundJob/Trust Loop, no second queue. |
| A11 — richer knowledge center/enterprise analytics/mobile-PWA/background uploads | `implemented+tested` basic [analytics](../../backend/app/api/analytics.py), [DocumentsModule knowledgeMode](../../frontend/src/modules/documents/DocumentsModule.tsx), [local upload](../../backend/app/staging/local_upload.py); **`implementation gap` provenance-first knowledge lifecycle, advanced analytics и resumable offline upload**. | Разделить на независимые slices: A09-backed retrieval; explainable dataset/model version; encrypted resumable upload with session recovery. PWA shell/document search не эквивалент этим требованиям. |

## Runtime, UX, security и обязательные артефакты

| ID / требование | Статус и точное evidence | Следующее ограниченное действие / критерий закрытия |
| --- | --- | --- |
| R01 — migrations/CAS/locks/isolation/current candidate PG | **`runtime gate`**: [owned runtime](mvp-runtime-owned-postgres.md), [runner](../../scripts/ci/v54_pilot_runtime.py); test definitions/CI mock contracts не execution evidence. | Запустить exact final SHA в принадлежащих run isolated DBs; zero skip/xfail/deselection у mandatory cases, schema/head and cleanup proof. Дополнить authority/materialization/generic fixtures, не скрыть их gaps. |
| R02 — 1k≤30s, 10k≤5min, changed-only, bounded concurrency | **`runtime gate`**: [storage performance contract](../../backend/tests/test_mvp1_storage_performance_contract.py) synthetic 2304/provider без wall-clock threshold; [performance smoke](../../backend/tests/test_performance_smoke.py) не live scan benchmark. | Reproducible metadata benchmark per provider and DB; report hardware/network/data/latency distribution, then approved deviation or fix, no threshold removal. |
| R03 — web/API separate worker, checkpoints/retry/timeout/dead-letter/health/restart | `implemented+tested`: [queue](../../backend/app/jobs/queue.py), [durable harness](../../scripts/ci/durable_queue/run.py); **`runtime gate` real process effects on current SHA**. | Graceful shutdown + kill/reclaim before enqueue/after effect/before receipt; storage same-process simulation не считать двумя процессами. |
| R04 — tenant/ACL/secrets/encryption/download/log-content negative security | `implemented+tested` targeted v5.4/staging tests; **`runtime gate` независимого all-ingress review**, C02/C05/P04/P05 gaps. [security report](v54-wave3-security-review.md), [token crypto](../../backend/app/core/token_crypto.py). | Threat-to-route matrix и negative tests current SHA; first validate legacy admin/audit/AI egress findings, verify revoke/rotation/backup exclusion. |
| R05 — backup/restore DB/config/audit, RPO/RTO/retention/keys до production | **`owner/legal/live gate` policy + `runtime gate` actual drill**: [retention](../retention-policy.md), [backup](../../scripts/backup-job-queue.sh), [restore](../../scripts/restore-job-queue.sh), [restore tests](../../backend/tests/test_deploy_restore_verification.py). | Owner numeric targets; isolated clean install→backup→restore→entity/history/hash verification→rollback/cleanup. Script/unit PASS не восстановление всей системы. |
| R06 — OCR/AI gold corpus, quality thresholds, unit/integration/security/perf/UX/recovery | `implemented+tested` [corpus](../acceptance/v54-corpus/manifest.json), historical real local Tesseract [benchmark](v54-ocr-benchmark-gate.md); **`runtime gate` representative corpus/current env**, **`owner/legal/live gate` allowed data/quality thresholds**. | Полная p253 matrix: corrupt/inaccessible/native/sheets/images/ambiguous/rate-limit/rollback; precision/recall incl merged tables, no technical-success-only quality claim. |
| R07 — §28 UX/ER/API/errors/sequences/specs/processing/AI/DLP/rights/load/corpus/backup/retention/backlog | `implemented+tested` docs exist: [architecture](../architecture-v5.1.md), [contracts/errors/sequences](../contracts-and-errors.md), [rights](../contract-role-model.md), [UX](../ux/v54-pilot/contract-map.md), [acceptance](../acceptance-v5.1.md), [retention](../retention-policy.md). **`implementation gap` согласованного executable contract pack current base**, C02/C06/P03–P05. | Один requirements→OpenAPI→DB→sequence→test manifest current SHA, mark each stale/missing item; explicit load/RPO/retention decisions O03/O04. Не считать наличие markdown выполнением семантики. |
| U01 — все ключевые экраны §17: project/contract/inbox/source/run/proposal/document/task/calendar/risk/ledger/settings | `implemented+tested` parts in [App](../../frontend/src/App.tsx), [modules](../../frontend/src/modules/), [hardening report](mvp1234-final-hardening.md); **`runtime gate` full authenticated non-mock browser coverage**. | Один role-aware scenario per screen group against real isolated API, source→evidence→approval→ledger; отсутствующие полные settings связаны с C02/P04/A06. |
| U02 — before/after/reason/source/confidence/risk; stale responses/permission/accessibility | `implemented+tested` Evidence/provider controls/supply/read model tests; **`runtime gate` keyboard/a11y/mobile real API**, [evidence UI](v54-evidence-product-api-ui.md), [provider controls](mvp2-provider-controls.md). | Browser keyboard-only matrix at mobile/desktop viewport and role revocation mid-operation; assertions against real API outcomes, не подменённые fixtures. |
| U03 — web≤3s, first page≤2s, local UI≤300ms; automatic background progress | `implemented+tested` durable progress/polling; **`runtime gate` latency targets**, [hardening](mvp1234-final-hardening.md), [App](../../frontend/src/App.tsx). | Measure startup/first-page/input-to-paint against declared network/data profile; prove live progress/reconnect/restart without fake percentages. |
| U04 — human-readable AI action/evidence/approver/outcome/compensation history | `implemented+tested` pilot [evidence UI](v54-evidence-product-api-ui.md), [provider action center](../../frontend/src/modules/provider-actions/ProviderActionCenter.tsx); **`implementation gap` unified graph/ledger explanation across all domains** C05/A03. | One unified object trace read model for source→proposal→approval→receipt→correction, tenant-scoped, with missing evidence explicitly marked. |

## Owner, legal и live decisions: отдельно от разработки

Следующие пункты сохраняют прежний release backlog, но не добавляют юридические
условия к техническим требованиям самого DOCX. Они нужны только для честного
production/commercial closure; наличие шаблонов не равно подписанным решениям.
Никаких новых правовых выводов о действующем законодательстве этот аудит не делает.

| ID | Статус/evidence | Следующее ограниченное действие |
| --- | --- | --- |
| O01 — canonical product version/title | `owner/legal/live gate`: p002 vs p359–386, [old OWN-01](v54-tz-coverage-2026-09-04.md). | Владелец фиксирует canonical version/edition в release decision; исходный DOCX/history не переписывается без задания. |
| O02 — scope freeze/acceptance/opening MVP/provider selection; non-goals | `owner/legal/live gate`: p417/565–578, [integration scope](mvp1234-final-hardening.md). | Сохранить принятое расширение MVP1–4; явный acceptance checklist и следующий bounded slice. Graph engine, dozens of agents, high-risk AUTO и массовая копия не становятся MUST. |
| O03 — selected test accounts/editions/residency/KEK/AI/autonomy/data classes | `owner/legal/live gate`: [policy decisions](../architecture/v54/staging-integration/policy-decisions.md), [old OWN-03..05](v54-tz-coverage-2026-09-04.md). | Назначить sandbox provider/capability/owner/limits, data region/retention/keys and rollout; это prerequisite live gate, не разрешение production enable. |
| O04 — RPO/RTO/retention/on-call/incident/restore owners | `owner/legal/live gate`: [retention](../retention-policy.md), [installation/recovery](../legal/08_INSTALLATION_RECOVERY_RU.md). | Численные targets + approved incident/restore exercise R05; определить сохранение contract/context history при project deletion. |
| O05 — canary/observability/SLO/deployment approval | `owner/legal/live gate`: [old PROD-01..09](v54-tz-coverage-2026-09-04.md), [hardening](mvp1234-final-hardening.md). | Exact SHA+all gates→staging composition→scoped shadow/pilot/canary→observation→explicit go/no-go; KEK/FragmentStore/mailbox/local upload/OCR/provider action принимать раздельно. |
| O06 — finance accounting meaning/currency/VAT/retention/mandatory act fields | `owner/legal/live gate`: [finance decision guards](mvp4-finance-decision-guards.md), [financial acceptance](mvp4-financial-acceptance.md). | Owner + профильный бухгалтер/юрист утверждают versioned policy; ручной payment fact не bank statement/бухгалтерская проводка. |
| O07 — права/лицензия/ПДн/состав коммерческой передачи | `owner/legal/live gate`: [legal readiness](legal-release-readiness-result.md), [legal kit](../legal/README_RU.md). | Заполнить owner/rightsholder/counterparty evidence, согласовать лицензию и data processing; код не подтверждает цепочку прав. |
| O08 — dependency lock/SBOM/NOTICE/container layers | **`implementation gap` release evidence + `owner/legal/live gate` license conclusions**: [SBOM audit](v54-wave3-sbom-legal.md). | Воспроизводимый Python transitive lock+hashes, exact image/layer inventory/package texts; legal licenseConcluded после evidence, не по догадке. |
| O09 — final handover/archive/Russian software registry dossier | `owner/legal/live gate`: [registry requirements](../legal/registry/REQUIREMENTS_MATRIX_RU.md), [legal readiness](legal-release-readiness-result.md). | После O07/O08 и runtime собрать allowlist manifest/checksum/install/restore artifacts без secrets/PII; dossier проверить владельцем/юристом. Подача и deploy не входят в audit. |

## Точные приёмочные crosswalk: повторы не считаются новыми функциями

Статус и bounded next action берутся из канонических строк. Совместное условие
считается закрытым лишь после **всех** ссылок; `implemented+tested` части не
повышает открытый live/runtime/implementation gate целого сценария.

| §24 MVP1 — отдельный исходный критерий | Локатор | Канонические строки / незакрытая граница |
| --- | --- | --- |
| organization/project + Google account/scopes | p256 | C01/C02/D01; actual OAuth live gate |
| one/multi folders, snapshot/tree, no obligatory copy | p257 | D01/D02/D11; live metadata/optional copy distinction |
| progress processed/skipped/failed + incremental replay | p258 | D03/R03/U03; real restart/latency |
| metadata/source/analysis state | p259 | D02/D12; provider ACL/version completeness |
| structure/name/move/archive proposals, no delete | p260 | D06/D07; rule precedence/naming and live apply gap |
| no source effect before confirm+dry-run | p261 | D07; synthetic tested, real adapter missing |
| current-source check before every effect | p262 | D07/D08; exact provider precondition |
| no overwrite/name clash, no duplicate retry | p263 | D08; real unknown/lookup gate |
| transparent partial failure + supported rollback | p264 | D08/D09; real saga/rollback gate |
| substantial actions audited | p265 | C05/T04; universal audit gap |
| low confidence never auto-mutates | p266 | D06/P01; universal enforcement |
| native file_id and revision cache invalidation | p267 | D05; native export/cache gap |
| org policy blocks external AI | p268 | P04; organization policy gap |
| run/result persist through restart | p269 | D03/R01/R03 |

| §25 MVP2 — отдельный исходный критерий | Локатор | Канонические строки / незакрытая граница |
| --- | --- | --- |
| permitted source ingress + original link | p271 | M201/M209; live mailbox/attachment |
| summary + project/contract probability | p272 | M202/M203/P02/P03 |
| task/date/assignee/amount/risk + reason | p273 | M203/P01; exact legacy Evidence gap |
| human confirms low-confidence relationship | p274 | M202/T02; actual API acceptance |
| editable reply draft | p275 | M204/U02 |
| proposed Google Task/Calendar, no default creation | p276 | M205 |
| confirmed external effect once + external ID | p277 | M206/T05; live S10 |
| Message→Project/Contract→Task/Event→Source | p278 | M208/A03; provider inbound M207 |
| send/create/update audit | p279 | M206/T04/C05 |
| AI failure preserves message/manual handling | p280 | M208/P04/R06 |

| §37 backlog ID / §34 DoD where repeated | Точная формулировка-группа | Canonical / следующий gate |
| --- | --- | --- |
| V1-01 p478–481 | repo/module/debt audit | R07; current matrix, actionable gaps rather than rewrite |
| V1-02 p482–485 | documented one-procedure startup | R05/R07; clean actual install |
| V1-03 p486–489 | Organization/User/Project/Contract migrations+CRUD | C01/C02/C03/R01 |
| V1-04 p490–493; p428 | test Drive OAuth, no token log | D01/R04; live gate |
| V1-05 p494–497; p429 | SourceFolder metadata/virtual source selection | D01/D02 |
| V1-06 p498–501; p429–430 | immutable snapshot, no source changes | D02/D03/D07 |
| V1-07 p502–505; p429 | virtual tree without physical copy | D02/D10/D11 |
| V1-08 p506–509; p431 | Document/Version linked to file_id/snapshot | D12/C03 |
| V1-09 p510–513; p431 | PDF/DOCX extraction explicit errors | D13/R06 |
| V1-10 p514–517; p432–433 | one deterministic rule→before/after proposal | D06; precedence separate from one-rule minimum |
| V1-11 p518–521; p433 | review one proposed operation accept/exclude | U01/U02/D07 |
| V1-12 p522–525; p434 | dry-run source rights/name/parents/time/version | D07; missing live conditional client |
| V1-13 p526–529 | version/status/idempotency ChangeBatch+Operation | D08/R01 |
| V1-14 p530–533; p435 | confirmed rename/move of one source object | D07; **implementation gap**, then live gate |
| V1-15 p534–537; p437 | changed source not overwritten | D07/D08 |
| V1-16 p538–541; p438 | accessible key-stage AuditEvent | C05/T04/U04 |
| V1-17 p542–545; p439 | compensating rollback if no new conflict | D09 |
| V1-18 p546–549; p440 | restart survives operation/run/audit | R01/R03/D03 |
| V1-19 p550–553; p441 | automated plus manual acceptance no loss | R01/R04/R06/U02/D01/D07 |
| V1-20 p554–557 | ≥1k metadata performance smoke | R02; smoke не весь 1k/10k target |
| Additional p436 | same idempotency key no repeat effect | D08/T05; same contract as V1-13/18 |

§31 strategic scenarios A–E p357–358: A→T01/M209/M206; B→T05;
C→T02/M202; D→T03/T08; E→T09/M204. Дополнительные A–G p383
соответствуют T07–T13. Шесть признаков Pilot Ready p380 — T01–T06.
Таким образом и ранние пять сценариев, и поздние семь не потеряны за обозначением
«13/13»; повторные требования approval/dedup не увеличивают общий denominator.

## Следующая последовательность работ

Проверка самого документа: hash исходного DOCX сопоставлен с прежним аудитом;
все 160 уникальных относительных ссылок разрешаются в этой worktree; повторов
канонических ID не обнаружено; `git diff --check` выполнен. Это проверка
трассируемости документа, не повтор backend/frontend/runtime acceptance.

1. **Замкнуть текущий runtime evidence**, R01/R03 и supply F05, на итоговом SHA:
   mandatory isolated PostgreSQL, реальные competing transactions/worker
   процессы, safe protocol и cleanup. Параллельные результаты интегрировать
   только после review; прежние synthetic/SQLite числа не переименовывать в PG PASS.
2. **Owner-independent implementation gaps с высокой проверяемостью**:
   D14 formula/value/sheet-cell extraction; D05 native export/cache manifest;
   M305 exact meeting origin; M303 durable escalation; M207 inbound Calendar/Task
   correction proposals. Каждый — свой небольшой regression-first slice.
3. **Сквозные security/contract gaps**: C02/C04 legacy authority, C05 typed audit,
   P04/P05 organization AI egress/DLP, затем C06 OpenAPI/error compatibility.
   Двигаться по одному endpoint/ingress, не массовым rewrite или заменой IDs.
4. **Drive live mutation блокер D07 и managed copies D11**: сначала доказанный
   provider contract/implementation либо явное owner-approved отклонение ТЗ,
   затем permitted sandbox. Hard-deny сохранить до этого; “enabled=false”
   нельзя считать готовой операцией.
5. **Runtime quality и UX** R02/R06/U02/U03: измеримый корпус, полные варианты
   типов/ошибок, real API browser, accessibility и latency. Затем R05 restore
   с принятыми O03/O04 эксплуатационными параметрами.
6. **Live and owner/legal closure** M206/M209/D01/D04/O03–O09. Finance decisions
   F08/O06 принимаются отдельно; не добавлять валютные/НДС/платёжные предположения.
7. **MVP6/1.0+ после выбранных требований** A01→A02/A05/A07/A08, затем A03/A09/A10/A11.
   Не объявлять весь Product Scope завершённым на основании технического MVP5
   или увеличения количества synthetic tests.

Итоговый статус всего ТЗ на проверенной базе: **не закрыто**. Есть значительный
реализованный и протестированный объём MVP1–MVP5, конкретные незавершённые
implementation contracts, невыполненные runtime/live gates и отдельные
owner/legal решения. Эта матрица — план проверяемого closure, не разрешение
включать production, отправлять сообщения, платить, публиковать или развёртывать.
