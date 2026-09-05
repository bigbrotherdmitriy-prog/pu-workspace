# v5.4 local upload encrypted staging — synthetic audit

Дата: 2026-09-04. Ветка: `codex/v54-local-upload-a05-wiring`.
База: `7509767`.

Вердикт: **local slice содержательно подключён к a05 SourceVersion и
MaterializationLifecycle; composition остаётся fail-closed без явно
инъецированной authority**.

## Область изменения

Local upload больше не извлекает и не индексирует документ внутри API-request.
После project-role gate весь batch валидируется, каждый файл шифруется через
существующий `StagingStorage`, а обработка передаётся единственной существующей
очереди `BackgroundJob` с kind `local_upload.process`.

Не изменялись Gmail/provider download, OCR/temp policy, frontend, schema,
Alembic migrations, production Compose и deployment. Push, merge, PR и доступ к
production не выполнялись.

## Реальная граница a05

`LocalUploadLifecycleAdapter` остаётся fail-closed границей composition, а
`A05LocalUploadLifecycle` является её concrete backend. Вторая persistence-
модель и новая очередь не создавались. Backend пишет настоящие
`SourceReference`, immutable `SourceVersion`, `Evidence` и
`Materialization`, а переходы representation делегирует существующему
`MaterializationLifecycle`.

Связи точные и проверяются перед каждым чтением/terminal transition:

- authenticated owner и project восстанавливаются server-side из
  materialization и сверяются с injected authority;
- `staging_id` является hyphenless UUID того же materialization/representation;
- SourceVersion, Evidence, representation и storage object связаны FK и
  manifest pins;
- BackgroundJob находится по exact idempotency binding и обязан иметь payload
  только `{"staging_id": ...}`;
- worker claim сравнивается с job id, worker id, attempt, locked_at и живой
  `lease_expires_at`.

`authority_factory` — обязательная server-side composition dependency. HTTP
body/job payload не может создать policy grant. Без неё runtime по-прежнему
возвращает стабильный `503` до первого staging effect.

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

## Durability, lease и cleanup

Границы транзакций явные: admission/fence commit выполняется до ciphertext
write; sealed/derived SourceVersion binding commit — до enqueue; job binding
commit — до ответа; live lease/source authorization commit — до decrypt/read;
business result commit — до finalize; terminal decision commit — до delete;
purge tombstone commit — после idempotent delete.

Completed и cancelled переходят `DERIVED → EXPIRED`, сохраняя content-free
outcome/result и storage descriptor для restart recovery. После durable
finalization ciphertext удаляется, затем lifecycle создаёт `PURGED` tombstone.
Crash в любом из этих промежутков повторяет delete/purge без повторной обработки.
Failed остаётся `DERIVED`, сохраняет ciphertext до retention и может быть
повторно claimed; старый/истёкший claim не может читать или финализировать.

## Synthetic negative coverage

`test_v54_local_upload_staging.py` и
`test_v54_local_upload_a05_wiring.py` проверяют:

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
- queued API response без file content/path/client idempotency value;
- exact Source/SourceVersion/Evidence/representation/object/job binding;
- full SQLite round trip, idempotent re-admission and safe audit surfaces;
- completed/cancelled purge, failed retention и restart между finalize/delete;
- expired lease rejection и recovery новым worker claim.

`test_v54_local_upload_a05_postgres.py` условно проверяет на отдельной
локальной test-БД конкурентные old/current lease attempts: только current
worker получает authorization на materialization read.

Проверки на Python 3.13.14:

- real-a05 SQLite integration: `5 passed`;
- focused lifecycle/local/conditional PostgreSQL: `42 passed, 3 skipped`;
- полный backend: `1033 passed, 14 skipped`.

`git diff --check` выполняется перед commit. `.pytest-tmp` является только
локальным test artifact и не входит в commit.

## Явная rollout граница

Новая migration, queue, production Compose и автоматическое production-enable
не добавлялись. Production runtime должен явно передать policy-backed
`authority_factory`, KEK resolver, residency и private shared storage. Разрешение
на local staging само по себе не разрешает OCR, AI/provider action, derived
artifacts или permanent copy.
