# V5.4 email compensation: a08 schema handoff

This branch intentionally creates no migration because `a07` is being developed
in parallel. The MVP read/propose flow therefore uses only the existing `a06`
provider-action and protected `response_drafts` records and fails closed unless
the original send has one unambiguous revision and the established content-free
command binding `response-draft:{draft_id}:send`.

## Required a08 persistence

The next schema owner should replace that transitional command binding with
explicit scoped relationships:

1. Add `relation_action_revision` and `relation_outcome_observation_id` to
   `v54_provider_actions`.
2. Add composite, tenant-scoped `RESTRICT` foreign keys from a related action to
   the exact source `(organization_id, action_id, revision)` and append-only
   outcome observation.
3. Add a protected link from `response_drafts` (or a dedicated scoped link
   table) to the exact provider action revision. Do not place recipient, subject,
   body, provider raw ID, or other content in the Action Ledger.
4. Constrain `CORRECTIVE` relations to a different action ID, an `APPLIED`
   irreversible send, the same organization/project/mailbox, and exact evidence,
   context, authority, capability, and credential pins.
5. Preserve one-way immutability: a corrective outcome must never update or
   relabel the source send observation.

The current service includes the exact source revision and observation in the
sealed payload hash and a PII-free audit event. The explicit a08 columns are
still required for relational verification, multi-revision source actions, and
queryable provenance without relying on command-key conventions.
