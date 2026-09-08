# v7 persisted calendar graph: bounded API vertical slice

Date: 2026-09-08. Base `890b81a6f6be0590981c28fa33628aafb8112c0b`.
Branch `codex/v7-schedule-persistence-wave2`, separate worktree; original dirty
worktree and production unchanged. No push, merge or deploy.

## Implemented

New `GET/PUT /execution/baselines/{baseline_id}/graph` uses the existing pure
`plan_schedule` core, existing project roles, schedule models and AuditLog.
No preview-only substitute, second queue/registry, external effect or UI change.

- Complete existing item set (maximum 500 in PUT), strict IDs/durations/booleans,
  explicit anchor, milestone, link grammar, constraints and not-before inputs.
- Persistent intent is distinct from computed planned dates; moving upstream
  earlier correctly moves descendants earlier. All output dates/float/critical
  edges come from the same validated pure result, not a browser algorithm.
- Project advisory serialization -> Project row -> actor/membership shared row
  locks -> refreshed baseline -> complete ordered item rows. Permission is
  rechecked from fresh rows; archived projects and pending scope edits deny.
  This preserves current `require_project_role` semantics, including its existing
  administrator role rule; it does not replace it with v5.4 Action Authority.
- Draft-only mutation and exact `graph_revision` CAS with UPDATE rowcount=1;
  one transaction writes all inputs, dates, revision and audit. Failure after
  item SQL flush rolls everything back. Repeated stale PUT returns 409, not a
  fabricated durable replay receipt. No actual facts/source data are modified.
- Existing legacy create/import bumps revision only on real additions (one per
  import batch, not per row/replayed row). Calendar graph mode rejects legacy
  insert/import with 409 until a separate graph-aware batch insert exists.
- Approval requires the reviewed graph revision, validates stored graph and
  increments lifecycle revision; supersession increments the old revision too.
  Stale approval cannot bypass CAS through `already_applied`.
- Clone validates the source before draft insertion, copies input mode/anchor,
  remaps all predecessor IDs to new IDs, retains planned dates, clears facts.
  Corrupt dates-only intent denies before DML rather than raising KeyError.
- GET/clone/approval reject inconsistency between stored graph and calculated
  dates. PUT may repair such an editable draft using explicit complete inputs.
  GET is read-only but holds transaction locks until request DB dependency closes.

## Migration and legacy policy

One new sequential revision `a54f001c0a20`, parent `a54f001c0a19`.
`CURRENT_SCHEMA_REVISION` updated; historical migrations are unchanged.
Integrator must update current runtime/test schema pins to a20; do not replace
historical a18->a19 migration test commands.

Baseline: positive BIGINT graph_revision default 1; planning_mode defaults
dates_only; project_start nullable. Item inputs duration/is_milestone/links/
constraints/not_before are nullable with no invented defaults. Row-local checks
explicitly handle SQL NULL logic. Existing planned/actual/source values are not
backfilled or recalculated. Legacy fields become explicit only on full activation.

Downgrade first takes ACCESS EXCLUSIVE locks on both schedule tables, then rejects
any activated graph, anchor or any non-null item intent. This closes both silent
loss of legacy-mode intent and check/drop TOCTOU. Only entirely unactivated test
data can downgrade directly; active deployment rollback requires a verified
compatible backup/restore. No production migration or backup was executed.

## Verification and exact commands

RED before implementation: **12 failed**, missing graph endpoints/columns and
legacy mutation revision behaviour. Added actual route, scope, complete graph,
stale revision, no partial writes, actual-fact preservation, clone, import replay,
HTTP JSON round-trip and audit-failure rollback tests. The HTTP test overrides
authentication with a synthetic user but exercises real roles, route and DB code;
it is not production login/CSRF acceptance.

From backend with shared test Python:

```text
python -X utf8 -m pytest tests/test_v7_schedule_graph_api.py tests/test_v7_schedule_graph_postgres.py tests/test_v7_schedule_graph_migration.py tests/test_v7_schedule_api_boundary.py tests/test_v7_schedule_planner.py tests/test_mvp4_gpr_baseline.py tests/test_v7_schedule_import_validation.py tests/test_execution_finance_api.py tests/test_structured_import.py -q --tb=short --basetemp=.pytest-graph-persist-final
```

Result: **320 passed, 4 skipped**, 5.05 seconds. Three existing Alembic
`path_separator` deprecation warnings. After the final approval-replay tightening:
**31 passed** graph API + baseline tests. `git diff --check`: PASS.
Final complete scoped rerun after that tightening: **320 passed, 4 skipped,
3 warnings, 4.36 seconds**.
Offline a19->a20 SQL generation, exact single head, no data backfill, nullable
intent, and downgrade lock-before-guard-before-drop ordering are tested.
Offline SQL is **not** actual PostgreSQL execution.

Prepared mandatory PostgreSQL nodes, using `PUW_MVP4_TEST_DATABASE_URL`:

```text
tests/test_v7_schedule_graph_postgres.py::test_postgres_graph_cas_allows_one_complete_winner
tests/test_v7_schedule_graph_postgres.py::test_postgres_upgrade_legacy_and_safe_downgrade
tests/test_v7_schedule_graph_postgres.py::test_postgres_downgrade_refuses_graph_intent[active_graph]
tests/test_v7_schedule_graph_postgres.py::test_postgres_downgrade_refuses_graph_intent[legacy_intent]
```

Fixture accepts only PostgreSQL owned `puw_mvp4_test_*`, localhost/loopback/db,
plus `postgres` only under GITHUB_ACTIONS=true; caller URL options denied. Each
test creates and migrates its own random schema to a20, checks current database
and READ COMMITTED, and removes only that schema with lock/statement timeouts.
No Base.metadata shortcut is used for PostgreSQL. The upgrade case downgrades
unactivated a20 defaults to real a19 tables containing synthetic legacy rows,
then runs actual a19->a20 and compares old values. Refusal checks SQLSTATE P0001
and exact safe error code, head and retained intent—not arbitrary exceptions.
Concurrent two-session PUT tests require one 200, one 409 and one coherent
saved plan/audit. These four cases are **NOT RUN** locally: owned PostgreSQL URL
is absent. No PostgreSQL CAS/revocation/downgrade race PASS is claimed.

## Limits and integration

Status **CONDITIONAL** until PostgreSQL/migrations and aggregate CI execute.
Root/integrator owns full backend regression and head pin changes; this branch
did not run the full backend, frontend suite, browser E2E or Docker.

This is not all M4-02: no WBS/rollups, business calendars/holidays, resources,
ALAP, graph-aware add/delete, or UI editor wiring. Manual project-specific
anchor/duration/milestone intent is needed at activation; migration must not
invent it. The existing global administrator role policy is unchanged.
GET's exclusive Project lock is conservative; lock-contention performance and
revocation-versus-edit interleaving need real PostgreSQL acceptance. There is no
durable replay journal or exactly-once claim, nor protection from arbitrary SQL
writers that bypass the application protocol. Mixed old/new writers after graph
activation are unsupported. The package is not a production deployment.

Changed files: execution_finance API and models; app/schema.py; one a20 migration;
three new test files; this report. No finance/payment business rules were changed.
