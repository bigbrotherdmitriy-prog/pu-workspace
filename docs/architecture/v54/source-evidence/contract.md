# Черновая схема и инварианты

Статус: DRAFT, не реализовано. Слова «обязан/нельзя» ниже описывают предлагаемую
приёмку будущего контракта, а не утверждают возможности текущего релиза.

## Общие типы

- Общие ObjectRef/TaggedId/VersionPin определены только в
  [integration glossary](../integration/glossary.md). Id ниже — shorthand
  локального PK исходного proposal, не wire-format. Existing PK сохраняются.
- `Timestamp`: UTC RFC3339. null = неизвестно/не выполнялось, не «сейчас».
- `record_version`: монотонный integer для optimistic concurrency; не revision
  внешнего файла. `schema_version`: `source-evidence.v54-draft.1`.
- `PolicyRef`, `ConnectionIdentityId`, `ContextRelationId`, `ApprovalId`,
  `ActionId`, `LedgerEntryId`: ссылки на объекты чужих потоков, без их схем здесь.
- Пустые значения запрещены вместо null. Enum расширяется новой версией API;
  неизвестное capability/status не означает разрешение.

## SourceReference

SourceReference описывает один адресуемый первоисточник в одном tenant/account
namespace. Его версия не хранит bytes обязательно. Если original — документ
на диске клиента, сама reference не меняет его владельца/расположение.

| Поле | Тип / обязательность | Семантика |
|---|---|---|
| id, organization_id | Id, required | Собственный ID; organization проверяется по разрешённому project, не доверяется клиенту |
| origin_project_id | Id, required на первом этапе | Совместимость с текущим project RBAC; не даёт доступ остальным проектам организации |
| public_id | string/null, optional | Дополнительный внешний handle, существующие ID не заменяет |
| record_version, schema_version | integer/string, required | Версия метаданных и контракта |
| connection_identity_id | Id/null, required | Стабильный логический аккаунт/tenant, не credential row или access token |
| identity_state | verified / legacy_unresolved | null connection разрешён только в изолированном legacy bridge |
| connection_generation | integer/null | Версия привязки/доступа на момент наблюдения; проверяется через интеграционный слой |
| provider | string | Канонический adapter identifier, alias map версионирован |
| namespace | string | Область уникальности provider: диск/mailbox/bucket/library; не секрет |
| external_id | string | Provider-native opaque ID; для path-only provider — путь, явно помеченный mutable |
| external_id_kind | stable_id / mutable_path / legacy_unresolved | Не считать путь глобальной identity |
| incarnation | integer >=1 | Новая сущность после доказанного повторного использования ID/пути |
| object_kind | file / folder / message / attachment / record | Возможности locator зависят от типа |
| canonical_locator | {kind, value, normalization_version} | Address, не credential-bearing URL; см. правила ниже |
| current_version_id | Id/null | Последнее согласованное наблюдение; не «последняя версия текста» |
| freshness | fresh / stale / unknown | fresh только в пределах явно заданного TTL/проверки |
| sync_state | discovered / syncing / current / degraded / stopped | Состояние чтения метаданных, не job lifecycle |
| availability | available / access_denied / provider_unavailable / not_found / deleted / unknown | not_found не автоматически deleted |
| last_seen_at | Timestamp/null | Последнее успешное наблюдение именно объекта |
| last_checked_at, next_check_at | Timestamp/null | Попытка проверки и срок следующей; ошибка не обновляет last_seen |
| access_policy_ref, residency_policy_ref, retention_policy_ref | PolicyRef, required для активной reference | Версионированные политики чужих владельцев; отсутствие блокирует материализацию |
| classification | string | Класс данных, назначенный разрешённым правилом/пользователем |
| residency | {source_location, observed_at, assurance} | Факт размещения: known/declared/unknown; адрес источника не доказывает страну хранения |
| last_error_code | safe enum/null | Без URL/query/token, исходного текста или raw provider error |
| created_at, updated_at, created_by_user_id | Timestamp/Id | Аудит создания; service actor согласовать через существующий identity слой |

### Stable connection identity и уникальность

Предлагаемый ключ confirmed identity:
`(organization_id, connection_identity_id, provider, namespace, external_id, incarnation)`.
Аккаунты A и B с одинаковым external_id **не дедуплицируются**. Hash содержимого
не участвует в identity; одинаковые bytes могут иметь разных владельцев/ACL.
Доступ между проектами задают подтверждённые ContextRelation/access bindings
другого потока; совпадение identity не расширяет ACL.

Integration owner должен предоставить stable identity: lifecycle refresh токена
сохраняет identity; reauthorization другого аккаунта создаёт новую identity.
Проверка account subject/tenant предпочтительнее email. При отсутствии subject
допустим локальный логический handle с подтверждённой привязкой, но **не**
автослияние по email. Credential generation может меняться при revoke/reauth;
обычная rotation секрета не означает новую source identity.

Для legacy null connection identity ключ только временный:
`(organization_id, origin_project_id, provider, legacy_binding_record_id, external_id)`.
Он остаётся в mapping/quarantine, не в глобальном confirmed dedup index.
Нельзя подставлять текущий аккаунт проекта задним числом; unresolved источник
не годится для автоматического исполнения. `connection_row_id` базы — лишь
bridge; `connection_id` строки может быть null или credential ID, не stable identity.

### Canonical locator

- Google: kind=opaque_id, value=исходный ID; имя/родитель не часть identity.
- Яндекс текущего adapter: kind=provider_path, `disk:/...`/`app:/...`, namespace
  сохраняется. `#`, `?`, `%`, кириллица и пробелы — данные, не URL fragment/query.
  HTTP transport кодирует один раз, application получает исходный locator.
- Generic normalization не lower-case, не casefold и не strip путь; Unicode,
  символы и case сохраняются до подтверждённых правил provider.
- Canonical reference не является произвольным fetch URL. Open-source URL
  генерирует проверенный adapter после auth; deny private-network/SSRF и
  credential-bearing links. Signed URL не сохраняется в evidence/audit.
- Переезд с path-only ID нельзя угадать по имени/hash. Нужен стабильный resource
  ID/move event либо подтверждённое сопоставление; иначе новая reference и
  неоднозначность, а не скрытая подмена старой.

## SourceVersion — подчинённое immutable observation

Не новый доменный документ и не чужой ledger. Сохраняется отдельно от mutable
SourceReference; в JSON примерах передаётся массивом `source_versions`.

| Поле | Тип | Правило |
|---|---|---|
| id, source_reference_id | Id | Evidence всегда указывает конкретную запись, не latest |
| observed_at | Timestamp | Когда получено наблюдение |
| provider_revision | {value, kind}/null | Provider token с непрозрачной семантикой; ETag не объявляется content hash |
| consistency | revision_bound / digest_observed / metadata_only / unknown | Сила привязки bytes/наблюдения |
| integrity | массив IntegrityMetadata | Может быть пустым по политике; hash оригинала и hash extraction не смешиваются |
| source_modified_at, size_bytes | Timestamp/null, integer/null | Слабые hints, не доказательство идентичности содержимого |
| locator_at_observation | CanonicalLocator | Снимок адреса; текущий путь не переписывает историю |
| read_started_at, read_finished_at | Timestamp/null | Окно extraction; null при metadata-only |
| consistency_reason | safe code/null | no_revision, hashing_forbidden, changed_during_read и т.п. |
| legacy_document_version_id | Id/null | Только доказанная связь; нельзя назначить по current_version ordinal |

Version row immutable. Новый observation может ссылаться на тот же внешний
revision; ключ ingestion/idempotency включает job/run observation key, а не
только hash. Несколько повторов одного run не создают дубль; повторная проверка
не создаёт новую текстовую версию без изменения content. Для revision-less
source новый read получает новый observation; ни время, ни metadata hash не
выдаются за provider revision.

IntegrityMetadata: `{algorithm, value, scope, computed_at, canonicalization}`.
Scope: source_bytes / exported_bytes / extracted_text / fragment / metadata.
Разрешены только явно согласованные алгоритмы; legacy unknown algorithm не
повышается до SHA-256 по длине строки. MD5 — слабый checksum/change hint, не
криптографическая гарантия. Hash подтверждает bytes указанного scope, **не**
юридическую истинность, авторство, полноту извлечения или безопасность текста.

## Representation descriptor — не реализация хранения

`{id, source_reference_id, source_version_id, kind, external_source_reference_id,
storage_object_ref, policy_ref, expires_at, availability, integrity}`.
Kind: cache / staging / authorized_copy / extracted_text / quote / ocr_image /
embedding. `storage_object_ref` — opaque ID владельца storage, не путь/ключ/URL.
Original представлен SourceReference, **не** representation kind=cache.

- Cache/export воспроизводим из конкретной версии, инвалидируется при её
  изменении; export format и exporter version относятся к provenance.
- Staging — краткоживущий вход worker, не архив и не источник истины.
- Authorized copy имеет собственный external объект (при необходимости свою
  SourceReference); descriptor связывает его с исходной версией. Изменение копии
  создаёт её версию и не меняет version оригинала. Источник каждого evidence
  — то, что фактически читалось; копия не маскируется как original.
- Запрет local copy распространяется и на temp files, OCR rasters, экспорт,
  extracted text, quote, embeddings, telemetry, backup — согласно отдельным
  permissions, а не только на полную копию файла. Encryption не даёт разрешение.

## Evidence

Минимальная единица — один адресуемый фрагмент одного source/version. Claim
с несколькими источниками хранит массив evidence IDs в объекте владельца
claim/ContextRelation. Ни граф, ни approval record здесь не дублируются.

| Поле | Тип / правило |
|---|---|
| id, organization_id, schema_version, record_version | Id/версия; record_version только для assessment/access projection |
| source_reference_id, source_version_id | Required; FK-пара обязана соответствовать одному org и source |
| representation_id | Id/null; обязателен, если фрагмент получен из export/copy/cache |
| locator | Discriminated locator, см. ниже; неизвестный locator не выдумывается |
| extracted_at | Timestamp |
| extractor | {name, version, method, model_provider, model_id, model_version, prompt_version, configuration_digest} |
| confidence | {value, kind, calibration_ref}; value 0..1 или null; kind=heuristic/model/calibrated/unknown |
| reference_metadata | {label, language, granularity}; необязательные разрешённые данные, не документное содержимое по умолчанию |
| integrity | IntegrityMetadata[], допустимо [] |
| status | verified / unverified / stale / unavailable; производное представление, не самоутверждение AI |
| assessment | {verification, verified_by_user_id, verified_at, verification_method, freshness, availability, checked_at, reason_code} |
| access_policy_ref, retention_policy_ref | Required; читатель evidence не обязательно читатель quote |
| fragment | {representation_id, retention_state}; inline text только в отдельном авторизованном ответе fragment API |
| created_at | Timestamp; correction/supersession создаёт новый evidence ID, старый остаётся либо обезличивается по policy |

Integrated Evidence имеет immutable revision=1; record_version относится только
к assessment. Claim/anchor пишет Task domain, не Evidence. Approval pins и
freshness-only recheck согласованы в integration glossary.

`extractor` поля version/model/prompt могут быть null для исторической записи
или отсутствующего AI; reason unknown/not_applicable различается через method
и migration diagnostics. Не писать Tesseract version, которую не измерили.
Configuration digest допустим только без секретов и по hash policy.
OCR confidence не является вероятностью истинности договора, а native .98
не означает human verified. Legacy review confirmed — не доказательство
проверки конкретной версии/пункта; backfill остаётся unverified до сверки.

### Locator union

| kind | Обязательное содержимое | Правила |
|---|---|---|
| page_bbox | page, coordinate_space, box, extent, representation_id | page 1-based; box=[x,y,width,height]; positive area, внутри extent; units pixels/points/normalized явно |
| page | page | Только реальная физическая страница конкретного PDF/image/export |
| section_clause | section_path, clause_label, anchor | Section path массив; anchor nullable при невозможности точной навигации; label сам по себе не уникален |
| sheet_cell | sheet_key, sheet_name, range_a1, value_kind | sheet identity в рамках version; value_kind formula/cached_value/displayed_value; не исполнять формулы/внешние ссылки |
| message | message_external_id, part, char_range | part body/subject; offsets nullable, Unicode code points [start,end), версия текста фиксирована |
| attachment | message_external_id, attachment_external_id, attachment_source_reference_id | Вложение — отдельный source/version, родитель message только reference; нет выдачи чужого attachment по одному имени |
| record | record_key, field_path | Только provider capability; JSON path convention versioned |
| whole_object | reason_code | Явная недостаточная гранулярность, не фиктивная page=1 |

BBox OCR сейчас в координатах **preprocessed raster**, не оригинального PDF.
Для навигации нужен representation ID, размер страницы, rotation/deskew/scale
и mapping к original; mapping nullable + precise_navigation=false если неизвестен.
Нельзя рисовать box на оригинале с неподтверждённым transform. Section/Sheet
из plain text без anchors не повышается до точного clause/cell evidence.

### Verified != fresh != readable

Immutable content/locator/provenance отделяются от изменяемых assessment records.
`assessment.verification=verified|unverified`, freshness=fresh|stale|unknown,
availability=available|access_denied|provider_unavailable|deleted|unknown.
Для простого UI `status` вычисляется в порядке:

1. Недоступно читателю/источнику → unavailable (уточнение безопасным reason).
2. Источник изменился либо истёк TTL → stale.
3. Exact version match, подходящий locator, законченная проверка и fresh → verified.
4. Иначе unverified, включая unknown/no-revision/некалиброванное extraction.

Историческая verification сохраняется, но UI не показывает её как свежую
после revoke/delete. Approved archival representation можно читать только
по отдельной archival policy; это historical evidence, не current source.
Status scoped к principal: нельзя утечь наличием evidence другому tenant.

## Сквозные инварианты

S1. Tenant и source/version/representation scope проверяются на сервере во
всех list/get/fragment/search/export/worker flows; project membership не заменяет
source ACL, admin bypass требует отдельного break-glass policy/audit.

S2. Refresh credentials не переписывает stable source identity. Удалённые links
не восстанавливаются legacy fallback. Имена и content hashes не dedup keys.

S3. Evidence pin version immutable; новая редакция не переназначает старое
evidence на latest. ContextRelation/claim получает новый evidence ID явно.

S4. No-copy/no-retain применяется до download/extract/enqueue/cache. Если
локальный OCR обязан писать temp files, а policy это запрещает, операция
возвращает policy_denied; запрет не обходится названием «staging».

S5. Source text/quotes/headers/filenames считаются untrusted data. Инструкции
«отправь», «игнорируй policy», credential-like strings не управляют tool calls.
Модель не назначает status=verified, права, retention, risk или autonomy.

S6. Даже verified evidence не является approval. Execution owner повторно
проверяет evidence assessment, version, current policy и полномочия; решение
AUTO/CONFIRM и invalidate approval принадлежат другому потоку.

S7. Purge quote/OCR/embedding удаляет содержимое и поисковые производные,
но не фальсифицирует history. Tombstone минимален, без утечки прежнего текста.
История не является бессрочным разрешением хранить персональные данные.
