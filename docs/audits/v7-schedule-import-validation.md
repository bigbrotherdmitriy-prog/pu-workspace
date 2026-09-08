# v7 M4-03: schedule import field validation

Date: 2026-09-08. Base: `5226782908dff519a2bf2c6b9d320b59f2972c10`.
Branch: `codex/v7-schedule-import-validation` in its own clean worktree.

## Scope and contract

The existing schedule preview silently accepted invalid supplied start/finish
dates as `None`, reversed date ranges, and malformed progress values. The shared
money parser stripped non-numeric text (`50oops` became `50`, `1e1` became `11`),
defaulted unparsable values to zero, and left out-of-range values for API clamping.

The fix is schedule-only in `backend/app/structured_import.py`:

- Supplied nonblank start/finish dates must parse using the existing date formats.
- Finish cannot precede start when both dates are present. Same-day dates are valid.
- Progress must be an entire decimal number, with decimal dot/comma and optional
  percent suffix, in the inclusive range 0..100. Decimal validation precedes float
  conversion, so huge values cannot reach the preview as infinity.
- Title is required and must meet the existing `ScheduleItemCreate` 2..500 bounds.
- Missing optional date/progress columns, blank optional cells, a title-only
  undated draft, decimal percentages and existing date formats remain supported.
- Invalid rows retain the original excerpt and receive field-specific issues and
  `importable=False`; preview numeric values stay JSON-serializable. The source
  document and its version content are never rewritten.

Budget/cash-flow parsing is unchanged. Progress is the numeric field consumed by
the current schedule importer; this change does not add dependency/duration fields.

Read-only inspection of `backend/app/api/execution_finance.py:583-650` confirmed
that the existing import endpoint reparses the source and checks every selected
row's `importable` flag before baseline locking or writes. Therefore no API change
was needed. Invalid unselected rows do not prevent importing valid selected rows.
Existing draft status, expected baseline version, membership and replay checks
remain in that API, unchanged.

## Regression and verification

First, the new regression file ran against the unchanged production parser:
**31 failed, 19 passed**. Failures included both date fields, reversed ranges,
malformed/nonfinite/out-of-range progress, title limits, and real API preview
accepting invalid selected rows. The test fixture uses an actual organization,
project membership and SQLAlchemy Session, not mocked authorization or parsing.

After the fix and additional positive boundary/selection cases:
**82 passed, 0 skipped** across:

- `backend/tests/test_v7_schedule_import_validation.py` (57 cases)
- `backend/tests/test_structured_import.py`
- `backend/tests/test_mvp4_gpr_baseline.py`
- `backend/tests/test_execution_finance_api.py`

Executed from `backend` with the shared test Python, `-X utf8 -m pytest`, `-q
--tb=short`, and unique basetemps `.../.pytest-v7-schedule-red-20260908-2` and
`.../.pytest-v7-schedule-green-20260908-2`. An initial fixture setup omitted the
required organization; it was corrected before the authoritative RED run above.

The new API tests use real SQLite Sessions and SQL statement observation to prove
preview has no writes, mixed valid/invalid selections cause HTTP 422 with zero
INSERT/UPDATE/DELETE, and valid import plus replay produces exactly two schedule
items with unchanged source text and exact normalized dates/progress.

No full suite, PostgreSQL runtime, migration, frontend/browser, external provider,
production or deployment was run. These results do not close all M4-03 or MVP4:
source-version identity, richer planner field import, browser flow and unified
same-SHA runtime evidence remain outside this bounded patch.

## Next selective graph port (not implemented here)

Source branch `codex/gpr-dds-production-merge`, commit
`dc86d4d36e081059a1f444851b09a06e51b0a5dc`, is not an ancestor of the audited base;
their merge-base is `2670f7da405c0023121938c205f3dce6601e473f`. Its existing graph
implementation is a reuse candidate, not a verified drop-in merge.

1. Selectively adapt `backend/app/api/execution_finance.py:218-359` from that
   commit: predecessor syntax/IDs, exact FS/SS/FF/SF and signed calendar-day lags,
   clone ID remapping, cycle/same-baseline validation and propagation. Keep the
   current integration's project locks, CAS, draft-only plan writes and immutable
   approved/superseded baseline rules; do not copy its older CRUD endpoints whole.
2. Plan explicit model/DTO/schema changes separately. The source migration
   `f18b7c42d9a1_add_schedule_planner_fields.py` adds sort order, parent, duration,
   milestone, predecessors and constraints, but descends from `c24d9e7a61b0`.
   Reconcile against the current single-head migration chain rather than copying
   that old migration lineage or introducing another head.
3. Wire draft edits, creation, clone remapping and batch edits only after whole
   proposed-graph validation. Test malformed/missing/foreign/self/duplicate links,
   cycles introduced by a batch, each link type/lag, constraints, milestone dates,
   and unchanged approved plans/facts. Calendar-day semantics must stay explicit;
   project calendars and resource leveling are not proved by this source helper.
4. Then adapt `frontend/src/modules/finance/GprWorkspace.tsx:36-111` for dependency
   offsets/slack/critical path and the corresponding views. Cross-check frontend
   and backend date semantics with the same fixtures, then run real PG concurrent
   edits and browser acceptance on the final integrated SHA.

No graph, models, migrations, API, frontend or source-identity changes are included
in this commit.
