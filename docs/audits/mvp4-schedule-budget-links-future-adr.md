# MVP-4: future ADR for schedule-to-budget allocation

Status: deferred by owner decision on 2026-09-21.

## Current decision

The canonical finance chain uses the existing direct references from an invoice or cash-flow item to one contract, one schedule item, and one budget line. The server validates that all three records belong to the same project and contract. This is sufficient for the current acceptance path and does not introduce a new allocation model.

## Deferred question

Independent allocation of budget across GPR stages is not part of the current implementation. A future ADR must decide whether to introduce a many-to-many `schedule_budget_links` model, including allocation amounts or percentages, overlap rules, revision behavior, and the effect on committed and actual projections.

Until that ADR is approved:

- no automatic distribution of a budget line across schedule items is performed;
- no schedule-to-budget allocation table is created;
- selecting the contract, schedule item, and budget line records traceable context for a financial operation, but does not claim that the entire budget line is allocated to that schedule item;
- existing proposed cash-flow records are linked to controls only by an explicit manager action.

## Trigger for revisiting

Revisit this ADR when the product must show or enforce stage-level budget allocation, compare GPR progress with allocated cost, or split one budget line across multiple schedule items.
