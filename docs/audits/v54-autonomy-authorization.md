# V5.4 A07 autonomy authorization integration

Scope: synthetic backend/schema acceptance only. Production activation,
provider I/O, external AUTO, deployment, push, and merge are out of scope.

## Boundary

- Schema head: `a54f001c0a07`, directly after `a54f001c0a06`.
- `HUMAN_APPROVAL`: non-null `approval_id`; all server-policy fields null.
- `SERVER_POLICY`: null `approval_id`; exact policy id/revision/hash, enabling
  authority epoch, decision/action/envelope/payload hashes, sealed decision and
  expiry are non-null.
- Both pending intent and immutable business receipt have direct sealed-action
  and exact policy composite foreign keys, database checks, and origin indexes.
- A07 backfills only the origin of existing rows. It inserts no policy, approval,
  task, credential, user, flag, or provider record.

## Runtime

The only AUTO-capable envelope is `task.internal.create`, LOW risk,
COMPENSATABLE, with effects `internal_task.create` and `task_history.append`.
`TrustFacade.request_dispatch` obtains/rechecks the server decision and writes T1
without constructing `ActionApproval`. T2 repeats the live authority and policy
checks under Project → AuthorityState → policy → action ordering immediately
before `InternalTaskMutation`. The mutation, history, decision-bound receipt,
and audits share the caller transaction.

Human CONFIRM continues to use the existing approval FK and approval epoch
checks. External message/email/finance/legal/destructive/access remain outside
the AUTO action DTO and are CONFIRM or deny in policy decisions.

## Evidence

- Synthetic scenario B creates exactly one internal Task and SERVER_POLICY
  receipt with zero human approvals.
- Negative T2 cases cover altered payload hash, authority epoch rotation, policy
  revoke, and expiry; all fail before Task or receipt creation.
- Offline PostgreSQL SQL asserts the origin checks, composite FKs, and indexes.
- Conditional localhost/disposable PostgreSQL coverage verifies A07 upgrade,
  downgrade to A06, and re-upgrade. Absence of the explicit test URL is reported
  as `CONDITIONAL`, not a runtime pass.
- Existing CONFIRM Trust/queue integration remains in the regression suite.

No live provider, production flag, push, merge, or deployment is part of this
change.
