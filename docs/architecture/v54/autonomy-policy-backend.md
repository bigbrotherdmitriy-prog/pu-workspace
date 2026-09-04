# v5.4 autonomy policy backend

Status: backend assignment and decision boundary implemented; production AUTO
execution remains intentionally unwired.

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

## Schema handoff before AUTO execution

No migration was added in this change. Actual AUTO task execution cannot safely
reuse the current CONFIRM executor because:

1. `ActionEnvelope.autonomy` currently accepts only CONFIRM.
2. `PendingDispatch.approval_id` is mandatory and foreign-keyed to a human
   `ActionApproval`.
3. `ActionReceipt.approval_id` is mandatory and has the same human-approval
   binding.
4. `TrustFacade` validates only the inactive synthetic CONFIRM policy shape.

The integration/schema owner should add an explicit authorization-origin binding
(`HUMAN_APPROVAL` or `SERVER_POLICY`) to pending dispatch and receipt, and for the
server-policy branch persist the exact policy id/revision/hash, enabling authority
epoch, decision hash, and sealed action/envelope hash. Constraints must require
exactly one origin and must never create a synthetic human approval. T2 must
recheck that binding under the existing Project → AuthorityState → policy →
action lock order before the DB-only task mutation and append the decision and
receipt in the same transaction.

Until that migration and Trust integration exist, an AUTO decision is an
authorization assessment only: this backend does not enqueue, invoke providers,
or mutate a Task. No production feature flag was enabled.
