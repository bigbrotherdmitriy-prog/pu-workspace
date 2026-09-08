# V7 DDS four-view UI — wave 6

Date: 2026-09-08. Branch `codex/v7-cashflow-ui-wave6`.
Base `e613c92d7e70f2ebc3d0816a734347b6b7bc29ab`.

## Exact dependency and wiring

Read/reviewed the sibling endpoint and contract in `v7-cashflow-views-wave6.md`. This UI requires backend commits `3b3e7e61fadbfa386fe85dd0dbea6fea192e5bb6` and `a5bf4ed3572cfa278b8d0f269d0bfda9bbd2666d` (confirmed-plan correction). The latter's mandatory `unconfirmed_plan` reason/counter and positive actual amount are included in validation. No backend code is copied here.

Finance controller annotates its scoped loaded overview with local `readonly_view_scope.project_id`. This metadata is not authorization; it only carries the already-fenced selected project into `FinanceOperations`. Existing authenticated API client performs the read. `FinanceOperations` embeds `CashFlowViews` only when scoped data is available; no App.tsx changes needed. Existing confirmation/correction handlers are unchanged.

The user explicitly chooses dates and loads one GET `/execution/cash-flow/views` with project, optional contract and inclusive period. Four tabs (details/months/calendar/summary) project the same response and switching tabs performs no requests. Scope change remounts the session. Date edits clear prior figures and invalidate pending replies, including ABA changes. Abort plus monotonically increasing request tickets fence late results. Missing scope, invalid response, scope mismatch or errors show no old totals.

## Financial boundaries

All displayed money stays exact two-decimal strings. Integer kopeck BigInt comparisons reconcile details against per-day ID sets and separate plan/fact buckets, then months and summary. No binary floating-point conversion of money, ledger, cash action, opening balance or forecast is created. Plan and fact are never added together. Proposed/cancelled/unsupported and unconfirmed rows remain visible with explicit exclusions. Unknown/duplicate IDs, missing days/months, wrong scope, inconsistent inclusion/review flags, unsafe money, mismatched exclusions and inconsistent totals fail closed.

The opening bank balance is explicitly **unknown**, not assumed zero. Summary net is labeled period inflow minus outflow, not a bank balance. Existing general forecast/overview figures remain separate legacy displays; this work does not claim to repair or reconcile those independent bases.

## Verification

Before implementation, parser tests failed collection because the requested new module did not exist. This is a new-feature RED, not evidence of a reproduced existing backend bug.

From `frontend`:

```text
node node_modules/vitest/vitest.mjs run src/modules/finance
node node_modules/typescript/bin/tsc --noEmit
git diff --check
```

Results: **30 tests passed**, TypeScript PASS, diff-check PASS. Includes 18 new parser/component cases plus 12 existing controller/operations tests. Tests cover precision above Number safe integer, decimal validation, all-view consistency, explicit-only GET, readonly switching, exclusion of legacy unconfirmed plan, missing/duplicate IDs/buckets, project/contract/period late response, ABA, invalid periods and leap-year 366-day bound. All synthetic fixtures; no real payment, provider or customer content.

No browser E2E, build, PostgreSQL or full backend run in this bounded package. Root owns aggregate integration and runtime verification. UI snapshot validity does not prove live permissions or application-wide financial correctness.

Only `frontend/src/modules/finance/**` and this report changed. Separate D worktree; original dirty worktree and production unchanged. No push, merge or deploy.
