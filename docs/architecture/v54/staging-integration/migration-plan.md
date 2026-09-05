# План совместимого внедрения

Это последовательность, а не Alembic-миграция. DDL должен
создать общий интегратор от актуальной single head.

## Schema sequence

1. Не переносить `e8a1c2d3f4b5`: она и `a54f001c0a01` обе имеют
   `down_revision = f360a1b2c3d4`, что создаёт две heads.
2. Зафиксировать актуальную single head в интеграционной базе;
   в этой assessment-базе это `a54f001c0a01`.
3. Согласовать owner для representation/materialization schema. v5.4 Source
   facade остаётся единственным writer-ом Source/Version/representation
   descriptors; staging service владеет physical ciphertext/lifecycle.
4. Создать одну additive migration после actual head. Минимальные
   таблицы/поля должны покрыть:
   - stable materialization request identity and idempotency key;
   - tenant/project/source/version/representation scoped FKs;
   - policy-decision pin/version, purpose, allowed derive classes and expiry;
   - state, storage object opaque ref, key version, encrypted metadata;
   - job binding/fence, created/ready/processing/purge timestamps;
   - artifact manifest and deletion state/attempt/error-code;
   - provider observation actually read and revoked/purged tombstone.
5. Не хранить в этих таблицах bytes, plaintext name/path/URL, extracted
   text/quote, provider token/raw error. Hash хранить только если это
   разрешено policy и нужно для integrity.
6. После upgrade доказать one head, fresh install, upgrade от current head,
   rollback через feature flag без destructive downgrade.

## Runtime rollout

1. **Deny-only facade.** Ввести materialization API и policy decision со
   всеми unknown/absent как DENY. Без download и storage.
2. **Synthetic encrypted round-trip.** Подключить selected crypto/filesystem
   primitives к synthetic source/worker, existing BackgroundJob и ID-only payload.
3. **Lifecycle and cleanup.** До ingress включить fenced cleanup, crash/restart,
   duplicate worker, rotation/key loss, backup exclusion и metrics/alerts.
4. **Local upload cutover.** Зарегистрировать Source/Version, policy admission,
   stream encryption; убрать base64 durable assumptions. Не включать OCR
   пока derive policy и temp isolation не пройдут tests.
5. **Provider ingress cutover.** Gmail/Drive/Yandex скачивают body только
   после exact source/version policy gate; provider revision сверяется до body.
6. **OCR/derived artifacts.** Изолированный encrypted/tmpfs workspace,
   manifest для source/temp/raster/text/cache, actual representation evidence.
7. **Workspace safe-copy cutover.** Разорвать безусловный
   `_build_snapshot()` → `_start_safe_copy_pipeline()`. Safe-copy становится
   отдельной policy-authorized operation.
8. **Production proof.** Two API/two workers/scheduler, kill -9, lease expiry,
   cancellation, provider revoke, storage failure, backup/restore, key rotation and
   purge SLA на synthetic data. До этого no-copy/no-retain status не PASS.

## Compatibility and rollback

- Feature flags разделяют admission, provider ingress, OCR derive и safe-copy;
  откат выключает новые admissions, но не отменяет purge.
- Existing queue, Source/Evidence records, documents и provider objects не удаляются.
- Успешный rollback сохраняет tombstones/audit и доводит cleanup;
  нельзя возвращаться к API-process extraction для policy-controlled source.
