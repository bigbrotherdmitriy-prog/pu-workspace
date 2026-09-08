# v7 schedule API wave 2: closed input boundary and concrete persistence package

Date: 2026-09-08. Base `4bcc94ab113f544a88ed9eaeb7ebd37b6133b410`.
Branch `codex/v7-schedule-api-wave2`, separate clean-base worktree.

## Implemented, not just proposed

`ScheduleItemCreate` silently discarded unsupported graph fields (duration,
dependencies, milestone, constraints and hierarchy) and acknowledged a legacy
item without saving the submitted planning intent. Twelve RED cases reproduced
this behaviour (`12 failed, 2 passed`). Added `ConfigDict(extra="forbid")` only
to this DTO: unsupported input is now rejected by request validation, not turned
into a misleading successful graph edit. Existing field defaults and validation
are unchanged; no aliases existed to remove.

Reviewed actual caller `frontend/src/modules/finance/useFinanceController.ts`:
create sends only baseline_id, expected_baseline_version, title, planned_finish
and planned_progress. This exact shape remains accepted. No frontend changed.
New HTTP boundary test is isolated FastAPI with inert auth/db dependency objects:
it proves 422 before endpoint database access, **not** production auth or browser
acceptance. No sensitive sample or real document is used.

## Why the planner is not falsely wired

Current models contain only planned dates/progress: no persisted duration,
milestone, edges, constraints or separate input floor. Baseline display version
does not advance on item edits. A schema-less preview or calculated-date-only
write would not preserve planner intent or provide exact edit CAS.

Inspected real historical migration `f18b7c42d9a1` and implementation at
`dc86d4d36e081059a1f444851b09a06e51b0a5dc`, not merely the earlier IR list.
Its parent is obsolete, default duration 1 fabricates legacy data, constraints
and fact-mutability differ from current guards. Prepared the concrete
[schema/API implementation package](../architecture/v7/schedule-persistence-package.md):
exact additive columns/checks, null legacy policy, new sequential migration after
a19, strict complete-graph PUT/GET, revision CAS, all writer changes, clone remap,
lock/permission requirements, API error behaviour and 13 acceptance gates.
Engineering can implement that package without waiting for owner preference;
real project anchor/durations are explicit user data at later activation.

## Verification

Commands from backend using shared `.venv-pu-workspace-tests/Scripts/python.exe`:

```text
python -X utf8 -m pytest tests/test_v7_schedule_api_boundary.py -q --tb=short --basetemp=.pytest-v7-schedule-api-red
python -X utf8 -m pytest tests/test_v7_schedule_api_boundary.py tests/test_v7_schedule_planner.py tests/test_v7_schedule_import_validation.py tests/test_mvp4_gpr_baseline.py tests/test_execution_finance_api.py tests/test_structured_import.py -q --tb=short --basetemp=.pytest-v7-schedule-api-green
```

Initial scoped GREEN before HTTP/OpenAPI tests: **289 passed, 0 skipped**.
Final scoped result including HTTP/OpenAPI checks: **291 passed, 0 skipped,
2.86 seconds**. New boundary file contains 16 cases. `git diff --check`: PASS.

No migrations/models/schema pins/worker/queues/frontend were changed. No new API
route was invented. PostgreSQL, browser E2E and full backend were not run in this
subtask; integrator must run aggregate tests. M4-02 is **not complete**: graph
persistence, API batch, clone/remap runtime and UI remain unimplemented.
Original dirty worktree and production untouched. No push/merge/deploy.
