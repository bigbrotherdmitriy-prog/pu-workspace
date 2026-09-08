# Concrete next package: persist the existing calendar-day planner

Date: 2026-09-08. Reviewed base: `4bcc94ab113f544a88ed9eaeb7ebd37b6133b410`.
This is an implementation contract, not an installed migration or implemented API.

## Source inspected and selective reuse decision

Source branch `codex/gpr-dds-production-merge` resolves to
`dc86d4d36e081059a1f444851b09a06e51b0a5dc`; merge-base with the reviewed base is
`2670f7da405c0023121938c205f3dce6601e473f`.
Commit `4ceae808144fa1dd52651de043c5908914dca46a` introduced
`backend/migrations/versions/f18b7c42d9a1_add_schedule_planner_fields.py`.

| Existing source | Reuse | Required correction |
| --- | --- | --- |
| `duration_days`, `is_milestone` | Names and integer/bool representation | Nullable legacy state; **no** default one-day duration |
| `predecessor_ids VARCHAR(2000)` | Existing compact link grammar and `parse_dependencies` | Canonical serialization; every predecessor must be in the locked baseline |
| `constraint_type`, `constraint_date` | Names and supported constraint vocabulary | Reject ALAP; validate pairing and infeasibility through the new pure core |
| `sort_order`, `parent_id` | Defer both in the minimal graph package | Old parent FK allows cross-baseline parents and SET NULL loses hierarchy; WBS requires a separate complete contract |
| `_auto_schedule_baseline` | Do not port | It overwrites constraints and confuses computed dates with intent; use `app.mvp4.schedule_planner.plan_schedule` |
| Old `ScheduleProgress` | Do not port | It mixes mutable plan and actual facts; retain current separate approved-fact endpoint |
| Old clone/bulk handlers | Do not port | Preserve current immutable approved revisions, project serialization, checks and audit |

The old migration's parent is `c24d9e7a61b0`, not current `a54f001c0a19`.
Do not cherry-pick/reparent that historical file or allocate its revision again.
The next schema owner creates a **new** sequential revision after rechecking that
`a54f001c0a19` is still the sole head. No merge migration is needed on that base.

## Exact minimal additive schema

No new registry, queue, task, ledger, or dependency table is required for the
bounded calendar-day graph. Existing foreign keys and schedule item IDs remain.

| Table / added column | SQL type and initial state | Rule |
| --- | --- | --- |
| `schedule_baselines.graph_revision` | BIGINT NOT NULL DEFAULT 1 | Positive, increases on every plan composition/input/date or baseline lifecycle mutation, not on actual facts |
| `schedule_baselines.planning_mode` | VARCHAR(20) NOT NULL DEFAULT 'dates_only' | CHECK in `dates_only, calendar_graph` |
| `schedule_baselines.project_start` | DATE NULL | Explicit input; required for `calendar_graph`, never supplied by current clock |
| `schedule_items.duration_days` | INTEGER NULL, no default | Unknown until explicitly confirmed; regular 1..10000, milestone exactly 0 |
| `schedule_items.is_milestone` | BOOLEAN NULL, no default | NULL means unclassified legacy row, not automatically an activity |
| `schedule_items.predecessor_ids` | VARCHAR(2000) NULL | Canonical parsed links; NULL means explicitly no links only after calendar graph activation |
| `schedule_items.constraint_type` | VARCHAR(30) NULL | NULL legacy; explicit `asap,snet,fnet,snlt,fnlt,mso,mfo` in graph mode |
| `schedule_items.constraint_date` | DATE NULL | NULL only for ASAP; required for all other supported constraints |
| `schedule_items.not_before_date` | DATE NULL | Explicit user floor, separate from computed `planned_start` |

DB row-local CHECKs: positive graph revision; valid mode; graph mode requires
project_start; duration null or 0..10000; duration/milestone are either both null
or a valid pair; constraint/date are both null (legacy) or ASAP/null or supported
dated type. PostgreSQL CHECK three-valued logic must be covered explicitly with
IS NULL/IS NOT NULL; do not rely on `NULL OR expression` rejecting a bad pair.
Complete-baseline activation validity is checked under one transaction, not a
cross-row CHECK. Do not add fake duration/constraint backfills.

Upgrade preserves every existing planned/actual/source value and status. All
existing baselines start `dates_only`, including approved/historical baselines.
The migration itself performs **no scheduling** and no automatic approval.
New legacy `POST /schedule-items` rows also remain unclassified until activation.

Downgrade: only safe on an isolated test database or before any calendar_graph
activation. Production rollback is restore of a verified compatible backup,
not silent deletion of graph intent. A downgrade guard must reject any activated
graph unless the operator has performed an explicit export/rollback procedure.
Old application binaries must not write after graph mode is activated.

## First API implementation, not a throwaway preview

Keep current endpoints and add two baseline-scoped operations:

1. `GET /execution/baselines/{id}/graph`: viewer permission; returns baseline
   version, graph_revision, mode, anchor and **complete** ordered item set plus
   explicit graph inputs. Do not return source excerpts or document contents.
2. `PUT /execution/baselines/{id}/graph`: editor permission, draft only, strict
   body `{expected_graph_revision, project_start, items}`; body project ID, status,
   facts, source contents, computed dates and critical flags are forbidden.

Each item is `{id, duration_days, is_milestone, predecessor_ids, constraint_type,
constraint_date, not_before_date}` with strict bounded types. `items` must contain
every existing item ID exactly once, up to 500 rows in this first write API;
unknown/missing/foreign/duplicate IDs reject the entire operation. No add/delete
is hidden inside this route. Existing item-create/import routes remain available
in dates_only drafts, and reject calendar_graph drafts until graph-aware batch
insert/delete with ID remapping is implemented. This deliberate 409 is preferable
to silently invalidating a persisted dependency graph. Empty graph is allowed
only if the actual locked baseline is empty.

`PUT` activates graph mode only after all items have explicit valid inputs and
the complete `plan_schedule` succeeds. It saves graph inputs and all computed
planned dates atomically, increments graph_revision once and appends one existing
AuditLog entry with IDs/revision/count only. It never writes actual fields or
creates tasks. Return authoritative calculated dates, floats and critical edges
from that exact result with the new revision; do not rerun a browser algorithm.

The former `ScheduleBaseline.version` remains the human baseline edition. It is
not an edit CAS: the current item-create/import routes do not increment it.
Exact CAS is `UPDATE schedule_baselines SET graph_revision=r+1 ... WHERE id=id
AND graph_revision=r AND status='draft'`, rowcount exactly one, in the same DB
transaction as every item write/audit. Stale r returns 409 and makes zero writes.
Repeated PUT with old r also returns 409; **do not** claim durable request replay
or exactly-once receipts without storing such a contract. GET after 409 shows
the committed graph. A failed validation/transaction does not consume r.

Whole graph write/read order: existing GPR project advisory lock, project row,
actor User and ProjectMember rows for the final permission decision, baseline
row, then complete ScheduleItem set ordered by ID. Use fresh populated values
under no_autoflush and reject pending scope edits rather than flushing them.
Authorize before returning any graph. Recheck membership/role under row locks
immediately before the mutation; use locks that prevent non-key role updates,
not merely FOR KEY SHARE. Keep existing role semantics; do not add service-worker
approval or infer action authority from user-supplied IDs. Coordinate this order
with auth/Project writers to avoid lock inversion; test revoke versus edit.

Every existing GPR writer must share the project/baseline serialization and bump
graph_revision for plan changes: `create_schedule_item`, schedule structured
import, clone output initialization, and baseline approval/supersession. Existing
`update_schedule` actual-only updates do not bump graph_revision. Clone copies
graph intent with a complete old-to-new item-ID mapping and rewrites all links;
no actual facts are copied. A calendar_graph approved baseline can only be edited
through its new draft clone. Approval requires expected_graph_revision in graph
mode, otherwise stale review could approve a different plan.

Return bounded planner code and authorized local item IDs on validation failures,
not raw SQL, graph texts, provider identifiers, exception strings or document data.

## Implementation file package and executable acceptance contract

Next implementation may change these existing files rather than a second system:

- `backend/app/models/execution_finance.py`: additive mapped columns and checks.
- one new `backend/migrations/versions/<new>_schedule_graph_intent.py`: parent a19
  after head verification; **never** old f18 revision.
- `backend/app/api/execution_finance.py`: strict DTOs, graph routes, existing
  writers' CAS/mode guards and clone remap.
- `backend/app/mvp4/schedule_planner.py`: reuse unchanged unless a regression
  proves a core defect; no ORM imports here.
- `backend/app/schema.py` and runtime schema pins: actual new head, coordinated
  by integrator, no historical migrations edited.
- `backend/tests/test_v7_schedule_graph_api.py` and isolated PostgreSQL test:
  real routes and actual DB models, not a planner transport double.

Mandatory test cases before acceptance:

| Gate | Observable assertion |
| --- | --- |
| Upgrade legacy | a19 fixture with undated, partial-date, same-day, approved and fact-bearing items; every old value equal after upgrade; no invented duration |
| All input fields round-trip | PUT followed by fresh GET preserves explicit intent and exact output dates |
| Earlier propagation | Move predecessor earlier; successor moves earlier because old computed start is not a floor |
| No partial update | Last task has a cycle/missing link/conflicting MSO; all rows/revision/audit remain equal |
| Whole graph | Missing, extra, duplicate or foreign item ID rejects; inputs cannot reference another baseline |
| CAS | Two PostgreSQL sessions use r; one commit, one 409, one audit, revision r+1 |
| Permission | Viewer cannot PUT; cross-project access and revocation race deny before mutation |
| Approved immutability | Graph edit/import/create cannot mutate approved or superseded baseline |
| Clone | Every edge maps to new IDs; input fields preserved, facts empty, source baseline unchanged |
| Legacy writers | Create/import increments revision in dates_only mode; graph mode rejected until graph-aware insert exists |
| Approval race | Approval with old graph revision fails after an edit |
| Retry | Repeated PUT old revision is 409; no second audit/side effect |
| Migration rollback | Empty test activation-safe downgrade works; active graph downgrade refuses data loss |

Use existing 193 pure planner cases plus new real route/PG tests. SQLite-only
tests do not prove serialization. Frontend/UI wiring is a subsequent package,
not acceptance of the API just because OpenAPI contains a route.

## Decisions versus owner input

Engineering decisions can proceed without an owner meeting: nullable legacy
fields, explicit anchor and duration, separate input floor, graph CAS, atomic
whole-graph writes, reuse of existing parser/core/audit, immutable approved plans,
calendar mode clearly labelled and no ALAP approximation.

Owner/project-specific **data**, not blockers to writing the package: actual
project anchor, reviewed durations, milestone identification, links and desired
constraints. They must be entered/confirmed at activation, never inferred during
migration. Working-day calendars/holidays, WBS summary conventions and contractual
milestone interpretation require explicit policy or project data for those later
features; the minimal calendar-day package does not claim them implemented.
