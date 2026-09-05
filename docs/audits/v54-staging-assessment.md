# V5.4 encrypted staging assessment

## Статус

**ASSESSMENT COMPLETE / INTEGRATION BLOCKED.** Encrypted staging fork даёт
полезные crypto/storage заготовки, но не совместим целиком с
Federated Source-of-Truth/no-copy v5.4. До внедрения нужны
policy-gated materialization, SourceVersion/representation binding, derived-artifact
controls, safe-copy cutover и новая sequential migration.

## Исходная точка

| Параметр | Значение |
|---|---|
| Worktree | `pu-workspace-v54-staging-assessment` |
| Branch | `codex/v54-staging-assessment` |
| Base/HEAD до правок | `4db9d51496e25d7916ecc75a5dfdf61a930c8637` |
| Input staging commit | `372b661eefebb9c154dd847e8c331acc2b128d94` |
| Merge-base | `83774aac726acd4e27b349e9194f30783158bde8` |
| Divergence | current `23`, staging `1` commit после merge-base |
| Initial status | clean |
| Applicable `AGENTS.md` | Не найден в worktree/применимых ancestors |
| Current schema expectation/head | `a54f001c0a01` |
| Staging migration | `e8a1c2d3f4b5`, parent `f360a1b2c3d4` |

Исходный репозиторий был dirty на другой ветке; его файлы
не копировались и не изменялись. Остальные параллельные worktree
не затронуты. Staging commit изучен через `git show`/`git diff`;
cherry-pick не выполнялся. Production data, documents, `.env` и secrets не
читались.

## Изучено

- `docs/architecture/encrypted-staging-design.md`;
- staging fork result/operations, `backend/app/staging/**`, model, migration,
  specialized tests и diffs ingress/queue/worker/scheduler/Compose;
- v5.4 Source/Evidence contract, integration decisions, ownership, migration handoff,
  acceptance и integrated pilot report;
- current SourceReference/SourceVersion/Evidence models/facade, BackgroundJob wiring,
  snapshot/safe-copy и OCR temp/raster behavior.

## Карта threat model

| Риск | Статус fork | Вывод |
|---|---|---|
| Transient download | PARTIAL | Gmail/local bytes становятся durable encrypted, но download/decoding идут до v5.4 policy gate. |
| Temp/swap/OCR rasters | FAIL | Current OCR writes plaintext source/PDF/JPG/processed PNG into ordinary `TemporaryDirectory`; staging manifest это не учитывает. |
| Extracted text/quotes/embeddings | FAIL | Handler immediately runs indexing/tasks/drafts/governance from one coarse permission; no per-derived-class policy/retention. |
| Crash/restart/lease expiry | PARTIAL | Durable ciphertext/queue exist, but no materialization fence/manifest; cleanup partials can race an active writer. |
| Failed retention | PARTIAL | `deletion_failed` exists, but count is reported as cleaned and no proven alert/SLA/operator state; expiry from env, not policy. |
| Backup | FAIL | Guidance permits encrypted staging backups generally. No-retain requires exclusion; restore does not replay revocation/purge before reads. |
| Owner/project isolation | PARTIAL | owner/project/job checks exist; organization/source/identity epoch/live policy absent, and DB session is trusted wholesale. |
| Key loss/rotation | PARTIAL | Versioned current/previous KEKs exist; no secret-manager binding, rewrap ceremony, purge/restore proof or per-policy key destruction. |
| No-copy/no-retain | FAIL | Encryption treated as admission; policy not checked before download and all derived artifacts are outside lifecycle. |
| Provider source revoked mid-process | FAIL | Handler does not recheck provider identity/source availability/revision/policy before decrypt or commit. |

## Обязательные проверки

| Проверка | Результат |
|---|---|
| Staging encryption != authorization | **FAIL:** scope checks есть, policy admission/recheck нет. |
| Job payload only opaque IDs | **FAIL:** fork stores project/owner/checksum/size/MIME/message index; target contract is only opaque materialization request ID. |
| Source/evidence pin preserved | **FAIL:** no SourceReference/SourceVersion/representation/policy pins. |
| Read copy not masked as original | **FAIL:** handler creates `StorageObject` from staging but evidence representation provenance is absent. |
| No-copy before download | **FAIL:** local decode and Gmail attachment retrieval precede v5.4 data policy. |
| Cleanup completed/cancelled/failed/expired | **PARTIAL:** input ciphertext covered incompletely; derived artifacts, backup semantics and truthful purge failure are not. |
| Retry does not create extra copy | **PARTIAL:** same job can reuse staging, but admission→stage→enqueue has commit windows and concurrent ingress may stage before idempotency resolution. |
| Safe-copy not started after snapshot against policy | **FAIL in current base:** `_build_snapshot()` unconditionally calls `_start_safe_copy_pipeline()`. |
| Migration compatible with `a54f001c0a01` | **FAIL:** both descend from `f360a1b2c3d4`, creating two heads. |
| No second queue/storage truth | **PARTIAL/PASS direction:** BackgroundJob is reused and staging can be transient; present model still lacks formal representation ownership and must not become source/document truth. |

## Дополнительные code findings

1. `StagingStorage.read_chunks` Protocol объявляет два аргумента,
   а implementation/service используют третий `key_version`.
2. `stage_bytes()` выполняет internal commit; ingress затем enqueue-ит job
   отдельно. Crash/race может оставить READY object без job.
3. `read_bytes()` собирает весь plaintext в RAM, что увеличивает
   exposure и memory pressure; target contract должен быть streaming.
4. Worker добавляет `_job_id` в generic payload на лету; это не
   typed/fenced dispatch binding.
5. Queue `succeed`/`request_cancel` directly import staging cleanup, что
   сцепляет generic queue с одним consumer и не учитывает cleanup outcome
   в terminal transition.
6. `cleanup_expired()` увеличивает count даже если delete закончился
   `deletion_failed`; это не доказательство purge.
7. DEK обнуляется присваиванием immutable Python bytes
   `b""`; это не гарантирует wiping memory.

## Итоговое решение

**Selective port/reimplementation.** Не cherry-pick, не rebase и не wholesale
copy. Портировать только audited crypto/filesystem concepts и негативные
test ideas. Service/model/API/handler/queue integration перереализовать
вокруг current v5.4 Source/Evidence/policy и BackgroundJob contracts.

До закрытия gates нельзя заявлять, что current product поддерживает
v5.4 no-copy/no-retain для materialized/OCR flows. Reference-only pilot по-прежнему
должен deny fragment/materialization.

## Артефакты и проверки

- [Integration decision and reuse map](../architecture/v54/staging-integration/README.md)
- [Lifecycle](../architecture/v54/staging-integration/lifecycle.md)
- [Policy table](../architecture/v54/staging-integration/policy-decisions.md)
- [Migration plan](../architecture/v54/staging-integration/migration-plan.md)
- [Negative security tests](../architecture/v54/staging-integration/negative-security-tests.md)
- [Future file map](../architecture/v54/staging-integration/future-file-map.md)

Продуктовые tests не запускались: изменения docs-only и не
переносят staging code. Выполнены проверки Git topology, clean
scope, Markdown links/paths и отсутствия правок вне разрешённых docs.

## Открытые owner decisions

1. Канонические policy IDs/versions и decision owner для copy, derive,
   residency, retention, backup, offline processing и legal hold.
2. Разрешённые processing locations и доказуемые controls swap/temp/backup.
3. Retention/Purge SLA, recovery window и operator escalation.
4. Secret manager/KMS, rotation, key loss и crypto-erasure policy.
5. Transaction owner/schema для representation/materialization и queue dispatch binding.
6. Разрешён ли offline commit после provider/source revoke.
7. Отдельная policy для safe-copy; по умолчанию DENY.
