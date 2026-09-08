# M3-02: meeting confirmation process faults

Date: 2026-09-08. Branch: `codex/v7-meeting-commit-fault-wave4`.
Base: `8794e1feed61ad5c8fd8c462e0d4e2059bfb7ac1`.

## Scope and evidence boundary

This package adds an acceptance harness around the **real**
`MeetingProposalService.confirm` and its caller-owned SQL transaction. It does not
replace that service with a probe implementation. No product code, queue, ledger,
model, migration or shared CI runner is changed.

Meeting confirmation currently records its durable business result using the
existing `Obligation.task_id`, `record_version`, `TaskHistory`, confirmation history
and `ManagementProposalOrigin`. **It does not create a generic ActionReceipt.**
This harness tests the actual existing representation rather than inventing a
second receipt store or claiming Trust/T2 integration that this path does not have.

The fixture reuses existing synthetic XLSX ingestion, source policy, keys and
meeting proposal helpers. It applies real Alembic migrations to a fresh UUID
PostgreSQL schema, seeds synthetic principals, executes the existing staged XLSX
handler, verifies the original materialization is `PURGED`, then binds an exact
retained child evidence pin to a meeting proposal. Three retained children exist;
revoking the selected child must not silently substitute another available child.

## Five mandatory nodes / integration request

Use existing `PUW_MVP3_TEST_DATABASE_URL` with a freshly created owned database
named `puw_mvp3_test_*`. Existing URL validation rejects external hosts, arbitrary
database names, query options and SQLite. `postgres` service hostname is accepted
only in GitHub Actions. No DSN is printed or included in the checkpoint channel.

Pin these exact pytest nodes, each requiring one PASS and no skip:

```text
backend/tests/test_v7_meeting_commit_fault_postgres.py::test_pg_meeting_commit_fault[before_commit]
backend/tests/test_v7_meeting_commit_fault_postgres.py::test_pg_meeting_commit_fault[after_commit]
backend/tests/test_v7_meeting_commit_fault_postgres.py::test_pg_meeting_commit_fault[revoked_authority]
backend/tests/test_v7_meeting_commit_fault_postgres.py::test_pg_meeting_commit_fault[stale_binding]
backend/tests/test_v7_meeting_commit_fault_postgres.py::test_pg_meeting_commit_fault[revoked_child]
```

Shared workflow wiring is owned by the integration stream. Adding these five to
the existing 37 proofs makes 42; this package does not silently edit that contract.
Head remains `a54f001c0a20`. No timeout increase is requested; actual runtime must
be measured, and budget exhaustion must remain FAIL/NOT_RUN.

## Fault protocol / expected states

| Case | Synchronized boundary and action | Required observation |
|---|---|---|
| before_commit | Child completes real confirmation and flush; sends IPC checkpoint before commit; parent hard-kills it | Separate observer sees zero Tasks, zero TaskHistory and zero confirmation transitions before and after kill; fresh child creates one durable result; further replay does not duplicate |
| after_commit | Child commits real transaction; sends test-only checkpoint; parent kills before application acknowledgement | Observer sees exactly one Task, one TaskHistory and one confirmation transition; fresh process returns identical entity/task/version; further replay preserves counts |
| revoked_authority | First child is killed before commit; parent revokes DB-backed authority and increases epoch | Fresh process returns denied; zero new business effects |
| stale_binding | First child is killed before commit; actual versioned meeting edit supersedes the binding | Fresh process returns denied; zero new business effects |
| revoked_child | First child is killed before commit; selected child's assessment becomes unavailable | Fresh process returns denied despite other retained children; zero new business effects |

One proposal-creation `ObligationHistory` row already exists before confirmation.
Thus “zero history after rollback” means **zero new confirmation/Task histories**,
not deletion of legitimate proposal history. Successful confirmation has exactly
two total ObligationHistory rows: creation and transition.

The processes use `spawn`, fresh SQLAlchemy engines and the real database service.
They receive only the isolated connection configuration, schema and internal IDs;
no policy closure, application Session, source contents, KEK or mocked confirmer
crosses the process boundary. Confirmation checks retained metadata, not file bytes.

Checkpoints use a strict allowlist: stage plus positive entity/task/version IDs,
or a content-free denied/failed signal. The parent waits on IPC, **not sleep-based
timing guesses**, then calls `kill()` only on its recorded child. The 60-second child
wait is a safety ceiling, not a synchronization delay. Parent IPC and joins are
bounded. `finally` joins/kills owned children and closes pipe handles before the
fixture disposes engines and drops only its validated UUID schema.

## Local verification

- First invocation from repository root lacked `PYTHONPATH` and failed import;
  rerun from `backend/` used the correct application import root.
- Dedicated harness checks: **13 passed, 5 conditional PostgreSQL skips**.
  This includes an actual spawned process rejecting SQLite before connecting;
  it proves IPC/import portability only, not database fault recovery.
- Related suite before the last negative-spawn addition: **65 passed, 10 skipped**
  (five existing meeting PostgreSQL proofs plus five new ones), two existing
  Alembic configuration warnings.
- Final related suite: **66 passed, 10 conditional skips, 2 existing warnings in
  22.40 seconds**. `git diff --check`: PASS.

Command from `backend/`:

```powershell
$env:TEMP = 'D:/PU-Workspace/tmp'
$env:TMP = $env:TEMP
& 'D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe' -X utf8 -m pytest tests/test_v7_meeting_commit_fault_postgres.py tests/test_v7_meeting_retained_evidence.py tests/test_mvp3_meeting_source_binding.py tests/test_mvp3_meeting_binding_postgres.py tests/test_mvp3_meeting_binding_migration.py tests/test_mvp3_meeting_digest.py -q --tb=short --basetemp=D:/PU-Workspace/tmp/meeting-fault-related-final
```

## Decision / remaining limitations

**CONDITIONAL: the five real PostgreSQL fault scenarios are NOT RUN locally.**
No test DSN was configured. A Docker client was discoverable, but daemon capacity,
isolation and a test server were not validated or provisioned by this package.
No product defect was established, so no speculative product fix was made.

This does not prove HTTP delivery/ACK semantics, browser behavior, queue lease
recovery, live provider effects or production activation. “Before ACK” is the
service caller's boundary after SQL commit, represented by a test-only IPC marker;
there is no live HTTP response or external action. Cleanup can still fail on an OS
or PostgreSQL outage; such failures must not be called PASS.

The integration stream owns full backend execution and CI wiring. No production
database, client document, real provider, secret, App/frontend, original dirty
worktree, push, merge or deploy was touched.
