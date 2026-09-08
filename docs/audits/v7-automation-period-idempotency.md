# M7-02: monthly automation period idempotency

Date: 2026-09-08. Base: `5226782908dff519a2bf2c6b9d320b59f2972c10`.
Branch: `codex/v7-automation-period-idempotency`.

## Defect and bounded implementation

The unchanged [manual API](../../backend/app/api/ai_secretary.py) passes today's
date to `prepare_rule_run`. Previously, two dates in the same month produced two
AutomationRuns and two Task/ResponseDraft pairs. The existing
[schema](../../backend/app/models/automation_rule.py) uniquely constrains only
`(rule_id, scheduled_for DATE)`, not a month.

[automation_engine.py](../../backend/app/automation_engine.py) now uses the first
calendar day of the requested month as the **period identity** for `monthly_email`:
new AutomationRun.scheduled_for and Task/Draft source identity use this date.
This is not a changed dispatch time. The original requested occurrence still
controls template `{date}`, Task.due_date, last_run_on and the existing
following_monthly_date calculation. Calendar clipping for days 29–31, leap
February and adjacent years is unchanged. Other rule kinds retain date identity.

Before creating anything, the existing rule row is locked and a month-range
query returns at most one prior run, ordered by scheduled_for then id. This
includes legacy noncanonical run dates without rewriting records or touching
their Task/Draft. Existing duplicates are not deleted or repaired; the earliest
existing run is reused and no additional pair is created. An incomplete legacy
run is not silently reconstructed. There is no new ledger, queue, table,
migration, API mutation or confirmation/send behavior.

The scheduler captures due dates with its initial query and passes that date
explicitly. Refreshing a rule while waiting for a concurrent worker must not
accidentally prepare its newly advanced, future next_run_on. The scheduling
selection, following-month calculation and counters otherwise remain unchanged.

Review correction: scheduler preparation now sets `require_active=True` and
checks the refreshed active flag under that same rule lock. A pause committed
after the initial due snapshot skips the pair without incrementing prepared or
failed (`due` remains the original snapshot count). The unchanged manual API
historically allows explicit preparation of a paused rule; the default remains
`require_active=False`, preserving that contract without enabling automatic sends.

`prepare_rule_run` rejects pending new/dirty/deleted changes to its input rule
before reading potentially expired attributes or issuing a refresh query. The
owner must explicitly flush or roll back the edit and retry. This is a narrow
preparation boundary, not a replacement for global authority checks or a change
to the scheduler's existing transaction ownership.

## Transactions and rollout boundary

The existing transaction includes Task, Draft, Run and next-run update with one
commit. A synthetic failure after pair flush but before Run persistence is rolled
back; a new session can replay into one complete pair. The helper retains its
existing commit ownership. Callers must still roll back after a failed transaction
(the scheduler already does so); this slice does not add generic retry policy.

On PostgreSQL, upgraded callers lock the same rule before lookup/creation; the
existing DATE unique key also protects new canonical run identities. No uniqueness
migration is required for the upgraded engine path. SQLite tests do not establish
PostgreSQL lock ordering or concurrency. A real two-session PostgreSQL test is
provided separately, using the normal READ COMMITTED configuration.

**Mixed old/new writers are not safe for a rolling overlap:** an old engine can
still insert a different date in the same month without acquiring the new lock.
Deployment must quiesce old scheduler/API writers before enabling this version.
Direct SQL writers are likewise outside the engine guarantee. Existing duplicate
cleanup requires a separate owner-reviewed operation; this change does not claim
that historical duplicate data has disappeared.

## Verification

[Real-session regressions](../../backend/tests/test_v7_automation_period_idempotency.py)
initially reproduced **15 failures / 5 passes**, including the four existing
automation controls. The first corrected profile was **20 passed**. Additional
controls exercise the real due scheduler, captured due snapshot, legacy duplicates,
nonmonthly compatibility and explicit leap/short-month dates.

Pre-review scoped profile: **33 passed, 1 conditional PostgreSQL test skipped in
32.07s**, running `test_ai_secretary_automation.py`,
`test_v7_automation_period_idempotency.py` and `test_v7_automation_period_postgres.py`.
No owned PostgreSQL URL was configured locally, so the concurrent database test
is **not executed**, not a PASS. No full backend suite or deployment was run.

[Conditional PostgreSQL test](../../backend/tests/test_v7_automation_period_postgres.py):

`tests/test_v7_automation_period_postgres.py::test_postgres_two_manual_days_serialize_to_one_period_pair`

It uses two independent sessions, verifies equal Run/Task/Draft IDs and exactly
one pair, then replays a third date. Configuration is exclusively
`PUW_V7_AUTOMATION_DATABASE_URL`. Hosts are localhost, 127.0.0.1 or ::1; the `postgres`
service hostname is accepted only when `GITHUB_ACTIONS=true`. Database names must
start with `puw_v7_test_` or `puw_v54_test_` (e.g. `puw_v7_test_automation_period`),
and URL query overrides are rejected. The test creates and removes only its own
random `v7_automation_period_<uuid>` schema. Guard regressions verify CI service
admission, rejection outside CI, remote/prod/query targets and malformed URLs;
errors contain a fixed code, not the DSN.

The review correction reproduced **6 failing / 1 passing** pause/pending controls
before the fix: a separately committed pause between snapshot and preparation,
pending active/name/day edits, pending deletion, and an expired next-run attribute
with pending pause. Manual paused preparation remained passing. The scheduler
snapshot advancement test now also changes the row in a separate committed
session, rather than simulating an unflushed caller edit.

Final corrected scoped profile: **40 passed, 1 conditional PostgreSQL test skipped
in 41.67s**, using the same three test files. PostgreSQL execution remains delegated
to the separately owned integration/CI runtime; no local concurrency PASS is claimed.

No live mail, external provider, production database or real user data is used.
This is only M7-02's engine slice, not MVP7 completion. Template edit/approval
invalidation, external CONFIRM policy, payment boundaries and Information Center
evidence remain separate requirements.
