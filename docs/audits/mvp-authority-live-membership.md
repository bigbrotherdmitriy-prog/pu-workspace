# DB authority: live membership and pending security changes

Base: `2848bba170025fbc84d3d3d7bff35d1e19e13739`. Offline regression fix only.

## Reproduced defects

1. A real SQLAlchemy Session retained `ProjectMember(role=owner)` with `expire_on_commit=False`. Another Session committed `role=viewer`. The authority reader fetched the row without refreshing its identity-map state and incorrectly authorized against the cached owner role. A scalar database query confirmed viewer. Both `lock=True` and `lock=False` paths reproduced this.
2. Pending membership/mandate/project revocations could be implicitly flushed by the authority read, or erased by `populate_existing` when the caller used `no_autoflush`. With a pending membership deletion, the unflushed old row could still authorize. Authorization must neither silently save security changes nor replace them with old persisted state.

## Minimal fix and transaction contract

`AuthorityResolver.require_principal` rejects pending new/deleted/actually-modified `Project`, `AuthorityState`, `ProjectMember` or `User` rows before any query. The caller must explicitly flush or roll back security-row changes first. This conservative session-level precondition does not manufacture a grant and does not decide whether a pending edit should be persisted. Authorization reads execute under `no_autoflush`; unrelated pending business rows are preserved and not implicitly saved.

The membership query now uses `populate_existing=True`, like the existing project and mandate queries. Persisted membership changes therefore override clean but stale identity-map objects. The existing Project → AuthorityState lock order, lock flags, permission set, mandate/role checks, expiry rules and no-admin-bypass policy remain unchanged. This does not claim a new PostgreSQL locking protocol for legacy writers.

One existing synthetic product acceptance fixture deliberately adds a permission and immediately assigns an autonomy policy. Its transaction owner now explicitly calls `db.flush()` after the validated mandate/epoch/version update. No expected denial was removed or relaxed.

The separate D14 local-source helper still owns its own pending Source/Evidence/Materialization lineage protections. This change does not edit that helper, staging, queues, schemas or role policies.

## Validation

- RED before the fix: **18 failed, 2 passed**. The two-session stale-role checks failed to deny; pending-change cases detected either bypass, unwanted SQL or lost ORM state.
- New regression coverage includes both lock modes, with/without caller `no_autoflush`, pending role/mandate/archive/delete edits, preserved ORM state with zero SQL on denial, clean explicit-mandate positive controls, unrelated pending business rows and explicit caller flush.
- Targeted files: `test_v54_authority_live_membership.py`, `test_v54_authority.py`, `test_v54_pilot_integration.py`, `test_v54_autonomy_policy.py`, `test_v54_autonomy_authorization.py`, `test_v54_product_acceptance.py`.
- Final combined result: **74 passed, 1 skipped** in 134.15 seconds, including all **22** new regressions/controls. The existing optional PostgreSQL gate was skipped; no PostgreSQL PASS is claimed. Two existing Alembic `path_separator` deprecation warnings remain.
- No provider requests, live data, deployment, schema changes or PostgreSQL execution. SQLite checks establish identity-map and transaction behavior, not PostgreSQL lock/concurrency guarantees.
