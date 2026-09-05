# MVP1 storage mutation runtime

## Result

The existing IDs-only durable-job seam is now backed by an opt-in, synthetic-only
runtime. No HTTP route or UI installs it, and live Google/Yandex adapters are
rejected unless an injected test adapter carries the explicit
`synthetic_storage_adapter = True` marker.

## Safety contract

- Existing `BackgroundJob` kind `workspace.storage_mutation` is reused; no queue was added.
- Job payload remains IDs-only and is revalidated by the existing handler.
- The DB resolver runs before the attempt and again after the durable crash fence.
- `execute_mutation` then repeats binding, CAS, source revision and provider state
  checks immediately before the first effect.
- Attempt and receipt rows are append-only `OrganizerOperation` records with
  bounded hashed idempotency keys.
- A crash after the attempt never blindly repeats the effect. Replay reconciles
  exact provider state: all target state becomes `applied`; every other state is
  an immutable `unknown` receipt and requires human/operator reconciliation.
- Same command key plus different command digest fails closed.
- Receipt payload contains exact structural pins and operation metadata, never
  document bytes, OAuth credentials, provider tokens, DSN or extracted text.
- Rollback is explicit and derives from an earlier immutable applied receipt.

## Acceptance evidence

- Targeted storage-mutation suite: **19 passed, 2 PostgreSQL-skipped**.
- Unit regressions cover default deny, live-adapter rejection, exact before/after
  reconciliation, partial-state UNKNOWN and IDs-only job payload.
- The DB-backed test proves durable attempt + receipt persistence and confirms an
  identical replay causes no second provider effect.
- An obsolete synthetic fixture omitted the now-required organization scope; it
  was repaired without changing product behavior.
- Python compilation and diff checks are required before commit.
- PostgreSQL crash/concurrency execution is conditional on `TEST_POSTGRES_DSN`;
  the prepared marker is not counted as PASS when no database is available.

## Remaining gates

- **PostgreSQL:** concurrent attempt insertion, worker crash between effect and
  receipt, lease recovery and database restart need a real PostgreSQL runtime.
- **Live provider:** Google/Yandex mutation adapters, OAuth latency and provider
  reconciliation remain disabled and require separate acceptance.
- **Product activation:** API/UI composition, RBAC authorization at enqueue and
  operator workflow for `unknown` are intentionally absent.
- **OWNER:** decide whether live provider mutation may ever be enabled and define
  operator ownership/SLA for UNKNOWN receipts.
- **LEGAL:** approve retention and visibility of structural file/folder metadata
  stored in immutable receipts.
