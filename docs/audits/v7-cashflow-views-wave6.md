# v7 M4-05 — Read-only DDS views, Wave 6

Date: 2026-09-08. Branch `codex/v7-cashflow-views-wave6`.
Base `0ce33739722f0ead2dba0b6ea1823b1f79839c34`.
Separate clean D worktree; main worktree and production untouched.

## Initial audit and bounded scope

CashFlowEntry is already the authoritative ledger, with separate fact history,
manual confirmation/correction and status/version guards. Existing UI has a
detailed list, project overview totals and a separate explanatory forecast, but
not four consistently scoped DDS views. This package adds no ledger, financial
rules, mutations, migrations, frontend changes, currency conversion or posting.

`execution_finance.py` only imports the pure projection and adds a read-only GET
route and its row limit. Existing overview, forecast and financial mutation
handlers are unchanged. No AGENTS.md was found in this worktree's source tree or
its D parent directories (same checked base as the preceding wave).

## Exact GET contract

```text
GET /execution/cash-flow/views?project_id=1&contract_id=2&date_from=2026-01-01&date_to=2026-02-28
```

Project required; optional contract must belong to project. Dates are mandatory,
inclusive, ordered and limited to 366 calendar days. IDs must be positive. Current
actor is refreshed before the existing viewer-role check; missing/archived project
is denied. The existing current-admin access policy is reused, not expanded.

The route selects CashFlowEntry once, where project/optional contract match and
**either** planned_date **or** actual_date is in the period. All four projections
use the same materialized row snapshot. Limit 5000 rows including excluded rows;
5001st row produces 413, never partial/truncated financial totals.

Response shape:

```text
scope: {project_id, contract_id: number|null, date_from, date_to}
currency: "RUB"
details: [
  {id, project_id, contract_id, record_version,
   schedule_item_id, budget_line_id, task_id,
   source_document_id, source_document_version_id,
   evidence_id, evidence_revision, evidence_assessment_version,
   confidence, review_status, direction, title, counterparty,
   planned_date, actual_date, planned_amount, actual_amount, status,
   plan_in_period: boolean, actual_in_period: boolean,
   exclusion_reasons: string[]}
]
months: [{month: "YYYY-MM", planned: {inflow,outflow,net}, actual: {inflow,outflow,net}}]
calendar: [{date: "YYYY-MM-DD", planned: {inflow,outflow,net}, actual: {inflow,outflow,net},
            planned_entry_ids: number[], actual_entry_ids: number[]}]
summary: {planned: {inflow,outflow,net}, actual: {inflow,outflow,net}}
excluded: {proposed,cancelled,unsupported_status,unconfirmed_plan,invalid_actual,invalid_plan,invalid_direction}
decision_requirements: [{code,decision_by,message}]
external_effects: {payment_created:false,posting_created:false,automatic_conversion:false}
```

Amounts are fixed two-place decimal **strings**, including `"0.00"`. A malformed
raw amount is null in details, flagged and excluded, never rendered as NaN or
converted from binary float. DB amounts remain unchanged. Every day and month in
the bounded period appears, including zero buckets. Entries are deterministic in
planned_date/id query order; calendar buckets themselves are chronological.

`plan_in_period` and `actual_in_period` mean **included in that basis's totals**,
not merely that the raw date matches. Details can show excluded rows with reasons.
The exclusion counts refer to period-relevant selected rows, not the entire project.

## Basis and existing financial boundaries

- Confirmed plan: approved/paid/received **and review_status=confirmed**, valid
  planned amount/date, planned_date. Legacy active status alone is not review.
- Active rows whose review is pending_confirmation/required/rejected stay visible
  with `unconfirmed_plan`, but contribute no confirmed plan money. No backfill.
- Fact: paid/received consistent with direction, strictly positive actual amount/date, and confirmed
  review status. This preserves the existing payment handler's human-review guard.
- A malformed/unreviewed/zero-default fact is flagged `invalid_actual` and excluded
  from fact. A valid original plan remains countable only with confirmed review;
  required/pending/rejected review excludes both bases. Plan zero remains supported.
- Proposed and cancelled rows are visible as excluded details and contribute no
  money. Unsupported states are likewise excluded explicitly.
- Corrections change the fact month/date/amount, never replace the planned basis.
- Plan and fact columns are separate comparisons, not a combined cash movement.
- Calendar and monthly sums derive from precisely the same events as the summary;
  there is no independently calculated frontend or secondary-table total.
- No opening bank balance, VAT/net conversion, retention formula or currency rate
  is invented. RUB is the existing implicit CashFlowEntry storage convention.
  Existing financial decision messages are reused for this selected DDS dataset;
  this route does **not** claim to inspect currencies in unrelated BudgetLine rows.

Errors: 422 invalid dates/range/query IDs or foreign contract; 403 access/missing or
archived project; 413 `cash_flow_view_row_limit`; 409
`cash_flow_view_pending_changes` if a caller supplies pending ORM mutations. The
route neither flushes nor discards those mutations. No commit, audit, document
content read, external request or financial action occurs.

## Regression-first and results

Before implementation:

```powershell
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest tests/test_v7_cash_flow_views.py -q --basetemp=D:/PU-Workspace/tmp/dds-views-red --tb=short
```

**13 failed**: requested read-only route absent. Missing feature specification,
not a claim that existing payment guard behavior was wrong.

Final targeted + related run from backend, TEMP/TMP on D:

```powershell
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest tests/test_v7_cash_flow_views.py tests/test_mvp4_budget_dds.py tests/test_execution_finance_api.py -q --basetemp=D:/PU-Workspace/tmp/dds-views-final --tb=short
```

**47 passed, no skips, 2 existing Alembic configuration warnings, 7.65s.**
`git diff --check`: PASS.

Covered:

- All-view sum equality separately by plan/fact and inflow/outflow/net.
- Planned January / actual February, inclusive period boundaries, empty leap day,
  bounded ranges, date.min/date.max bucket serialization.
- Actual payment handlers: low-confidence proposal cannot become payment;
  human approval → confirm → repeated confirm → correction into another month →
  cancel. Both directions tested with real role checks, no permission monkeypatch.
- Corrected outflow reconciles linked budget actual; cancellation removes it;
  history retains exactly the confirmation and correction without a duplicate.
- Proposed/cancelled/unsupported states and malformed actual values explicitly
  excluded; source financial fields remain unchanged.
- Decimal cents and large sums, row-limit refusal, duplicate/scope validation.
- Foreign project/contract and stale admin membership refusal, archived project,
  pending-change refusal.
- Exactly one ledger SELECT and test guards forbidding session flush/commit.
- Real FastAPI JSON GET route (synthetic authentication transport dependency only),
  monetary strings and invalid-query rejection.

Only synthetic SQLite and pure projection/HTTP tests ran. No live PostgreSQL,
browser, frontend suite or full backend suite was run by this bounded stream.
Those remain root integration responsibilities, not silently counted as PASS.

## Remaining scope / status

### Blocking-review correction (separate commit above initial package)

Review found that legacy active statuses with a14's pending review default could
enter planned totals, and a paid zero-default amount could be presented as a fact.
Both were reproduced before correction. Expanded target RED:

```powershell
python -X utf8 -m pytest tests/test_v7_cash_flow_views.py -q --basetemp=D:/PU-Workspace/tmp/dds-unconfirmed-zero-red --tb=short
```

**11 failed, 18 passed in 8.09s**: all nine combinations of approved/paid/received
with pending_confirmation/required/rejected review, plus required-review basis
expectation and zero actual. Fixed only read-only projection/tests/report; no
financial handlers, migrations or existing records changed. Valid reviewed plan
with malformed fact is still preserved. Unknown/unconfirmed review fails closed.

Same final targeted+related suite with
`--basetemp=D:/PU-Workspace/tmp/dds-confirmed-final`: **57 passed, no skips,
2 existing Alembic warnings, 5.66s**. UI integrator notified about the mandatory
additional `unconfirmed_plan` reason/counter. This corrected contract supersedes
the initial package's broader active-status-only plan admission.

**Backend projection contract PASS; M4-05 full product acceptance NOT COMPLETE.**
UI must consume this response without recomputing money, display excluded states,
and reject stale project/contract/period replies. The four-view UI/E2E is separate.

Observed pre-existing issues are not changed here: overview cash-gap ordering uses
planned dates even when facts move to actual dates; explanatory forecast includes
unconfirmed proposals while overview excludes them; FinanceModule global metrics
do not become contract-scoped merely because its register is filtered. Treat the
new confirmed DDS projection and explanatory forecast as distinct labeled bases.
This package does not declare overview/forecast/all-release reconciliation done.

Changed files: new `backend/app/mvp4/cash_flow_views.py`, read-only additions in
`backend/app/api/execution_finance.py`, new `backend/tests/test_v7_cash_flow_views.py`,
and this report. No model/migration, payment rule, product UI, production, push,
merge or deploy changes.
