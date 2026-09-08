# v7 W0 — scope-aware pending authority guard

Date: 2026-09-08. Base: `5226782908dff519a2bf2c6b9d320b59f2972c10`.
Branch: `codex/v7-authority-pending-scope`; separate clean-base worktree.

## Reproduced defect

The shared AuthorityResolver rejected every pending Project, User, ProjectMember
or AuthorityState, including provably unrelated records. This broke the existing
local-source regression where derived child records and an unrelated project
are pending in a read-only publishing transaction.

Before the fix:

- Existing `test_pending_child_rows_and_unrelated_project_are_not_original_authority`: **1 failed**.
- New scope tests: **8 failed, 9 passed**. Failures were unrelated new project,
  user, membership and mandate, each with lock enabled/disabled.

The earlier uncommitted implementation in the authority worktree was reviewed
read-only and left untouched. This patch reuses its scope-inspection approach,
with an important distinction: a new row's NO_VALUE history means never persisted,
whereas an existing expired row's NO_VALUE means unknown previous scope.

## Fix and invariants

Only pending rows that may affect the exact project/principal/tenant/mandate scope
block resolution. Current values, committed previous values and identity-map PK
are inspected without lazy loading. Moving an existing row out of the protected
scope cannot evade denial; unknown previous scope fails closed. New records whose
explicit scope is disjoint do not cause unrelated authorization failure.

No query/flush/rollback/refresh is performed by the pending guard. Relevant
revocations must still be explicitly flushed or rolled back by the caller before
authorization. The subsequent live membership refresh, epoch/role/expiry checks,
human-only operation restrictions and explicit service mandate remain unchanged.
No global-admin privilege, new grant or second authority registry was introduced.

Independent review found a second boundary: `authorize_subject(task.assign)` had
a membership SELECT outside no_autoflush. With the narrower principal guard,
pending assignee membership could be flushed implicitly. Five additional
regressions reproduced this before the correction. The assignee's current/old
membership/User scope is now guarded too, and the final SELECT is no-autoflush.
Unrelated records remain pending; no unflushed grant or revocation is applied.

## Verification

From `backend/`, using the existing workspace test Python:

```powershell
python -X utf8 -m pytest tests/test_v7_authority_pending_scope.py tests/test_v54_authority_live_membership.py tests/test_mvp1_local_source_authority.py tests/test_v54_authority.py -q --tb=short
```

Initial profile: **82 passed** in 91.27 seconds. Final profile including the
independent-review corrections: **87 passed** in 90.46 seconds, no skips. Includes the original failing
derived-child regression, old-scope moves, expired unknown scope, live membership
refresh, pending revocation and authority integration contracts. An intermediate
run was interrupted while refining new-row history handling; it is not a PASS.

These are real SQLAlchemy Session/SQLite tests, not PostgreSQL concurrency proof.
TEST_POSTGRES_DSN/TEST_DATABASE_URL are not configured. Docker was not found in
PATH or its standard installation location. No provider, production database or
real document was accessed. No migration/frontend/queue changes.

## Integration

Apply the scoped commit on top of base or compatible descendant. Repeat together
with D14 XLSX Evidence and meeting binding tests. The existing integration dirty
UI/CI changes and original authority-worktree WIP are not part of this commit and
were not overwritten. This closes the specific W0 local regression, not the
whole tenant matrix or v7 acceptance. Push/merge/deploy were not performed.
