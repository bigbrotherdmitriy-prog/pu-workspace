# Future ADR: contract budget direction and cross-contract aggregation

Status: backlog; deliberately outside MVP-4 Decision 2.

The current project finance summary aggregates confirmed budget lines across contracts without distinguishing whether the linked contract represents project income or project expense. Contract kinds already imply a direction (`customer` / `revenue_subcontract` as inflow, `downstream_subcontract` / `supply` as outflow), but `BudgetLine` does not persist an explicit direction and the summary does not separate the two populations.

Before changing this behavior, the owner must decide whether direction is derived from the current contract kind or pinned on each financial record, how contract-kind changes affect existing records, and how project totals present income, expense and margin. Decision 2 intentionally creates or revises only the contract's canonical budget line and does not alter aggregation semantics.
