# v7 C1: pure schedule graph core, not integrated GPR acceptance

Date: 2026-09-08. Base: `5226782908dff519a2bf2c6b9d320b59f2972c10`.
Branch/worktree: `codex/v7-schedule-graph-core` / `pu-workspace-v7-schedule-graph-core`.

## Existing implementation and reuse boundary

Read-only searches found no graph planner in the current backend. The existing
organizer planner and job scheduler solve unrelated problems. The reference is
`codex/gpr-dds-production-merge` at `dc86d4d36e081059a1f444851b09a06e51b0a5dc`:

- `backend/app/api/execution_finance.py:218-359`: dependency grammar, DAG validation,
  calendar propagation and constraints.
- `frontend/src/modules/finance/GprWorkspace.tsx:36-111`: dependency offsets and
  earliest/latest/slack analysis.

This patch adapts those graph concepts into one pure, stdlib-only module:
`backend/app/mvp4/schedule_planner.py`. No second business registry, queue, provider,
database, current clock, persistence or baseline mutation is introduced.

The current integration's immutable baseline rules were also inspected: creation
is draft-only with expected baseline version and project serialization; cloning
requires the current approved baseline and does not copy facts; progress updates
are actual-only for the current approved baseline. The old branch's broader CRUD
endpoints must not replace these guards during a later integration.

## Reproduced reference defects and deliberate corrections

Before implementing the new core, a read-only synthetic check compiled the actual
reference `_auto_schedule_baseline` and its three parser/date helpers from `git
show` using AST, with an in-memory query transport double. The assertion failed:

- predecessor: start 2026-01-01, duration 3, calculated finish 2026-01-03;
- successor: `1FS`, MSO 2026-01-02;
- reference silently forced successor start to 2026-01-02, violating FS.

This was a functional RED on the existing reference, not PostgreSQL proof. The
new core rejects inconsistent upper/equality constraints instead of overwriting
dependency results. Regression tests cover MSO/MFO/SNLT/FNLT conflicts.

The reference API accepts ALAP/SNLT/FNLT, but its forward scheduler only handles
MSO/SNET/MFO/FNET. The new core implements explicit upper bounds for SNLT/FNLT and
explicitly rejects ALAP. It does not claim complete constraint coverage.

The reference frontend treats milestone duration as zero in edge offsets, while
the backend uses milestone start == finish and FS = finish + 1 + lag. This patch
uses one consistent named-date inequality for scheduling and float calculations,
including both milestone positions and all four link types. It also incorporates
explicit date floors/constraints in float analysis, unlike the reference frontend.

## Pure input/output contract

`parse_dependencies(text)` accepts the existing `12FS+2d; 7SS-1д` syntax, with bare
IDs meaning FS. It returns a canonical tuple of frozen `Dependency` values. Bad,
empty-between-separators, duplicate and over-2000-character inputs fail explicitly.
No malformed token is silently discarded.

`plan_schedule(tasks, project_start=explicit_date)` accepts frozen `ScheduleTask`s:

- positive unique `task_id` and a complete graph of local dependency IDs;
- explicit `duration_days`: regular activities 1..10000, milestones exactly zero;
- tuple of dependencies, `planned_start` optional **not-before floor**;
- `constraint_type`: ASAP/SNET/FNET/MSO/MFO/SNLT/FNLT, with the required date;
- plain dates only; no datetime, automatic string parsing or implicit current date.

It returns a frozen `SchedulePlan` containing topologically ordered `TaskPlan`s,
earliest/latest start/finish, integer total/free float, deterministic topology,
project finish, zero-total-float IDs and tight critical edges. An empty graph has
no finish. Errors are content-free `PlannerError.code` plus numeric task IDs; there
is no partial result for cycles, missing/self/duplicate dependencies, invalid
fields, inconsistent constraints or unrepresentable calendar dates.

Dates are inclusive, calendar-day-only. Let `span = duration - 1` for an activity
and zero for a point milestone. For predecessor P and successor S:

| Relation | Minimum successor start relative to predecessor start |
| --- | --- |
| FS | `span(P) + 1 + lag` |
| SS | `lag` |
| FF | `span(P) - span(S) + lag` |
| SF | `-span(S) + lag` |

Every start is at or after the explicit project anchor, even with negative lags.
A milestone has identical start/finish dates; FS after a milestone therefore uses
the next calendar day at zero lag. This matches the reference backend's date-only
rule, not a newly invented time-of-day or working-calendar interpretation.

The forward pass finds feasible earliest dates. The backward pass respects the
minimal project finish and each upper/equality constraint. Total float is latest
minus earliest start; free float is the independent delay allowed by fixed early
successor dates, the same finish horizon and the task's own upper bound. Negative
float is not clamped to a fake success. Zero constrained float can include a fixed
isolated task, so `critical_ids` is a critical network, not necessarily one path.
Only tight dependency edges between critical tasks appear in `critical_edges`.

## Tests and evidence

New tests were written before the new module: the initial run failed collection
because `app.mvp4.schedule_planner` did not exist. This missing-module RED is
distinct from the reference MSO functional RED above.

Final scoped verification: **218 passed, 0 skipped**, comprising:

- `backend/tests/test_v7_schedule_planner.py`: **193 cases**;
- existing `test_mvp4_gpr_baseline.py`, `test_execution_finance_api.py` and
  `test_structured_import.py`: 25 cases.

The new cases include chain, unequal/equal diamond, disconnected nodes, 48
type/lag/milestone combinations, constraints, malformed input, deterministic
immutable outputs, overflow rejection and a 1200-node non-recursive chain.

Crucially, independent exhaustive oracles enumerate actual possible start dates,
apply named start/finish inequalities directly, and compare earliest/latest,
total and free float: 16 three-task link-type combinations plus 72 constrained
link combinations across all four types, positive/negative/zero lag and all six
supported date constraints. These do not merely compare two copies of the same
forward/backward algorithm.

Ran with shared test Python `-X utf8 -m pytest`, `-q --tb=short`, from `backend`,
using unique basetemps `.pytest-v7-graph-red-20260908-1` and
`.pytest-v7-graph-green-20260908-2`. No full suite, PostgreSQL, migrations, frontend,
browser, providers, production, push or deployment was run.

## Concrete API/schema integration requests (not implemented)

1. The future API must resolve actual actor/project/baseline authority, lock the
   existing project/baseline in the established order, read a fresh complete graph
   and validate the proposed batch before any plan writes. A pure graph input is
   not evidence of tenancy or permission. Preserve draft-only mutation, approved
   immutability, separate actual updates, audit and retry idempotency.
2. Define an explicit project start source and a true draft-edit concurrency token
   or locked graph fingerprint. A baseline's display version alone must not be
   assumed to advance after every item edit. Map typed planner errors to bounded
   API validation/conflict responses; persist no subset on any rejection.
3. Add/reconcile explicit duration, milestone, dependency and constraint fields in
   a separately authorized schema package. The source migration
   `f18b7c42d9a1_add_schedule_planner_fields.py` has the old parent `c24d9e7a61b0`;
   do not copy its lineage into the current migration head. Undated legacy rows
   do not justify silently inventing a duration. Current partial dates need an
   explicit conversion/review policy.
4. Distinguish user not-before dates from previously calculated dates. Passing all
   old calculated `planned_start` values back as floors would prevent earlier
   propagation after an upstream advance. The caller must map explicit planning
   intent, not accidentally make every computed date a permanent constraint.
5. Clone needs complete ID remapping for dependencies before planning the new
   draft; no old-baseline IDs or fact values may leak into it. WBS hierarchy,
   parent cycles/rollups and their schema are a separate integration request,
   absent from this core's input and validation.
6. Align browser critical-path/date views with the authoritative core result,
   including milestone and constraint semantics; do not retain the conflicting
   frontend offset algorithm. Add real API/batch/PG/browser acceptance afterward.
7. Working-day calendars, holidays, resource leveling, ALAP, WBS and full planner
   constraint coverage remain explicit gaps. No calendar or owner decision is
   silently approximated here.

This pure core is **not M4-02 acceptance completion** and does not wire a user
workflow. API, models, migrations and frontend remain unchanged in this package.
