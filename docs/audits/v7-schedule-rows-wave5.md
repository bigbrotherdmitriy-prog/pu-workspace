# v7 schedule graph row lifecycle — Wave 5

Date: 2026-09-08. Branch: `codex/v7-schedule-rows-wave5`.
Base: `fe1bf865aa349d818921f897ffae827e66541341`.

## Scope and initial audit

The existing persisted calendar graph PUT accepts exactly the existing row IDs;
the legacy row-create route intentionally rejects calendar graphs. Consequently
even a cloned draft could not safely add, rename or remove work through the graph
contract. The new route reuses the existing planner, project/member/baseline locks,
graph revision, calculated-date response and audit writer. No second scheduler,
WBS, model, migration, frontend or workflow changes were introduced.

Initial worktree was clean and created separately on D. No AGENTS.md was found in
the worktree or its D parent directories. Main worktree and production untouched.

## API contract for the UI integrator

`PUT /execution/baselines/{baseline_id}/graph/rows`

```json
{
  "expected_graph_revision": 2,
  "project_start": "2026-09-01",
  "deleted_ids": [],
  "items": [
    {
      "id": 101,
      "title": "Synthetic existing work",
      "duration_days": 2,
      "is_milestone": false,
      "constraint_type": "asap",
      "constraint_date": null,
      "not_before_date": null,
      "dependencies": []
    },
    {
      "client_ref": "new_work",
      "title": "Synthetic new work",
      "duration_days": 1,
      "is_milestone": false,
      "constraint_type": "asap",
      "dependencies": [{"predecessor_id": 101, "link_type": "FS", "lag_days": 0}]
    },
    {
      "client_ref": "new_milestone",
      "title": "Synthetic milestone",
      "duration_days": 0,
      "is_milestone": true,
      "constraint_type": "asap",
      "dependencies": [{"predecessor_ref": "new_work", "link_type": "FS", "lag_days": 0}]
    }
  ]
}
```

- Exactly one non-null `id` / `client_ref` per row. IDs are strict positive ints.
- Client refs match `[A-Za-z][A-Za-z0-9_-]{0,63}`, unique within this request only;
  they are not durable idempotency keys and are not stored or audited.
- Dependencies use exactly one `predecessor_id` / `predecessor_ref`; refs may
  point forward to another new row. Link types FS/SS/FF/SF, default FS;
  strict integer lag defaults 0, range -10000..10000 calendar days.
- `items` is the complete resulting graph, maximum 500 rows. Existing kept IDs
  plus explicit `deleted_ids` must partition the current graph, without overlap
  or duplicates. Omission does **not** implicitly delete a row.
- Title 2..500 characters, at least two non-whitespace characters. Duration and
  milestone/constraint consistency use the existing planner. Calculated dates,
  actual facts and source fields cannot be supplied (unknown DTO fields denied).
- Dependencies default to empty, deleted IDs default to empty, optional dates
  default to null. Canonical dependency text must still fit the persisted limit.
- Output: the existing GET graph response plus `client_ref_map: {ref: real_id}`.
  GET remains unchanged; persisted IDs are stable and dates are authoritative.
- Both legacy dates-only drafts and calendar-graph drafts can use this explicit
  complete intent route. Success makes `planning_mode=calendar_graph`.

Conflict/error contract:

- 403: insufficient project role; editor required, current membership refreshed.
- 409 `schedule_draft_required`: approved/superseded/non-draft immutable.
- 409 `schedule_graph_revision_changed`: stale/repeated request. No new rows or
  revision advance. UI must preserve local changes and explicitly reload/rebase.
- 409 `schedule_row_delete_protected`: any financial link (including cancelled
  records), source metadata, actual dates/progress or non-planned row status.
- 409 `schedule_pending_changes`: supplied session already has pending changes;
  these are neither flushed nor discarded by the route.
- 422 `schedule_complete_graph_required`: incomplete/foreign/duplicate identities.
- 422 `schedule_unknown_client_reference` / `schedule_unknown_predecessor`:
  dangling or deleted predecessor. Planner errors retain the existing code plus
  task IDs shape; provisional new-row IDs in preflight errors are calculation
  identifiers only, not persisted source identities. Do not use them as row IDs.
- 422 `schedule_dependency_limit`: canonical text would exceed 2000 characters.

Approval remains the existing separate manager operation with exact loaded graph
revision. Saving row composition never approves a baseline.

## Atomicity and deletion security

Project advisory/project lock → current actor/member locks → baseline → schedule
rows FOR UPDATE. Full request validation and prospective planner evaluation occur
before row allocation. New IDs are allocated only inside the mutation transaction;
the existing planner is then rerun on the actual IDs. One baseline CAS, graph edits,
deletions, authoritative dates and count-only audit commit together. Any late
failure rolls the transaction back. No document titles, client refs or graph
contents are written into audit details.

BudgetLine and CashFlowEntry are the only current model FKs to ScheduleItem. Any
link blocks deletion regardless of financial record status or scope; we never
silently accept their FK SET NULL behavior. Source name/excerpt also block delete,
including empty-but-present metadata. Kept rows retain actuals and provenance.
PostgreSQL FOR UPDATE conflicts with concurrent FK key-share insertion, preventing
a linked row from being deleted between the link check and DELETE. This property
is covered by a prepared opt-in test but was not executed here.

## Checks and evidence

Regression-first command (before implementation):

```powershell
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest tests/test_v7_schedule_graph_rows.py -q --basetemp=D:/PU-Workspace/tmp/schedule-rows-red
```

Result: **29 failed**, all missing new DTO/route; existing graph API separately
documents/reproduces the legacy insertion prohibition. This is a missing lifecycle
feature, not a claim that the old fail-closed guard itself was defective.

Final bounded regression command, from `backend`, TEMP/TMP on D:

```powershell
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest tests/test_v7_schedule_graph_rows.py tests/test_v7_schedule_graph_rows_postgres.py tests/test_v7_schedule_graph_api.py tests/test_v7_schedule_planner.py tests/test_v7_schedule_api_boundary.py tests/test_mvp4_gpr_baseline.py tests/test_execution_finance_api.py -q --basetemp=D:/PU-Workspace/tmp/schedule-rows-related
```

Result: **287 passed, 2 skipped in 18.95s**. Included real FastAPI JSON route
roundtrip and repeated request; only authentication transport dependency is
synthetically overridden. All project-role checks still execute.

Scenarios include stable/new IDs and forward references, title change, recalculated
dates, cycles/dangling refs/duplicates/omissions, delete and rewiring, financial and
source deletion denial, actual/provenance preservation, empty graph and milestone,
approved-original clone immutability, viewer/outsider refusal, strict DTOs, stale
revision and repeated HTTP request, and injected late-audit rollback.

PostgreSQL: **NOT RUN**. `PUW_MVP4_TEST_DATABASE_URL` was absent. Two new tests use
the existing guarded `pg_graph` fixture, restricted test DB name/host, isolated
migrated schema and bounded thread waits:

1. Two complete row batches at one revision: one commit, one 409, one new row,
   one lifecycle audit.
2. Uncommitted financial FK insert before delete: insertion commits, deletion
   refuses and retains linkage/revision.

External runner command after providing an owned test DB only:

```text
python -m pytest tests/test_v7_schedule_graph_rows_postgres.py -q
```

The existing PG fixture migrates its isolated schema through a20, sufficient for
this unchanged schema contract; this is not a claim of validating current full
release migrations. No DB URL or secrets are recorded here.

## Result / remaining integration

**CONDITIONAL**: bounded local contract/regression PASS; real PostgreSQL concurrency
not verified. Root owns full backend suite and CI orchestration. Frontend row UI,
browser E2E, WBS hierarchy/ordering, working calendars and release acceptance remain
outside this bounded package. No full backend/frontend build or production action
was performed, and no push/merge/deploy occurred.

Changed files: `backend/app/api/execution_finance.py`, the two new row test modules,
and this report. No migrations; current head unchanged.
