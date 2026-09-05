# MVP4 finance decision guards

Date: 2026-09-05

Branch: `codex/mvp4-finance-decision-guards`

Base: `f0d4135823ce9f4cc2d17d07b9d17709d547977f`

## Audit before changes

- `BudgetLine` stores a three-letter currency; `CashFlowEntry`, `ProcurementItem` and
  `AcceptanceAct` do not store currency and their current UI labels amounts as RUB.
- The finance overview previously summed approved budget rows without separating
  currencies.
- A cash-flow or invoice proposal could reference a non-RUB budget although the DDS
  row could not preserve that currency.
- A supply case preserved currency, but DDS proposal creation wrote to the
  currency-less cash-flow table and therefore could erase a non-RUB currency.
- Pydantic declared database precision, but the business contracts did not expose a
  single explicit money/quantity precision boundary.
- Payment confirmation was already a manager action and did not call a bank. There
  is no automatic accounting-posting provider in this scope.
- VAT and retention do not have an approved durable policy model. Selecting a rate,
  interpretation or formula in code would invent an owner/legal decision.

## Implemented fail-closed behavior

- The existing currency-less operational registers are explicitly treated as the
  current implicit-RUB ledger. This documents an implementation fact, not a new
  configurable accounting policy.
- Non-RUB budget rows may be retained only as proposals requiring review. They
  cannot be approved, activated or closed.
- A non-RUB budget cannot be linked to an implicit-RUB DDS/invoice proposal.
- A non-RUB supply case cannot create a DDS proposal until a durable multi-currency
  representation and approved policy exist.
- Overview totals exclude non-RUB budget rows instead of adding unlike currencies.
  The response exposes excluded rows and marks the totals unreliable when currency
  decisions remain unresolved.
- Finance and supply read models expose `decision_required`, responsible party and
  explicit `payment_created=false`, `posting_created=false` and
  `automatic_conversion=false` facts.
- Money accepts at most two decimal places; supply quantities accept at most three.
  Values are rejected rather than rounded silently.
- No migration, exchange-rate lookup, automatic payment or accounting entry was
  introduced.

## Decisions deliberately not made

### OWNER

1. Approve the currencies supported by each project and whether the operational
   ledger remains RUB-only.
2. Decide whether cross-currency links are forbidden or converted; if converted,
   approve conversion date, rate source, rounding and immutable evidence.
3. Decide whether project totals are displayed per currency or as an approved
   converted reporting currency.
4. Decide whether a non-RUB supply request may progress before the above decisions
   are recorded.

### LEGAL

1. Confirm when VAT applies, whether amounts are inclusive or exclusive, which
   source proves that treatment and how corrections are represented.
2. Confirm the legal/accounting meaning of retention, its basis and limits, and its
   effect on budget, DDS plan, payment fact, acceptance and release.

Until these decisions are implemented as versioned policy with evidence, the
application presents them as unresolved and does not calculate them.

## Synthetic regression coverage

- mixed RUB/EUR overview and excluded-currency totals;
- retained-but-blocked non-RUB budget proposal;
- approval guard and DDS-link guard;
- non-RUB supply-to-DDS guard;
- exact kopeck and quantity precision;
- explicit owner/legal notices and absence of automatic financial effects;
- fail-closed supply read model.

## Verification

- Targeted backend finance/supply suite: `57 passed`.
- Full backend pytest: `1413 passed, 24 skipped`; skips are pre-existing
  environment-dependent scenarios.
- Full frontend Vitest: `202 passed`.
- Frontend TypeScript check: PASS.
- Frontend production build: PASS; generated `react_dist` output was restored and is
  not part of the change.
- `git diff --check`: PASS.
- Full backend result is recorded in the final commit handoff.

All fixtures use synthetic names and `.invalid`/`.test` addresses. No provider,
production database, secrets or external effect was used.

## Remaining limitations

- General multi-currency DDS/procurement/act storage requires a separately reviewed
  schema and migration.
- No rate source, VAT engine, retention engine or accounting provider was selected.
- PostgreSQL runtime is not required for this validation-only change; existing
  PostgreSQL-only skips remain whatever the full suite reports.
