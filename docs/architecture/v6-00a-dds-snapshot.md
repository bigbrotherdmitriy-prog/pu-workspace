# V6-00a, stage 2: DDS snapshot API

Stage 1 parent: PR #83, main `0b6570169210b9b8af0e856ba52add22d142171d`.
This change does not switch the frontend, alter money storage or deploy production.

## Request and authorization

`GET /execution/cash-flow/views?project_id=17&date_from=2026-01-01&date_to=2026-12-31`

Required project viewer access (existing project RBAC). Optional `contract_id` must
belong to the project. Optional comma-separated `statuses` defaults to
`approved,paid,received`. Optional `include_review_rows=false` controls detail rows
for excluded statuses; it never enables their financial contribution.

Dates are inclusive, date-only, maximum 366 days. Both planned and actual dates use
the same interval independently. Every day/month in the interval is emitted,
including zeros. A row outside both date ranges is outside the snapshot scope.

## Status rules

| Status | Plan | Fact | Exclusion / integrity |
| --- | --- | --- | --- |
| proposed | never | never | awaiting_confirmation |
| approved | planned date | never | status_filtered if not selected |
| paid, outflow | planned date | actual date | requires positive fact and actual date |
| received, inflow | planned date | actual date | requires positive fact and actual date |
| cancelled | never | never | cancelled |
| unknown / inconsistent direction | none | none | entire response rejected |

Integrity validation occurs before status filtering: a filter cannot conceal an
unknown status, currency mismatch or corrupt money in the selected date/contract
scope. Unknown filter values and invalid periods return 422. Integrity failures
return 409 with `detail.code` and a human-readable message. More than 10,000 source
rows returns 413, without partial totals. Review/excluded rows count toward the cap.

## Consistency and money

One SQL statement outer-joins project currency/revision with the date/contract
scoped cash-flow rows, bounded by limit+1. PostgreSQL statement MVCC guarantees
that revision and rows come from the same committed state even under READ COMMITTED.
The query bypasses ORM identity-map caching. RBAC/contract checks are separate;
there are no separate source queries for each view. No writes are performed by GET.

The materialized source produces `months`, `calendar`, `details`, `summary`
(totals plus category/object breakdowns), and `excluded`. All views carry the same
plan/fact, inflow/outflow/net totals shape. Money objects are
`{"amount_minor":"150050","amount":"1500.50"}`. Arithmetic uses integer minor
units only; input NUMERIC values are parsed through the stage-1 Decimal boundary.
No rounding of aggregates or currency conversion is performed. Negative net and
aggregates larger than one NUMERIC(18,2) cell are supported exactly.

`snapshot_hash` is SHA-256 over canonical UTF-8 JSON of contract version, full scope,
revision, sorted source rows (including zero/excluded contributions and all source
metadata), and exclusion reasons. Stable sorting is planned date / actual date / ID.
It excludes request timestamps, so unchanged requests yield identical responses.

## Revision migration

Migration `c70a00a2f001`, parent `c70a00a1f001`, adds two project columns:
`cash_flow_revision` (bigint, default 0) and internal `cash_flow_revision_txid`.
Existing rows start at revision 0; monetary data is not rewritten.

PostgreSQL triggers cover INSERT/UPDATE/DELETE on cash_flow_entries, including ORM,
bulk SQL, Excel import, invoice materialization, plan edits/copy/undo, payment
confirmation/correction/reversal and cancellation. Revision advances atomically
once per project per transaction, with rollback semantics and no lost increments.
Moving a row invalidates both projects. Identical raw SQL updates do not increment.
Project currency changes also invalidate the snapshot; no new currency-changing API
or permission is introduced. Linked labels are read from the cash-flow row itself,
not independently joined mutable dictionaries.

Downgrade removes only the triggers/functions and two revision columns. It preserves
all cash-flow/project money and currency data. Re-upgrade starts a new revision
sequence; caches must be invalidated after downgrade/re-upgrade.

The revision trigger is PostgreSQL-specific. SQLite offline tests cover projection
algebra and HTTP/RBAC; they are not proof of durable revision behavior. CI explicitly
runs six real PostgreSQL gates with a no-skip assertion, using disposable schemas.

## Rollout boundaries

Current `/execution/overview` and monetary POST/PATCH contracts are unchanged.
Existing frontend totals remain legacy until stage 3. Excel/bulk invoice writes
now invalidate revision automatically, but their UI, parsing and batch-confirm
redesigns are outside this stage. Proposed rows remain visible for review and never
enter snapshot plan/fact. No production migration/deploy without separate approval.
