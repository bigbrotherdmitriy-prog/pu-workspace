# M4-10: synthetic/offline financial acceptance

Date: 2026-09-05  
Base: `9b9404a79ad336f68a4dc91b92f556cf7d512671`  
Branch: `codex/mvp4-financial-acceptance`  
Scope: tests, this audit, and three minimal fixes proven by failing regression tests.

## Decision

**Synthetic/offline acceptance: PASS. PostgreSQL runtime: CONDITIONAL.**

The accepted chain is:

`exact contract/document evidence -> approved immutable GPR baseline/stage -> budget -> invoice proposal -> manager approval -> explicit payment confirmation -> separate correction -> supply request/order/delivery/act -> advisory explainable forecast`.

No bank, payment, signature, mail, provider or other external effect is executed by this chain. The supply workflow remains internal and `external_action_status` remains `not_created`; the forecast remains a draft with `can_trigger_actions=false`.

## Defects reproduced before fixes

1. **Budget evidence disappeared in the forecast.** A `BudgetLine` had an exact `DocumentVersion`/`Evidence` pin, but `load_forecast_input()` did not include budget source documents in its evidence lookup and did not pass the document ID to the source DTO. The new regression failed with an empty evidence tuple. The repository now preserves exact page/coordinates for evidenced budget inputs.
2. **Sub-kopeck amounts were accepted by financial commands.** Values such as `1.001` passed request validation and could rely on database-specific rounding. All financial request DTO money fields now enforce `Numeric(18,2)`-compatible `max_digits=18, decimal_places=2` validation.
3. **A stale status CAS mutated an ORM entity before rejecting the request.** `update_status()` set `review_status=confirmed` and incremented `record_version` before checking `expected_status`. The check now occurs before any mutation while exact replays retain their former idempotent behavior.

## Acceptance checklist

| Requirement | Status | Evidence |
|---|---|---|
| Exact contract/source version evidence | PASS | deterministic extraction and immutable v5.4 Evidence pins |
| Immutable approved GPR baseline | PASS | composition mutation rejected with `409` |
| Budget and cash-flow exact control links | PASS | contract, stage/task, budget, document version, Evidence revision and assessment version |
| Invoice is not payment | PASS | proposal and approval leave actual amount/date empty; only explicit manager confirmation creates fact |
| Payment correction is a separate event | PASS | `confirmed` then `corrected` history entries with old/new values |
| RBAC | PASS | viewer cannot create budget; editor cannot approve supply |
| Tenant/project isolation | PASS | role and exact-scope checks deny cross-project access |
| CAS/stale version | PASS | stale finance status and supply record versions fail closed |
| Idempotency | PASS | payment and supply command replay do not duplicate facts/transitions |
| Currency format | PASS | three-letter uppercase ISO-style code required |
| Monetary precision | PASS | more than two fractional digits rejected before persistence |
| Quantity precision | PASS | supply quantities use three fractional digits; unit price uses two |
| Low confidence/manual review | PASS | finance remains `required`; supply remains `needs_review` until a manager review |
| Immutable histories | PASS | Evidence and supply history update attempts are rejected |
| No AUTO finance/external action | PASS | no jobs created; supply external flag false; forecast advisory-only |
| No document body in jobs/audit | PASS | synthetic leak marker absent from job payloads and audit details |
| Explainable forecast | PASS | actual payment/correction, exact budget evidence, page and coordinates preserved |
| PostgreSQL concurrency | CONDITIONAL | no `TEST_POSTGRES_DSN` was available in this worktree |

## Commands and results

Red regressions, before fixes:

```powershell
python -m pytest -q tests/test_mvp4_financial_acceptance.py
# 3 failed: missing budget evidence; BudgetCreate accepted 1.001;
# PaymentConfirmation accepted 1.001

python -m pytest -q tests/test_mvp4_financial_acceptance.py -k stale_financial_status
# 1 failed: stale CAS left review_status=confirmed and record_version=2
```

Final targeted acceptance/regression run:

```powershell
python -m pytest -q `
  tests/test_mvp4_financial_acceptance.py `
  tests/test_mvp4_budget_dds.py `
  tests/test_mvp4_gpr_baseline.py `
  tests/test_mvp4_supply_acts.py `
  tests/test_mvp4_explainable_forecast.py `
  tests/test_contract_financial_evidence.py
```

Result: **74 passed**, no skipped tests in the selected acceptance set. Two Alembic deprecation warnings are unrelated to M4-10.

`git diff --check`: PASS.

## Remaining decisions and gaps

### OWNER

- Decide whether M4 supports currencies other than RUB. `CashFlowEntry` has no currency column, so safe multi-currency aggregation is not implementable without an additive schema/API change. Until decided, production use should be treated as single-currency per project/report.
- Decide whether approved supply acts may automatically propose (but never confirm) matching DDS/budget events. This acceptance deliberately creates no such financial side effect.
- Define the business rule for VAT, retention release, overpayment/partial payment and exchange-rate date.

### LEGAL / accounting

- Confirm that operator confirmation is sufficient evidence for the intended accounting/management-register purpose; it is not represented as a bank statement or statutory accounting posting.
- Approve retention, advance and acceptance terminology and mandatory act/invoice fields for supported contract types.
- Define retention periods and export requirements for immutable financial/supply histories.

### PostgreSQL / runtime

- Run the prepared suites against a clean PostgreSQL database and exercise simultaneous manager confirmations/updates from separate transactions.
- Verify row-lock/CAS behavior under two API processes and duplicate HTTP retries.
- Verify migration upgrade and backup/restore in the integration branch; this fork intentionally changed no migration or schema pin.

No production data, credentials, client documents or provider APIs were used. No push, merge or deployment was performed.
