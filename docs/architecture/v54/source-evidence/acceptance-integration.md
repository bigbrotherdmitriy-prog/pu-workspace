# Приёмка и зависимости — DRAFT

Матрица ниже — **план будущих тестов**, не результаты выполнения новой
функциональности. Сейчас реализованы только документы контракта. Fixtures
синтетические, adapters fake, без реальных файлов/аккаунтов и внешнего AI.

## Матрица приёмочных тестов

| ID | Setup / действие | Ожидаемое доказательство |
|---|---|---|
| SE-01 | Два аккаунта одной org, одинаковый external_id | Разные source IDs; ни content hash, ни имя не сливают их |
| SE-02 | Две org и одинаковые account/provider/external IDs | Раздельные записи/ACL, list/get/search/fragment не раскрывают соседа |
| SE-03 | Credential refresh того же account | Stable connection/source IDs сохраняются, не новый документ |
| SE-04 | Reauth другой account в той же credential row | Новая identity/generation; старые jobs отклонены до read |
| SE-05 | Legacy connection_id=null | Unresolved, нет молчаливого выбора active/первого account и cross-project merge |
| SE-06 | Google opaque ID и Yandex disk/app path | Namespace не смешивается; #/?/%/Unicode/пробелы transport round-trip |
| SE-07 | Move stable-ID файла | Reference прежняя, locator history новая; ACL перепроверен |
| SE-08 | Move path-only файла, имя/hash совпали | Ambiguous mapping, не auto-merge; old evidence не переназначено |
| SE-09 | Delete + reuse того же path/ID | Новая incarnation; прежние evidence не воскресают |
| SE-10 | Evidence на version V1, current стала V2 | V1 pin сохранён, status stale; новый extraction даёт новый evidence ID |
| SE-11 | Источник изменился между metadata/read/metadata | Результат не становится verified, bounded retry/quarantine |
| SE-12 | Нет revision, разрешены bytes+hash | Digest с алгоритмом/scope на конкретном observation; не invented revision |
| SE-13 | Нет revision, hash/копии запрещены | Metadata-only/unknown и unverified; отсутствуют bytes/temps/embeddings |
| SE-14 | MD5 checksum, SHA-256 text и ETag | Типы/scope различаются; ETag/MD5 не выдаются за original SHA-256 |
| SE-15 | Разрешён экспорт Docs → representation | Evidence pin включает original version + export provenance; TTL invalidation |
| SE-16 | No-copy policy + snapshot | Только метаданные; safe-copy/staging job не создаётся; legacy pipeline gate обязателен |
| SE-17 | Запрет temp disk при локальном OCR | Fail closed до создания raster; альтернативный путь только по capability/policy |
| SE-18 | OCR bbox после rotation/deskew | BBox/extent/coordinate space соответствуют raster; нет ложной проекции на original |
| SE-19 | Native DOCX text без physical pagination | whole_object/section known, не фиктивная page 1 |
| SE-20 | XLSX формула/cached value/merged cells | Sheet/version/range/value_kind; отсутствие anchor явно, формулы/links не исполняются |
| SE-21 | Два письма с одноимёнными attachments | Разные scoped IDs; message/attachment source/version mismatch отклонён |
| SE-22 | OCR .98 / legacy review confirmed без version pin | Не verified автоматически; отдельная проверка source/version/locator |
| SE-23 | Permission view metadata, но нет fragment read | Metadata разрешена без quote; fragment запрещён, no snippets/cache leakage |
| SE-24 | Revoke ACL после extraction/перед read | Недоступен fragment, cache и derived search; stale credentials не помогают |
| SE-25 | Provider timeout/429, cache существует | Safe unavailable/degraded, last_seen неизменен; cache маркирован historical/stale |
| SE-26 | Provider 404 может означать потерю доступа | not_found/unknown, не автоматическое безвозвратное удаление history |
| SE-27 | Истёк quote/OCR/embedding TTL | Тексты/индексы удалены; tombstones минимальны; no leaks в audit/jobs/logs |
| SE-28 | Backup восстановлен после revoke/purge | До открытия доступа replay tombstones, deindex и policy checks; bytes не «воскресают» |
| SE-29 | Разные residency worker/source/cache/backup | Запрещённая локация блокирует read/materialization; provider name не заменяет факт |
| SE-30 | Instruction injection внутри OCR/email/filename | Текст остаётся untrusted; нет tool call, approval или изменения policy |
| SE-31 | Повтор resolve/extract с тем же observation key | Один logical result, конфликт другой payload не перезаписывает старый |
| SE-32 | Конкурентная verification старой версии | If-Match/source_version проверены; assessment не перезаписывает новый |
| SE-33 | Delete project с историческими ссылками | Явный purge/tombstone workflow; нет бесследного cascade доказательств |
| SE-34 | SourceVersion из другого source/org | FK/service reject для evidence и representation; никаких cross-tenant fragments |
| SE-35 | Existing integer IDs + optional new public_id | Старые URLs/интеграции работают; identity backfill без изменения PK |
| SE-36 | Откат feature flag после no-copy/revoke | Legacy fallback не обходит более строгую policy |
| SE-37 | Safe-copy изменена независимо от original | Разные versions; фактически прочитанная копия указана в evidence |
| SE-38 | Model/prompt/extractor поменялись, source тот же | Новый evidence/provenance, не переписан прошлый вывод |
| SE-39 | ContextRelation/Execution читают evidence | IDs+effective assessment, версия проверена; verified не означает разрешённый AUTO |
| SE-40 | Технические логи при любой ошибке | Только ID/коды/счётчики; нет quote, document text, tokens, signed URLs |

Стартовые regression-наборы для будущей реализации (не переписывать их
статические ожидания ради зелёного результата): test_storage_binding_validation,
test_storage_provider_regression, test_storage_adapter_contract_matrix,
test_yandex_storage_adapter_contract, test_document_version_comparison,
test_content, test_ocr_commercial_hardening, test_ocr_batch,
test_local_upload_documents. PostgreSQL нужен для настоящих unique/FK/CAS/
concurrency, а не только SQLite. OCR corpus-тест regex extraction не заменяет
реальное OCR-качество и проверку навигации на исходном raster.

## Границы соседних потоков

| Владелец | Что нужно получить/согласовать | Что передаём |
|---|---|---|
| Integration/storage | Stable connection identity, namespace, generation; capability versioned_read/revision/permissions/copy/locator | SourceReference ID, pinned identity/version, допустимый режим read/derive |
| ContextRelation | Scope links и права на межпроектные ссылки; supersession без стирания истории | evidence_id/source_reference_id/source_version_id; status и причины, без собственной схемы relation |
| Approval/Execution/Ledger | Повторная оценка при смене source/evidence/policy; правила хранения истории и restrictive rollback | evidence IDs + source versions + assessment record_version + correlation ID; никаких decision/payload schemas здесь |
| Organization/security | Read/fragment/derive/retain ACL, residency, TTL, legal-hold ownership | PolicyRef и список representations, требующих контроля |
| OCR/content | Exact extraction run, Tesseract/model/config версии, bbox coordinate transform, per-version metadata | Evidence locator/provenance; не новая OCR-реализация |
| Encrypted staging fork | Решение об интеграции, shared volume/runtime проверки, версия migration head | StagingObject ID как representation handle; encryption != authorization |
| Database/integration | Additive migration/bridge, tenant constraints, rollback, project delete policy | Поля, инварианты и SE-01..40; ORM/Alembic не создаются этим потоком |

## Вопросы до утверждения

1. Кто владеет stable connection registry и как подтверждается subject, если
   provider не возвращает стабильный account ID? Legacy записи нельзя угадать.
2. Допускается ли одна SourceReference в нескольких проектах org? Предложение:
   identity одна, project visibility только через explicit links; пока их нет,
   origin_project_id ограничивает доступ, не даёт organization-wide read.
3. Что считается достаточной verification для конкретного claim: human review,
   deterministic extraction, provider revision или combination? Порог .72 из
   OCR не универсален и не legal approval.
4. Какие policy поддерживают transient_read без disk, цитаты и embeddings?
   Где worker/temp/swap/backups физически разрешены? Без ответа default deny.
5. Какая retention precedence при удалении source, отзыве доступа и legal hold?
   Нужен владелец решения, не бессрочный audit bypass.
6. Какие права отделяют metadata, source open, fragment read, download и verify?
   Существующий project viewer/admin недостаточен для будущего source ACL.
7. Каким контрактом Execution получает invalidation до внешнего действия?
   Нужна обязательная финальная проверка, не одна best-effort доставка события.
8. Как показывать старый reviewed evidence без original: «historical verified
   at T, source unavailable», без обещания актуальности и без forbidden quote?
9. Когда отдельно менять snapshot → auto safe-copy под org policy и включать
   version-aware extraction? Этот docs-only commit ничего не переключает.
10. Переносится ли staging fork `372b661eefebb9c154dd847e8c331acc2b128d94`?
    Его миграция/queue изменения должны быть отдельно сведены с базой.

Решения пока **OPEN**. Ни наличие черновика, ни JSON-примеры не закрывают
runtime/security acceptance. Утверждение контракта — отдельный этап интегратора.
