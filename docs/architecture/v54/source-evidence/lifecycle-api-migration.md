# Lifecycle, API и совместимая миграция — DRAFT

Все endpoint/таблицы ниже **предлагаются**, не существуют в базовом SHA.
Ничего из этого документа не запускается автоматически.

## Наблюдение и доказательство версии

1. Авторизовать principal + project + organization + source connection.
   Разрешения read metadata, read bytes, derive, retain проверяются раздельно.
2. Зафиксировать stable connection identity/generation, source locator и policy
   version. Если legacy identity неизвестна — сохранить unresolved mapping,
   не выбирать единственное/первое активное подключение автоматически.
3. Получить metadata и provider revision при capability; записать observation
   timestamps. `last_checked_at` меняется на попытку, `last_seen_at` только на
   успешное наблюдение конкретного объекта, не на healthcheck provider.
4. При разрешённом чтении получить **версию**, а не произвольный latest.
   Если versioned read нет — прочитать metadata до/после bytes; при расхождении
   discard/quarantine extraction и bounded retry. Одинаковый weak metadata
   не гарантирует отсутствие изменения внутри окна чтения.
5. При отсутствии revision: разрешённый hash исходных bytes даёт
   digest_observed для конкретного read. Повторная актуализация требует снова
   проверить bytes; mtime+size недостаточны. Hash exports не равен hash original.
6. Если hash/read запрещены или недоступны: metadata_only/unknown, reason.
   Создать immutable observation ID, не synthetic provider revision. Fresh
   metadata не повышает evidence до verified content. Нельзя получить сильное
   доказательство версии по одному имени, размеру или времени запроса.
7. Извлечение сохраняет ссылку на observation и прочитанную representation.
   Доказательство версии/locator и human review конкретного значения отделены
   от confidence extraction. High confidence сам по себе не verification.
8. Новые evidence связываются с потребителем только через его ID-контракт.
   Запись результата и публикация reference должны быть атомарны/идемпотентны
   в существующей БД/очереди; модель outbox/ledger принадлежит интегратору.

## Изменения и сбои

| Событие | SourceReference/Version | Evidence и поведение |
|---|---|---|
| Изменились bytes/revision | Новая SourceVersion, обновление current_version_id | Старое evidence stale; старые fragment/locator не переносятся на latest |
| Переименование/move при стабильном ID | Та же source identity, новый locator observation; история адреса сохраняется | Content evidence может сохранить проверку версии, но ACL/freshness перепроверяются |
| Move при path-only ID | Не угадывать соответствие; pending mapping/new reference | Old unavailable/stale; пользователь/adapter подтверждает same-source, иначе новые evidence |
| Путь/ID появился снова после удаления | Новая incarnation/reference, пока не доказана прежняя identity | Историческое evidence не воскресает автоматически |
| 404/не найден | not_found, не доказанное deleted | Не выбрасывать history; объяснить «объект не найден или недоступен» |
| Доказанное удаление | Tombstone, sync stopped; bytes purge по policy | unavailable с историческими pointers; разрешённый архив явно historical |
| Отозван доступ/credentials | access_denied, connection generation invalidated | Закрыть original и fragment/cache reads; нельзя продолжить через другое подключение |
| 429/timeout/provider 5xx | degraded/provider_unavailable; last_seen не меняется | Bounded retry existing queue; разрешённый кэш только с устареванием, не fresh |
| Нет hash/revision | observation сохранён с unknown/metadata_only | unverified, объяснение ограничения; ручной review не выдумывает version |
| Изменились ACL/residency/retention | Политика пересчитана для всех descendants | Отозвать fragment URLs/tokens, остановить запрещённые операции, purge по policy |

Повторный fetch той же версии может обновить availability/freshness assessment,
но не менять locator/extractor/value старого evidence. Recovery не меняет
источник job на «текущее подключение проекта»; используется pinned identity.
Выход из unavailable не возвращает verified без новой проверки.

## Политики содержания, копий и retention

Политики принадлежат organization/security owner. Контракт требует
`allowed_processing_locations`, `allowed_storage_locations`, разрешённые
purpose/derivation виды, `retention_policy_ref/version` и явные сроки.
Неизвестное размещение != разрешённая страна. Cloud/on-prem не определяется
именем provider; source, worker, cache, logs и backups проверяются отдельно.

Режимы не синонимы:

- reference_only: разрешённые metadata/locator, без чтения/сохранения bytes;
- transient_read: чтение в разрешённом контуре без retention; RAM/swap/temp
  должны соответствовать policy. Если runtime не гарантирует — deny;
- derived_only: разрешены перечисленные representations, но не original copy;
- retained_copy: конкретная разрешённая копия, purpose/expiry/location и ID
  policy/approval (если требуется) известны до постановки job.

| Данные | Что хранить только по явному разрешению | Invalidation/purge |
|---|---|---|
| Metadata, locator, имя | Минимум для source identity; имя/путь тоже могут быть чувствительными | Redact/tombstone отдельно от удаления bytes |
| Исходные bytes | Не требуются контрактом | Удаление локальной копии не удаляет remote original |
| Текст DocumentVersion/ExtractionResult | Производный чувствительный контент | Expiry/policy change; не продлевать TTL простым чтением |
| Цитаты/значения Evidence | Раздельное право хранить и раскрывать | fragment tombstone, status unavailable; не оставлять дубликат в audit |
| OCR raster/token/bbox/text | Raster/text — контент; bbox также может раскрывать структуру | TTL по source version; mapping не делает retained bytes обязательными |
| Embeddings/search index | Derived data, не анонимные «технические числа» | Deindex при revoke/purge, удаление replicas; не использовать для training |
| Export/cache | SourceVersion + format/exporter + expiry | Invalidate при новой source version; не подменять original |
| Staging/retry/dead-letter | Самостоятельное разрешение и короткое recovery window | Cancel/success/expiry cleanup; ключ encryption не отменяет purge |
| Safe-copy/provider copy | Destination policy + relationship к original | Ownership/retention отдельно; delete только отдельно разрешённым action |
| Backup/WAL/snapshots/log archives | Явный backup retention/location; не «хранить навсегда ради audit» | Tombstone/purge replay до открытия восстановленной БД; expire immutable backups по срокам |

Никаких универсальных сроков «30/90/365 дней» без решения владельца. Null TTL
не означает forever; materialization блокируется до заполнения policy.
Legal hold, если существует, задаётся полномочным владельцем ID/политикой:
не создаётся AI, не возвращает отозванный live access, не скрывает конфликт
retention. При несовместимых ограничениях — block + решение владельца.

При удалении source связь с историей остаётся через ID/tombstone, но текст,
цитаты, embeddings и PII не сохраняются автоматически. Action Ledger owner
должен поддержать минимальные ссылки и отображение redacted/unavailable;
нельзя писать исходный fragment в append-only ledger для обхода retention.

## Предлагаемый API

Версия `/api/v54` условная; namespace согласует интегратор. Обязательны текущая
auth/CSRF, server-side object permissions, request/correlation ID, pagination.
Ниже не предлагается публичный URL-fetch или endpoint произвольного download.

| Метод / путь | Контракт |
|---|---|
| GET /projects/{project_id}/source-references | Разрешённые metadata, cursor/limit; filter provider/availability; без fragments |
| GET /source-references/{id} | SourceReference + effective access/assessment для principal; ETag record_version |
| GET /source-references/{id}/versions/{version_id} | Конкретная immutable observation; никогда fallback на latest |
| POST /projects/{project_id}/source-references/resolve | Предложение register/resolve: connection_identity_id, expected_generation, locator; Idempotency-Key; metadata-only intent |
| POST /source-references/{id}/refresh | Authorize read; If-Match + expected_generation; 202 existing durable job ID при разрешённом refresh |
| GET /evidence/{id} | Metadata/locator/assessment без inline quote; раздельные fragment capabilities |
| GET /evidence/{id}/fragment | Read/source ACL + retain/residency policy; version pinned, Cache-Control:no-store, без другого source fallback |
| POST /evidence/{id}/verifications | Право verify + evidence record version + source_version_id + outcome/reason; append assessment, не overwrite fragment |
| GET /source-references/{id}/open | Auth перед выдачей короткоживущего safe link exact object/version; если version недоступна — явное unsupported, не latest |

Создание Evidence — authenticated internal extractor command с source/version
и observation key; caller не может установить verified/authorization. Если
интегратор откроет endpoint, требуются equivalent сервисные scopes, лимиты,
dedup key и запрет self-approval. Re-extract создаёт новый evidence ID.

Resolve принимает organization через server project lookup, а не доверенный
body. Conflict текущего project/connection возвращает 409, без выбора первой
папки/аккаунта. Нельзя отправить arbitrary organization/access policy для
самоповышения; policy ID только из разрешённого org scope.

Ответ fragment: evidence_id/source_version_id, media_type, разрешённый
excerpt либо representation handle, effective status, historical flag.
Он не кэшируется service worker/browser/shared CDN. Список evidence не
раскрывает цитату через search snippets, counts или error_message.

Ошибки: 401 unauthenticated; 404 resource_not_visible для чужого scope;
403 policy_denied для видимого объекта и запрещённой операции; 409
source_changed/connection_changed/ambiguous_identity; 412 stale_record_version;
422 unsupported_locator/insufficient_version_evidence; 410 fragment_expired
для видимого tombstone; 503 source_unavailable с retryable и bounded retry hints.
HTTP 404 provider сам по себе не создаёт permanent-deletion tombstone.

У события source_version_changed/access_revoked/representation_purged только
ID, record versions, reason, correlation ID. Payload без документа/секретов.
Context/Execution owners получают эти события или проверяют freshness по
запросу; доставка события не заменяет проверку непосредственно перед исполнением.

## Минимальная физическая схема (предложение, не ORM)

Интегратору рассмотреть additive `source_references`, дочерние
`source_version_observations`, `evidence`, `evidence_assessments` и таблицу
legacy mapping. Representation descriptors могут переиспользовать будущий
storage registry, не требуют новой blob-таблицы в этом контракте.

Нужны FK source+version+organization consistency (составные FK либо обязательная
транзакционная проверка), scoped unique index confirmed identity и уникальный
ingestion idempotency key; generation/CAS на mutable записи. Для history не
использовать cascade content deletion как единственный lifecycle. Retention
purge выполняет явный workflow; project delete должен согласовать tombstones
и purge, не просто отключить ограничения.

`Document.id`, `DocumentVersion.id`, `SourceFolder.id`, `WorkspaceSnapshot.id`
сохраняются. FK/source reference можно добавить nullable либо через bridge
`(organization_id, legacy_entity_type, legacy_entity_id, source_reference_id,
source_version_id, mapping_state)`. Это техническое происхождение, не
замена ContextRelation. Нужные Alembic и DDL создаёт интеграционный поток.

## План внедрения без переписывания

1. Утвердить identity/version, no-copy и ACL/retention решения; сверить contracts
   соседних потоков. Статус этого документа не меняет runtime policy.
2. Только инвентаризация и additive schema/indices под feature flag: не менять
   существующие ID, aliases и legacy columns; backup/restore проверить отдельно.
3. Shadow backfill из существующих метаданных, **без обращения к provider**.
   organization берётся через Project; snapshot storage_binding переиспользовать.
   Неразрешённые identity, неизвестные версии/hash scopes пометить unresolved.
   Не копировать текущий Document.ocr_metadata на все DocumentVersion.
4. DocumentVersion → source observation связывать только при доказанном
   соответствующем extraction run. Иначе provenance_unknown + unverified;
   текста может быть достаточно для исторического просмотра по старым правам,
   но это не fresh evidence внешнего оригинала.
5. Новые reads/extractions dual-write ссылки в одной транзакции/согласованном
   existing job flow; audit compares counts/mappings/conflicts. Расширения
   StorageObject для revision/capabilities optional, не break всех adapters.
6. UI metadata-first показывает status/limitations. Раздельный fragment access
   включается после security-тестов; no fallback на другой source/version.
7. Отдельно интегратор устраняет автоматическое snapshot → safe-copy для
   reference-only policy. Сохранить разрешённый legacy copy workflow; не
   отключать Gmail/финансы/OCR. До gate продукт не заявляет full federated no-copy.
8. Staging fork интегрировать только отдельным решением, после reconcile queue,
   schema head и runtime tests. Он не условие для reference-only режима.
9. Rollout org/project cohorts; обратное переключение readers feature flag,
   но без удаления mappings/history или обхода новой restrictive policy.
   При no-copy/revoke rollback блокирует операции, не включает legacy fallback.

Контроль backfill: restartable checkpoints, dry-run, conflict report, bounded
batch, no remote I/O, no derived-content duplication. Критерий rollback —
scope leak, недоказанное слияние аккаунтов/версий, retention bypass или
расхождение dual-write. Деструктивная cleanup старых полей здесь не предлагается.
