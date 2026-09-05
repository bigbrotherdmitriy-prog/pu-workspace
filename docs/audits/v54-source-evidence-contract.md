# Аудит v5.4 SourceReference / Evidence

Статус: **внутренний черновик контракта, NOT APPROVED**. Только docs.
Дата: 2026-09-03. База: `66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`.
Ветка: `codex/v54-source-evidence-contract`.
Worktree: `C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/pu-workspace-v54-source-evidence-contract`.

## Проверка исходного состояния и требования

Создана новая чистая worktree от точной локальной базы, без cherry-pick и
переноса пользовательских файлов. Применимых AGENTS.md не обнаружено.
Основная worktree осталась на `codex/commercial-p2-yandex360`,
`83774aac726acd4e27b349e9194f30783158bde8`, с исходными изменениями:
backend/app/api/auth.py, local_upload.py, workspace.py; backend/app/schema.py;
backend/app/static/app.js; docker-compose.yml; frontend/index.html.

DOCX из Downloads прочитан read-only через OOXML, включая весь body и таблицы.
579 абзацев, 10 таблиц; отсутствие redlines/comments/footnotes проверено.
SHA-256 и точная трассировка требований — в
[README контракта](../architecture/v54/source-evidence/README.md).
Documents skill использован для чтения; LibreOffice/soffice в PATH не найден,
визуальная пагинация не проверялась и номера страниц не выдумывались.
Исходный DOCX не изменялся, не отправлялся наружу, в репозиторий не копировался.
Старые пункты про первый срез не отменяют уже работающие Gmail/финансы/OCR.

## Existing → reuse → gap

Пути ниже относительны корню **указанной базы**, symbols дают устойчивые
точки проверки. Это статический аудит кода, не production/runtime аттестация.

| Existing / доказательство | Reuse | Gap / риск для нового контракта |
|---|---|---|
| `models/document.py:Document` | Integer id, project_id, external_id, source, hash, modified_at, OCR metadata | Нет connection identity/namespace/source revision/org field напрямую; org через Project |
| `models/document_version.py:DocumentVersion` | Версии текста по document_id + version_number | Нет provider revision/hash scope/evidence pin; content=полный plaintext; CAS/unique ordinal в модели не заданы |
| `document_engine.py:index_documents` | Incremental text-hash versioning, сохранение существующих IDs | Поиск только project_id+external_id, без provider/connection; разные источники могут смешаться. SHA-256 текста или md5 в одном content_hash без algorithm/scope |
| `models/workspace.py:SourceFolder` | Provider-neutral root/source, project ID | Unique(project_id, external_id) не включает account/provider; длина 255; не межаккаунтная identity |
| `WorkspaceSnapshot`, `VirtualNode` | Snapshot id, node parent/name/mime/size/checksum/mtime | Нет строгого source version per node, ACL/residency/freshness; snapshot metadata не byte-consistent snapshot дерева |
| `models/workspace.py:ExtractionResult` | FK к DocumentVersion, extractor/status/error/text | Нет model/prompt/versioned locator/retention; не то же самое, что dataclass ExtractionResult в content.py |
| `models/external_resource.py:ExternalResourceLink` | Provider-neutral связь Core → external, sync_status/synced_at | Unique(entity_type, entity_id, provider, resource_type), без account/namespace/version; не новый source registry |
| `integrations/external_resources.py:external_id_for` | Legacy compatibility wrapper | deleted link может уйти в legacy_id fallback; для revoked/deleted source v5.4 такой fallback недопустим |
| `models/drive_connection.py`, `integration_credential.py` | Project connection, encrypted credentials, account_external_id, optional connection_id | Один connection/project, identity credential row изменяема; account email не stable key; generation отсутствует |
| `core/integration_types.py:StorageObject` | Provider-neutral object fields, id/parent/type/checksum/mtime | Нет revision, account namespace, permissions/capability snapshot; md5 поле не универсальный integrity type |
| `integrations/contracts.py:StorageAdapter/MutableStorageAdapter` | Read/list/walk/copy и copy-only mutation boundary | Нет general capabilities/versioned_read/delta/ACL contract; не создавать второй adapter registry |
| `integrations/storage.py:validate_storage_locator` | Provider-specific namespaces/валидация; 255 limit | Не объявлять Google opaque ID и Yandex path взаимозаменяемыми; generic canonicalization опасна |
| `organizer_engine/drive.py:DriveClient` | Google ID/parents/mtime/checksum, export/read | Revision не запрашивается; native export text/plain или CSV, без persisted versioned export cache/TTL |
| `integrations/yandex_disk.py:_to_object` | disk/app paths, md5/modified, safe-copy | ID обычно path, resource_id fallback; stable resource identity/rename continuity не подтверждены |
| `api/workspace.py:_binding/_validate_snapshot_target` | JSON pin project/provider/connection_id/row/folder, guards before worker | Нет account generation/content revision; legacy без binding проходит совместимый путь |
| `models/ai_secretary.py:Message` | org/project, source external/thread IDs, attachments_json, context_evidence | Evidence текстовое; global unique(source_type,source_external_id) без mailbox identity; attachment version pins не едины |
| `core/auth.py:require_project_role` | Server-side project RBAC | admin bypass и project role не дают раздельный source/fragment ACL; не заявляется готовая org residency policy |

## OCR: существующее доказательство и пределы

`organizer_engine/content.py` уже содержит PageExtraction, FieldEvidence,
TableCell, confidence и metadata. Есть preprocessing, Tesseract rus+eng по
умолчанию, native/hybrid/OCR, извлечение number/date/party/amount, bbox и
строки/колонки OCR-таблицы. Это основа, не повод создавать параллельный OCR.

- `_parse_tsv`: bbox `(left, top, width, height)` в pixels обработанного raster.
  `_preprocess_image` меняет ориентацию/наклон; inverse transform к original
  не входит в сохранённый metadata. `_union_bbox` эвристический, может объединять
  совпадающие токены на странице; это не доказанный clause anchor.
- `ExtractionResult.metadata`: page excerpt до 500 символов, fields с value/
  excerpt/bbox, cells максимум 2000, warnings. Текст сохраняется, несмотря на
  название metadata; его требуется включить в retention/ACL.
- `_native_page` и `_coerce_page` назначают эвристические confidence; aggregate
  mean/field score не calibrated truth probability. Не-PDF native extraction
  представляет текст как page=1, не реальную DOCX/XLSX pagination.
- `_xlsx_text` читает worksheet XML в табличный текст, но не сохраняет sheet
  identity/A1 provenance/formula отдельно; нельзя backfill точных ячеек из
  одной плоской строки. OCR table cells не эквивалентны Excel cell IDs.
- `ocr_batch.py:reprocess_documents`: durable job progress/cancel; низкая
  уверенность исключается из automation_ready. Но metadata/review status
  записываются на Document и перезаписываются повторным OCR, не per-version.
  Ветка batch явно ограничена google_drive/google_drive_copy и DriveClient,
  несмотря на provider-neutral текст docstring. Этот контракт не меняет её.
- `api/documents.py:list_ocr_review_queue/update_ocr_review/document_card`:
  просмотр viewer, review manager, UI получает evidence JSON. Review меняет
  только document status, без source_version/CAS/верифицированного fragment
  и отдельной истории reviewer; «confirmed» нельзя мигрировать в verified
  для всех версий документа.
- `test_ocr_commercial_hardening.py` проверяет TSV/fields/bbox/review и regex
  benchmark на синтетическом page text. Это не доказательство качества
  настоящего OCR на произвольных сканах; результаты старых запусков не
  переносятся как свежая приёмка этого контракта.

## Snapshot → safe-copy: важное расхождение

`api/workspace.py:queue_workspace_snapshot` сохраняет root/binding и создаёт
`workspace.snapshot`. `_build_snapshot` строит metadata nodes и после ready
вызывает `_start_safe_copy_pipeline`; тот ставит `workspace.safe_copy`.
`_run_safe_copy_pipeline` вызывает `_scan_worker(..., auto_apply=True)`.
В `organizer.py:_scan_worker` создаётся provider copy, её содержимое индексируется
как google_drive_copy или provider-specific copy source; copy IDs становятся
Document.external_id, а разрешённые high-confidence rename/move применяются
внутри копии. Оригиналы защищаются copy-only mutation checks.

Следовательно «оригинал не меняется» **не означает** «копий/передачи нет».
В этой цепочке нет нового organization no-copy/data-residency gate; готовый
snapshot автоматически продолжает копирование. Для federated reference-only
потребуется отдельное минимальное переключение workflow у его владельца.
В этом docs-only этапе ничего не отключено и не переименовано. Id_map копии
можно использовать только при доказанной provenance; из имени `*_copy`
невозможно восстановить revision оригинала.

## Encrypted staging: база против другого форка

| Проверка | Базовый SHA | Другой fork |
|---|---|---|
| Ветка/SHA | `66129dca3a4cb92f9f09bd87f19f5433ceeb87a0` | `codex/commercial-p0-encrypted-staging`, `372b661eefebb9c154dd847e8c331acc2b128d94` |
| Design doc | `docs/architecture/encrypted-staging-design.md`, explicitly architecture only | Тот же design + operations/result |
| ORM/storage implementation | Нет `models/staging.py` и `app/staging/` | Есть StagingObject, contracts/crypto/filesystem/service/handlers |
| Migration | Нет `e8a1c2d3f4b5_add_encrypted_staging.py` | Есть, parent f360a1b2c3d4 |
| Local upload | `api/local_upload.py`: base64 → extraction/index внутри API | Encrypted staging → durable local_upload.process |
| Gmail attachments | Staging job kinds отсутствуют в базовом dispatch | Fork добавляет gmail.attachment_import |
| Scope/lifecycle | Только проектный design | owner/project/job bindings, size/SHA256, encrypted name, wrapped DEK, expiry/delete metadata |

Проверено `git ls-tree` и `git show` обоих commits. `git merge-base
--is-ancestor 372b661 HEAD` вернул 1: fork не является предком базы.
Это дополнено проверкой отсутствующих файлов, а не только ancestry.
Описанные тесты staging в его отчёте — **заявленные там результаты**, здесь
не запускались; production availability/volume/encryption runtime не проверены.
Даже интегрированный staging не заменит source identity/evidence version и
не разрешает запрещённую локальную копию. Contract ссылается на staging ID,
не на ciphertext bytes/ключи и не требует переноса этого fork сейчас.

## Что предложено

[SourceReference/Evidence contract](../architecture/v54/source-evidence/contract.md):
stable scoped identity, immutable version observations, честный fallback
без revision/hash, typed locators, provenance/confidence, explicit assessments,
separate fragment ACL и representation descriptors. Существующие integer IDs
не заменяются UUID. [Lifecycle/API/migration](../architecture/v54/source-evidence/lifecycle-api-migration.md)
описывает предлагаемые endpoint, CAS, read/recheck, tombstones/retention,
reference-only gate и additive shadow backfill без provider I/O.
[Acceptance/integration](../architecture/v54/source-evidence/acceptance-integration.md)
содержит 40 плановых сценариев и открытые решения владельцев.

## Проверки этого изменения

- Статические read-only git/code проверки выполнены; исходный ТЗ прочитан.
- JSON-примеры: **PASS** — PowerShell ConvertFrom-Json и локальные assertions:
  уникальные IDs, source/version/representation FK и org scope, confidence,
  status=verified projection, bbox bounds, отсутствие representation при
  forbidden retention и два аккаунта с одинаковым external_id.
- **PASS: 9 относительных Markdown-ссылок** разрешаются в существующие файлы.
  File allowlist проверен; `git diff --check` без ошибок. Эти проверки относятся
  только к согласованности черновика, не являются runtime contract tests.
- Новый runtime/API/ORM не реализован, поэтому матрица SE-01..40 не запущена.
  Backend/frontend suites не запускались: их код и конфигурация не менялись.
- Не вызываются реальные providers, реальные пользовательские документы
  (кроме явно указанного ТЗ), credentials, production, OAuth, внешние AI.
- Результат — проект контракта, не «100% реализовано», не security/commercial gate.

## Файлы / передача

Только `docs/architecture/v54/source-evidence/{README.md,contract.md,
lifecycle-api-migration.md,acceptance-integration.md,examples.json}` и этот аудит.
Один собственный docs-коммит; SHA в итоговом ответе. Source/Evidence IDs
передаются ContextRelation и Approval/Execution/Ledger owners; их схемы не
дублируются. Push, merge и deploy не выполняются.
