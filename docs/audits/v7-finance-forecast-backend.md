# v7 finance forecast backend

## Result

The existing read-only explainable forecast was tightened into a deterministic
project / contract / WBS projection. It remains an advisory draft: the API has
only `GET /execution/forecast/{project_id}`, never confirms a record, never
creates a payment and has no bank or provider integration.

## Input and scope contract

- Project membership is checked before any scoped identifier is resolved.
- Optional `contract_id` and `schedule_item_id` are resolved inside the requested
  project; a cross-project or contract/WBS mismatch is rejected.
- A WBS leaf returns only records directly linked to that leaf.
- A WBS summary aggregates records linked to descendant leaves. A confirmed
  budget or DDS row linked directly to a summary makes the forecast fail closed.
- The existing execution overview now exposes `wbs_parent_id`, `wbs_level`,
  `wbs_order` and `is_summary` for every schedule row. A frontend can therefore
  build the same recursive hierarchy without guessing from titles or dates.
- Only the latest approved schedule baseline participates. Draft and superseded
  schedules are excluded, and summary dates are not treated as independent work.
- Only `BudgetLine` and `CashFlowEntry` rows with
  `review_status=confirmed` participate in financial calculations. Proposed,
  required-review and rejected/cancelled rows are not silently promoted to facts.
- `row_limit` is 1..500 (default 200). The repository requests one extra row and
  rejects overflow; it never returns a truncated forecast as if it were complete.

## Calculations

- Budget is reported separately per exact currency. Forecast for each line is
  `max(plan, committed, actual, declared forecast)`.
- Mixed currencies are never added or converted. Aggregate legacy totals become
  `null`, `totals_by_currency` stays exact, and owner decisions for working
  currencies, conversion policy and rate source are emitted.
- DDS storage has the existing explicit implicit currency `RUB`. Confirmed
  planned and human-confirmed paid/received actuals are kept in separate
  `planned`, `actual` and `forecast` buckets.
- `cash_gap_date` is the first date the cumulative forecast becomes negative;
  `minimum_balance_date` is separately reported for the lowest balance.
- All monetary values are decimal strings with two fraction digits. No floating
  point money, automatic conversion or bank statement is introduced.

The existing VAT and retention decision requirements remain visible. This work
does not invent owner/accounting policy and does not claim accounting-ledger
semantics.

## Safety properties

- `publication_state=draft`, `advisory_only=true`,
  `can_trigger_actions=false`, and human confirmation remains mandatory.
- The response contains exact internal evidence pins only; provider locator,
  document content and credentials are not exposed.
- Repository and engine are read-only and contain no flush, commit, queue,
  provider or external action.
- No schema or Alembic migration was needed.

## Regression evidence

Synthetic tests cover confirmed-only input, contract and leaf isolation, summary
aggregation/direct-link denial, mixed currencies, bounded rows, cross-project IDs
and project ACL. No real customer documents, bank data or provider credentials
are used.

Commands from `backend/`:

```powershell
python -m pytest tests/test_mvp4_explainable_forecast.py tests/test_v7_finance_forecast_backend.py -q
python -m pytest tests/test_mvp4_explainable_forecast.py tests/test_v7_finance_forecast_backend.py tests/test_mvp4_finance_decision_guards.py tests/test_v7_schedule_wbs.py tests/test_v7_cash_flow_views.py -q
```

Targeted result after the overview integration: 19 passed. Extended finance/WBS
regression before the additive overview fields: 63 passed. Full backend regression
before that final additive read-model field change: 2483 passed, 57 environment-
conditional skips; the affected overview/API target was then rerun: 47 passed.

## Remaining decisions and limits

- Multi-currency conversion stays blocked until the owner approves a versioned
  currency and exchange-rate policy.
- VAT and retention accounting meaning requires owner/legal/accounting approval.
- `CashFlowEntry` still has no dedicated currency column, so its documented
  storage currency remains RUB.
- Live PostgreSQL concurrency is outside this pure read-only slice; the full
  backend regression is still required by the integration flow.
