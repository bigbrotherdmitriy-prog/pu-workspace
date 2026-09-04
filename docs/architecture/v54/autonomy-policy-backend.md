# v5.4 autonomy policy backend

Status: backend assignment, authorization-origin schema, and synthetic internal
Task AUTO path implemented; production AUTO remains intentionally unwired.

## Implemented boundary

`ActionPolicy` remains the single versioned policy store. Its existing database
`mode = CONFIRM` constraint is the conservative execution default. A sealed
`v54.autonomy-policy.1` rules document can add only one AUTO override:
`task.internal.create`, with an exact LOW / COMPENSATABLE / two-effect binding.
`message.external.send` is structurally fixed to CONFIRM. Advisory stages default
to ASSIST, known execution effects default to CONFIRM, and unknown capabilities
are denied.

Policy assignment, rotation, and revocation use an exact tenant/project/scope,
policy id/revision/hash CAS, and the live `AuthorityState.authority_epoch`. Only a
human project member whose live mandate role is `owner` and contains
`autonomy.policy.manage` can change policy. Global admin status, service
principals, model output, and payload fields do not provide elevation.

The decision DTO is bound to action type, stage, risk, reversal, ordered effects,
the sealed envelope SHA-256, payload SHA-256, policy revision/hash, and enabling
owner epoch. `recheck` repeats live policy and authority resolution. Policy
changes, revocation, owner role changes, epoch rotation, expiry, or altered action
and payload bindings fail closed.

Policy change and revoke events use the existing `AuditLog` + `AuditExtension`
append path. Audit details remain null and the policy rules contain IDs, modes,
timestamps, hashes, and epochs only—no email address, source text, task title, or
other payload content.

## HTTP surface

- `GET /api/v54/projects/{project_id}/autonomy-policy`
- `PUT /api/v54/projects/{project_id}/autonomy-policy`
- `POST /api/v54/projects/{project_id}/autonomy-policy/revoke`
- `POST /api/v54/projects/{project_id}/autonomy-policy/decide`

All responses are `no-store`. The authenticated actor and tenant are constructed
from server context and project ownership; neither is accepted from the body.

## Schema/runtime handoff implemented in A07

Migration `a54f001c0a07` makes authorization origin explicit and mutually
exclusive on `PendingDispatch` and `ActionReceipt`. Existing rows become
`HUMAN_APPROVAL`. The `SERVER_POLICY` branch has no `approval_id` and persists
the exact policy id/revision/hash, enabling authority epoch, decision hash,
sealed action hash, envelope hash, payload hash, decision document, and expiry.
Composite foreign keys bind the policy hash and immutable action revision;
checks and indexes enforce/query the two origins. No synthetic human approval is
created.

`ActionEnvelope.autonomy = AUTO` is valid only for
`task.internal.create` with the exact LOW / COMPENSATABLE / two-effect shape.
T1 seals the AUTO decision into `PendingDispatch`. T2 takes Project → live
requester/enabling-owner `AuthorityState` → exact current policy → action locks,
then rechecks policy, epoch, revocation, expiry, envelope and payload immediately
before the DB-only `Task` + `TaskHistory` mutation. The receipt copies that same
authorization binding in the transaction containing the mutation and audit.

This remains an explicitly injected synthetic runtime. No provider is invoked,
no production feature flag or config loader was added, and external message,
email, financial, legal, destructive, and access capabilities remain CONFIRM or
deny. Existing human CONFIRM dispatch is retained unchanged at the boundary.
