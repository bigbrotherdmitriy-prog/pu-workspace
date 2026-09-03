# Encrypted staging и Federated Source-of-Truth v5.4

## Решение

Коммит `372b661eefebb9c154dd847e8c331acc2b128d94` нельзя cherry-pick или
rebase целиком. Рекомендован **selective port с перереализацией
оркестрации**:

- переиспользовать проверенные идеи chunked AEAD, opaque filesystem key,
  atomic write, versioned KEK и cleanup partials;
- не переносить API, handler, queue hooks, model и миграцию без
  перепривязки к SourceReference/SourceVersion, policy pins и текущей
  BackgroundJob;
- создать staging как краткоживущую `representation`, а не как новый
  source, archive или storage truth.

Причина: staging-fork ответвился от
`83774aac726acd4e27b349e9194f30783158bde8`; текущая база на 23
коммита вперёд, fork — на один коммит. Прямой diff затрагивает
157 файлов и удаляет уже интегрированные v5.4 contracts, facades,
CI и tests. Миграция fork `e8a1c2d3f4b5` и текущая
`a54f001c0a01` имеют одного parent `f360a1b2c3d4`, то есть дадут
две Alembic heads.

## Граница доверия

```text
Provider original
  └─ SourceReference + immutable SourceVersion
       └─ policy decision (access/copy/derive/residency/retention)
            └─ authorized materialization request
                 └─ encrypted staging representation (ephemeral)
                      └─ existing BackgroundJob, ID-only payload
                           └─ derived representation/evidence
                                └─ cleanup + tombstone/audit
```

Encryption защищает разрешённую копию; она не выдаёт разрешение
на её создание. До download должны быть разрешены как минимум:
actor, tenant/project, provider identity/binding epoch, exact source/version,
purpose, operation, copy/derive classes, processing location, retention, backup
and legal-hold interaction. Отсутствующий или неактуальный pin означает
deny.

## Existing → reuse → gap/conflict

| Область fork | Что можно reuse | Gap/conflict v5.4 |
|---|---|---|
| AES-256-GCM chunks, per-object DEK, KEK wrapping, key version | Примитивы и test vectors после crypto review | Env с ключами не доказывает secret-manager ACL, rotation ceremony, destruction и restore/revocation ordering. |
| Opaque path, traversal guard, atomic `.part-*`, checksum | Да; исправить Protocol signature и lifecycle races | Cleanup partials удаляет все partials без age/active-writer fencing; нет durable deletion claim. |
| `StagingObject` owner/project/job binding | Идея scoped materialization | Нет organization, SourceReference/SourceVersion, representation ID, policy/version, purpose, credential epoch, derivation classes, backup/legal-hold decision. Staging ID не должен заменять source identity. |
| Job payload without bytes | Только направление | Payload содержит owner/project/checksum/size/MIME и Gmail indexes. v5.4 contract требует opaque IDs; hash тоже может быть sensitive. |
| Existing BackgroundJob | Обязательно reuse единственной queue | Fork меняет shared queue/worker/scheduler и вставляет `_job_id` в payload; нужен current handler contract и отдельная dispatch binding. |
| Local upload staging | Stream/encrypt primitives | Bytes сначала base64-декодируются; нет source version и policy decision before materialization. |
| Gmail attachment staging | Authenticated provider adapter как ingress | Gmail body скачивается в API до no-copy/retention/residency gate; exact provider revision/source pin не сохранён. |
| `read_bytes()` integrity | Checksum verification | Весь plaintext собирается в RAM; authorization по owner/project/job не заменяет live policy, source availability/revocation и binding epoch. |
| Handler → OCR/index/tasks/drafts/risks | Не переносить | Один read не даёт все derive permissions. OCR создаёт plaintext temp files/rasters; extracted text/evidence не привязаны к actual representation. |
| Terminal cleanup | Success/cancel hooks как input | Cleanup после job success не покрывает derived temp/cache/text/embeddings; failure retention выдуман из env, а не из policy. |
| Backup guidance | Идея encrypted backup | Для no-retain даже encrypted backup запрещён; restore должен replay revoke/tombstone/purge до read. |
| Snapshot → safe-copy в current base | Существующий job/idempotency после cutover | `_build_snapshot()` без policy-gate автоматически запускает safe-copy. Пока это не устранено, no-copy claim недопустим. |

## Инварианты интеграции

1. `reference_only` не создаёт bytes, temp, raster, text, quote, cache,
   embedding, telemetry content или backup.
2. Materialization начинается только после server-side policy decision,
   pinned к exact source/version/policy versions и purpose.
3. Job payload содержит только `materialization_request_id`; job ID/lease
   не заменяют business identity.
4. Worker перепроверяет tenant/project, permission, identity/binding epoch,
   source availability/revision и effective policy до decrypt/read/derive.
5. Фактически прочитанная representation указывается в evidence.
   Staging/authorized copy не маскируются как provider original.
6. Все derived artifacts подчиняются своему allowlist и retention;
   permission to stage не разрешает OCR, indexing и AI.
7. Cleanup идемпотентен, fenced от active lease и охватывает original
   staging bytes, partials, temp, raster, text, cache, embeddings и backup policy.
8. Provider revoke или policy revoke до worker read блокируют read. Уже
   прочитанная копия не становится original; дальнейшая обработка
   зависит от pinned offline-processing policy.
9. Ни staging store, ни descriptor table не становятся второй очередью,
   Document Core или storage truth.

Подробно: [lifecycle](lifecycle.md), [policy table](policy-decisions.md),
[migration plan](migration-plan.md), [negative tests](negative-security-tests.md) и
[future file map](future-file-map.md).
