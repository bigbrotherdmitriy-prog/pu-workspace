# V7 M1-02: actual metadata snapshot recovery

Date: 2026-09-08. Base `8794e1feed61ad5c8fd8c462e0d4e2059bfb7ac1`.
Branch `codex/v7-snapshot-recovery-wave4`; separate clean-base D: worktree.
No AGENTS.md found in this worktree or D: workspace parents.

## Initial audit / reproduced defects

Existing `scripts/ci/durable_queue/workspace_checks.py` runs before workers. It
checks enqueue/retry reconciliation but not actual `workspace.snapshot` execution.
The actual handler calls `_build_snapshot`, whose original implementation checked
binding only before provider traversal, then published old ORM state without a live
delivery fence. Its exception path could overwrite recovery results with failure.
Production caller inventory found only `jobs/handlers.py`; direct calls were tests.

Before product edits:

```powershell
# cwd: this worktree/backend; TEMP and TMP under D:/PU-Workspace/tmp
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest -q tests/test_v7_snapshot_recovery.py --tb=short --basetemp=D:/PU-Workspace/tmp/snapshot-wave4-red
```

**RED: 8 failed, 4 passed, 39.76s.** Both synthetic Google and Yandex providers:

1. An expired owner was allowed to start provider reads.
2. An owner replaced during tree traversal could still publish.
3. Connection rotated during traversal was not revalidated before publication.
4. Direct actual handler execution left progress at claim's initial value.

Root authorized the minimal workspace fix and later the existing test adaptations
to use actual claims instead of preserving an unfenced test-only bypass.

## Implementation / lock and transaction boundaries

- Reuse existing queue `execution_owner`, captured `current_execution_claim`, and
  `_live_owner`; verify job ID, worker, attempt, locked_at, live lease, kind and
  exact snapshot/project/folder payload match. No caller-supplied owner/generation.
- No unfenced synchronous fallback, including when a durable job already exists.
- Short lock order: Project → exact live BackgroundJob → WorkspaceSnapshot.
- Validate current storage binding before reads; commit/release these locks before
  resolving adapter and doing get_object/walk_tree. No content read or copy/rename.
- Roll back the handler's own read session after I/O to discard pre-I/O ORM state;
  reacquire the same fence and fresh binding in the node publication transaction.
- Ready result is immutable/reused after crash following DB commit. A stale owner
  cannot publish or mark a newer owner's ready result failed.
- Failure updates require the same fresh live fence and never overwrite ready.
- Coarse progress checkpoints 10 (admitted traversal) and 95 (metadata committed),
  existing worker/queue completion sets 100. Not per-file progress or an ETA.
- Existing source binding/project and nested parent IDs remain exact.
- No changes to handlers, worker/scheduler, queue implementation, payload schema,
  models, Alembic, App, provider integration, main CI or production.

## Checks and evidence limits

Synthetic HTTP calls use the actual FastAPI workspace router. Repeated selection
reuses snapshot/job and restores the same binding in fresh sessions. The header
`Idempotency-Key` is supplied, but the current route implements **semantic folder
deduplication**, not a general arbitrary HTTP key registry; no broader claim made.

SQLite tests exercise the real handler/queue, nested folders for both providers,
changed binding, before/during/after-publish failure, exact delivery generation,
no-context denial, safe error state and old-owner completion denial. They do not
prove PostgreSQL row-lock concurrency or OS restart. Simulated BaseException and
lease rewinding are labelled as local regression instruments, not real process kill.

The first fix run: **80 passed, 196.37s** (12 initial new + related storage tests).
Final strengthened targeted/related run: **117 passed, 164.36s, no skips**:

```powershell
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest -q tests/test_v7_snapshot_recovery.py tests/test_storage_binding_validation.py tests/test_parallel_validation_integration.py --tb=short --basetemp=D:/PU-Workspace/tmp/snapshot-wave4-final
```

Final metadata-only guard rerun: **27 passed, 55.43s**. `py_compile` of the new
runtime phase: PASS. `git diff --check`: PASS. Full backend is delegated to the
root integration run; no unrelated frontend checks were repeated here.

## Prepared real PostgreSQL/process phase (NOT RUN locally)

New opt-in entrypoint:
`scripts/ci/durable_queue/workspace_snapshot_checks.py`.

Prerequisites: a freshly migrated **empty dedicated** PostgreSQL database. Explicit
`PUW_SNAPSHOT_RECOVERY_TEST=1` and `APP_ENV=test|ci`; only local/db hosts, owned
`puw_v7_test_*` or `puw_queue_test` database name, no DSN query options. It checks
the current schema revision and empty Project/User/Organization/BackgroundJob
tables before seeding. No production DB, credentials or provider objects.

Exact integration command contract (test values supplied by isolated runner):

```bash
# From backend, with a NEW test DATABASE_URL, not a production .env:
export APP_ENV=test
export PUW_SNAPSHOT_RECOVERY_TEST=1
python -m alembic -c alembic.ini upgrade head
PYTHONPATH="$PWD" python ../scripts/ci/durable_queue/workspace_snapshot_checks.py
```

The phase uses actual `worker.main` in two subprocesses and the real handler. Only
the provider is injected synthetic metadata. An existing AuditLog test checkpoint
records entry into walk, then worker A waits. Worker B starts but cannot claim A's
live delivery. Coordinator kills only its child A, waits for **real 60s lease expiry**
(no SQL expiry shortcut), and checks B completes attempt 2, exactly 3 nodes, proper
binding/progress and no duplicate HTTP job. All child processes are stopped through
their own handles in finally; no global cleanup. Database remains for inspection.

Allowlisted JSON contains phase/status, local numeric IDs/counts, duration and
booleans. Child stdout/stderr are discarded; raw exception/DSN/payload is not
published. This protects artifacts but is not a comprehensive runtime log scan.
Safe failures identify the phase only. Phase list: guard, seed_http, first_walk,
second_worker, kill, lease_expiry, recovered, replay.

**Not connected to main CI yet**: integrator must supply an empty owned database
and invoke this phase. Existing populated durable_queue fixture database cannot be
reused in place because the explicit empty-data guard will reject it.

NOT RUN: real PostgreSQL locks, worker kill/expiry/recovery, two API OS processes,
API/Compose restart, live providers. Fresh TestClient sessions are not claimed as
API-process restart. The prepared JSON explicitly says `api_process_restart=NOT_RUN`.

## Remaining risks / next integration requests

- Existing enqueue route holds Project lock across provider get_object. This
  pre-existing admission behavior is outside this handler fix and needs a separate
  optimistic admission/CAS design; root explicitly kept it out of this wave.
- Provider adapter construction resolves current credentials by project. There is
  no new frozen credential-generation adapter interface here. Commit-time binding
  validation prevents publication after detected changes; it is not a universal
  provider credential epoch/remote-access revocation guarantee.
- No end-to-end stale-owner guarantee is claimed for workspace.analysis or
  workspace.safe_copy; only metadata snapshot handler was changed.
- No safe-copy or content analysis is auto-triggered by snapshot completion.
- PostgreSQL/runtime remains **CONDITIONAL**, not PASS. Main runtime workflow,
  Alembic expectations and deployment were deliberately not changed.

No push, merge, PR or production changes.
