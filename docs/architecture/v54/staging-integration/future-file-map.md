# Точная карта будущей реализации

Это карта ownership, не разрешение менять все файлы одним PR.
Каждый этап должен иметь отдельные ownership и tests.

| Путь | Будущее изменение / owner |
|---|---|
| `backend/app/staging/contracts.py` | Новый/selected port: streaming storage protocol, delete fencing, artifact manifest interface; исправить fork signature mismatch. |
| `backend/app/staging/crypto.py` | Selected port после crypto review: AEAD envelope, KEK reference/version, rotation/rewrap; secret manager integration. |
| `backend/app/staging/filesystem.py` | Selected port: atomic encrypted chunks, no-follow/path controls, age+fence partial cleanup; no global active-file glob. |
| `backend/app/staging/service.py` | Reimplementation: policy-authorized materialization state machine, streaming ingress, no internal commits, scope/source/version/representation binding. |
| `backend/app/staging/handlers.py` | Reimplementation: live policy/source recheck, dispatch fence, per-derive authorization, actual representation evidence, manifest cleanup. |
| `backend/app/models/staging.py` | Reimplementation from approved schema; scoped materialization/manifest records, not fork model as-is. |
| `backend/app/models/v54_pilot.py` | Source owner only: minimal representation/materialization references if current JSON descriptor is insufficient; do not duplicate Source/Evidence. |
| `backend/app/source_evidence/facade.py` | Source owner: materialization admission/resolve/revoke API; foundation currently denies fragment because no materialization exists. |
| `backend/app/core/v54_interfaces.py` | Integrator-owned typed policy/materialization interfaces only; no storage implementation. |
| `backend/app/core/v54_permissions.py` | Policy owner: copy/derive/residency/retention/backup/offline-processing decisions; absent=deny. |
| `backend/migrations/versions/<new>_add_v54_materialization.py` | Integrator creates one additive migration after actual head; do not reuse `e8a1c2d3f4b5`. |
| `backend/app/jobs/handlers.py` | Queue owner wires existing BackgroundJob kind to ID-only handler after contract tests; no second queue. |
| `backend/app/jobs/scheduler.py` | Queue owner schedules recovery/purge by opaque request IDs after lifecycle implementation. |
| `backend/app/jobs/queue.py` | Change only if public queue contract cannot express cancellation/fencing; staging cleanup must not be imported directly into generic succeed/cancel. |
| `backend/app/jobs/worker.py` | Avoid fork-wide `_job_id` payload mutation; pass execution binding through handler context agreed with queue owner. |
| `backend/app/api/local_upload.py` | Ingress owner: streaming admission after Source/Version registration and policy; enqueue opaque request ID. |
| `backend/app/api/gmail.py` | Gmail owner: policy decision and provider revision check before attachment body download; no bytes in API logs/queue. |
| `backend/app/api/workspace.py` | Workspace owner: remove unconditional snapshot→safe-copy; make safe-copy an explicit policy-authorized operation. |
| `backend/app/organizer_engine/content.py` | OCR owner: policy-aware isolated temp/artifact manifest; no normal-disk/swap fallback; preserve extraction algorithms. |
| `backend/app/main.py` | Composition owner: validate configuration/capabilities without importing product secrets into diagnostics. |
| `docker-compose.yml` | Deployment owner: private shared volume or provider, secret reference, tmpfs/encrypted work area, backup exclusion, no host publication. |
| `backend/tests/test_v54_staging_policy.py` | Policy table, source/version pins, owner/project isolation, revoke and actual representation. |
| `backend/tests/test_v54_staging_lifecycle.py` | State machine, duplicate workers, crash/lease, cancellation, retention and purge failure. |
| `backend/tests/test_v54_staging_crypto.py` | AEAD/tamper/path/key rotation/loss/partial cleanup tests from selected fork ideas. |
| `backend/tests/test_v54_staging_ingress.py` | Fake local/provider streaming, pre-download gate, revision race, ID-only payload. |
| `backend/tests/test_v54_staging_ocr.py` | Temp/raster/text manifest, no-copy deny, cleanup and evidence representation mapping. |
| `backend/tests/test_v54_workspace_no_copy.py` | Snapshot must not auto-start safe-copy under deny/unknown policy. |
| `scripts/ci/v54_staging_runtime.py` | Synthetic PostgreSQL/shared-volume fault, backup/restore and log/payload inspection. |

## Порядок ownership

1. Policy + Source interfaces and schema request.
2. Staging crypto/storage/lifecycle implementation.
3. Queue wiring and fault tests.
4. Local/provider ingress cutovers.
5. OCR derived-artifact controls.
6. Workspace safe-copy cutover.
7. Deployment/backup proof and final integration gate.

Нельзя параллельно вносить несогласованные правки в shared
`jobs/**`, `models/v54_pilot.py`, `source_evidence/facade.py` и
`api/workspace.py`; их подключает общий интегратор.
