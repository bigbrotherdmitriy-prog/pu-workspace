# v5.4 local upload encrypted staging — synthetic audit

Дата: 2026-09-04. Ветка: `codex/v54-local-upload-staging`.
База: `f721634762944e8bf9020e99c50f504678291296`.

Вердикт: **local slice и synthetic contract готовы; production wiring
fail-closed до интеграции a05 lifecycle**.

## Область изменения

Local upload больше не извлекает и не индексирует документ внутри API-request.
После project-role gate весь batch валидируется, каждый файл шифруется через
существующий `StagingStorage`, а обработка передаётся единственной существующей
очереди `BackgroundJob` с kind `local_upload.process`.

Не изменялись Gmail/provider download, OCR/temp policy, frontend, schema,
Alembic migrations, production Compose и deployment. Push, merge, PR и доступ к
production не выполнялись.

## Граница a05

`LocalUploadLifecycleAdapter` — обязательная fail-closed граница composition.
Raw backend нельзя передать в `LocalUploadRuntime`. Adapter не импортирует
незавершённую a05 schema и не реализует вторую persistence-модель. Будущий a05
owner должен предоставить пять scoped durable операций:

1. `reserve`: idempotent request reservation с owner/project, fingerprint,
   opaque object/fence и retention;
2. `publish`: CAS binding зашифрованного descriptor, checksum и exact size;
3. `bind_job`: durable binding к существующему `BackgroundJob.id`;
4. `load_for_processing`: загрузка только по opaque staging ID и текущему
   claimed job ID с восстановлением server-side scope и manifest;
5. `finalize`: durable terminal decision и explicit cleanup authorization.

Отсутствующий/частичный backend, несовместимый DTO или любое его исключение
нормализуются в `local_upload_lifecycle_unavailable`. Текст исключения не
попадает в HTTP detail или лог. Пока composition не установит полностью
совместимый adapter, API возвращает `503` и не создаёт staging bytes/job.

## Queue и privacy contract

Разрешённый job payload имеет точную форму:

```json
{"staging_id":"<32 lowercase hex>"}
```

В payload запрещены bytes/content/base64, filename/display name/path/locator,
checksum/size/MIME, owner/project, idempotency value, KEK reference/version,
wrapped DEK и filesystem metadata. Client idempotency value хешируется вместе
со scope до persistence. API response содержит только opaque staging/job IDs и
счётчики со статусом queued.

Worker требует настоящий queue claim, загружает все binding metadata через
lifecycle и до decrypt проверяет staging/job, scope DTO, normalized display
name/MIME, checksum/size limits и descriptor/object/KEK binding. Ciphertext
полностью аутентифицируется и plaintext checksum сверяется перед processor.
Processor result имеет exact allowlist целочисленных счётчиков и document IDs;
произвольный content/path/key result отклоняется.

Логи содержат только stable error type (`processing_failure`). Ни exception
message, ни payload, ни имя/путь, ни содержимое, ни cryptographic material не
логируются этим slice. Infrastructure error в API — content-free `503`.

## Durability и cleanup

`enqueue()` сохраняет published lifecycle row вместе с durable job в текущем
public queue contract. Затем `bind_job` выполняется как отдельный durable
transition. Узкое падение между этими шагами восстанавливается повтором того же
request: queue idempotency возвращает прежний job, после чего binding
повторяется. Ошибка commit приводит к rollback и stable availability error.

Worker сначала durable-коммитит terminal lifecycle decision, затем выполняет
идемпотентное удаление ciphertext. Completed требует explicit
`delete_ciphertext=true`; при failure ciphertext сохраняется, если lifecycle не
разрешил удаление; cancellation очищается только по явному decision. Cleanup не
делается до durable finalization.

## Synthetic negative coverage

`test_v54_local_upload_staging.py` проверяет:

- filename/MIME normalization, owner/project scope и size/MIME admission;
- authorization до decode/staging и whole-batch admission до первого write;
- invalid base64, per-file/batch limits и stable 422;
- encrypted-at-rest bytes и отсутствие plaintext в `.enc`;
- exact one-ID queue payload и отсутствие file-derived/security metadata;
- same-request convergence и changed-content conflict;
- queue failure, lifecycle failure, malformed DTO и commit rollback без утечки;
- обязательный adapter и fail-closed missing/partial future a05 backend;
- current queue claim, exact payload shape и staging/job binding;
- corrupted descriptor KEK/path-like display metadata до decrypt;
- ciphertext tamper, plaintext checksum/size и processor result allowlist;
- completed/cancelled/failed finalize policy;
- commit-before-delete ordering и idempotent delete;
- отсутствие Telegram/provider side effect в local business processor;
- queued API response без file content/path/client idempotency value.

Проверки на Python 3.13.14:

- focused local upload: `28 passed`;
- staging/queue/local relevant suite: `114 passed, 2 skipped`;
- полный backend: `988 passed, 11 skipped`, один baseline failure в неизменённом
  `test_v54_source_evidence_pilot.py::test_transaction_is_required_and_correlation_cannot_leak_text`;
- тот же одиночный тест на чистой base-worktree `f721634` воспроизводит failure:
  Python 3.13 включает строку вызова теста с synthetic marker в
  `traceback.format_exception`, то есть это не local-upload regression.

`git diff --check` выполняется перед commit. `.pytest-tmp` является только
локальным test artifact и не входит в commit.

## Явные блокеры следующего интегратора

Этот commit намеренно не включает a05 model/migration/composition. До включения
feature должны быть интегрированы authoritative Source/Version/policy pins,
tenant/organization scope, live revoke/offline-processing checks, retention
owner и recovery/purge reconciliation. Разрешение на local staging само по себе
не разрешает OCR, AI/provider action, derived artifacts или permanent copy.
