# MVP1 managed-copy lifecycle — 2026-09-05

Branch: `codex/mvp1-managed-copy-lifecycle`. Base: `8113e734af51d81280b00e721234ffc8e26096cf`.

## Scope and result

Durable cleanup orchestration and its synthetic acceptance are implemented. **Production readiness remains CONDITIONAL.** Neither live provider cleanup nor complete provider copy reconciliation is claimed.

Cleanup discovery no longer scans folder names. Only exact project/snapshot/session/source-binding records qualify. Original folders (including originals bound to another project), virtual/manual sessions, inconsistent bindings and cross-project source rows are excluded. The identity pins project, canonical provider, connection identifier and row, source folder and snapshot revision. Historical session/snapshot identifiers remain intact; successful cleanup appends per-item and final audit receipts.

The owner-only HTTP command uses an exact list-version and matching `Idempotency-Key`/command key. Repeated commands return the existing job, including after completion; reuse with another version fails. New cleanup generations exclude prior completed receipts while recovery includes the current command's already processed items. Processing uses the existing BackgroundJob handler and an explicit provider cleanup capability. Changed connections fail closed, including a legacy null connection identifier. No folder names authorize deletion.

Archived projects cannot start or execute a new copy. Snapshot retries pass a stable opaque managed key to the existing provider copy interface. This is an orchestration identity contract, not proof that current provider adapters completely implement crash reconciliation.

The project card waits for a completed job and validated count before showing “Копии удалены, можете архивировать проект”. Queued, running and retrying are polled; failed/dead-letter/cancelled states never display success. Wrong job identifiers, missing counts and an unconfirmed originals-preserved flag fail closed. An empty managed inventory is displayed as no managed copies, rather than proof that untracked copies were deleted.

## Validation

All inputs are synthetic. No real storage, credentials or production data were used.

Commands run from the worktree backend (Python executable is the shared `.venv-pu-workspace-tests/Scripts/python.exe`):

```powershell
python -m pytest tests/test_storage_binding_validation.py tests/test_project_lifecycle.py -q --basetemp=.pytest-managed-finish-20260905
python -m pytest tests/test_managed_copy_identity.py -q --basetemp=.pytest-managed-identity-20260905
```

The first profile covers both Google and Yandex synthetic adapters, stale cleanup version, mismatched key, HTTP replay after completion, immutable history, second cleanup generation, crash after provider effect, recovery after an item receipt, missing capability, outsider denial, another project's original and archived project denial. Result: **73 passed**, no skips, in 246.68 seconds.

Identity/worker-capability profile: **9 passed**. Frontend `npm.cmd test -- src/modules/projects/safeCopyCleanup.test.ts`: **5 passed**. `npm.cmd run check`: **PASS**. The first Vitest attempt was blocked by sandbox directory access; the approved local rerun passed. No test was skipped to hide that failure.

## Remaining gates

- Current real Google/Yandex adapters do not declare `supports_managed_copy_cleanup`; the worker deliberately hard-denies cleanup. Enabling the capability requires tested provider ownership proof and replay/reconciliation semantics, not simply setting a flag.
- Existing Google copy retry lookup uses a deterministic folder name and does not prove a completed tree after interruption. Yandex provider completion/reconciliation likewise remains an adapter-level gate. New managed-key creation therefore requires `supports_managed_copy_idempotency` and hard-denies real adapters lacking that proof, before source read/copy. Legacy callers without a managed key retain their previous non-key path. This commit does not claim exactly-once copies or endorse name-based ownership. Provider adapters were not modified in this bounded work package.
- Legacy or orphan copies without the exact snapshot/session binding remain outside managed cleanup. They are never guessed by name or silently deleted.
- Real PostgreSQL concurrency, lease/fencing during external effects, ownership changes during processing, live provider cancellation and provider-side descendant-original protection require the isolated runtime/provider acceptance flow. Receipt append behavior is tested; a DB-enforced immutable ledger or a new queue was not introduced.
- Concurrent cleanup/fencing is explicitly **not proven**: this handler does not yet enforce an `execution_owner` guard immediately before each provider effect. Live capability denial remains enabled. Worker receipt replay preserves the same result fields, including `originals_affected=false`, as first completion.
- Polling times out visibly after 90 attempts; durable processing continues and can be inspected in the job journal. This change does not introduce a persisted frontend polling subscription across reloads.
- Full application suites and integration conflict review belong to the parent integration worktree. No schema migration is needed.

No push, merge, deployment, original-document modification or production operations were performed.
